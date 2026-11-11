# Honcho: установка, профиль и эффективная конфигурация

Справочник по локальному Honcho на ноутбуке. Собран из истории сессий, живого
профиля и контейнеров 2026-11-08; всё, что ниже, проверялось командой, а не памятью.
Upstream-документация не копируется: она меняется, а здесь — наша конфигурация и её
ловушки.

## Где что лежит

```text
C:\Users\s.poklad\.honcho\profiles\local-test\
  config.toml                 копия из образа, honcho start её не перезаписывает
  .env                        секреты и переопределения; ПЕРЕБИВАЕТ config.toml
  .env.bak-*                  следы правок (localemb, mistral)
  docker-compose.yml          сгенерирован honcho start
  docker-compose.override.yml наш: TEI в общем стеке
```

Профиль поднят штатной командой `honcho start` (профиль `local-test`), образ
зафиксирован дайджестом — он записан первой строкой `config.toml`:
`ghcr.io/plastic-labs/honcho@sha256:eb906a2d251aa48b22347e4465b40785ea7a84e2bfd1105780416bf1f0121099`.

## Стек

| Контейнер | Роль | Проверка |
|---|---|---|
| `local-test-api-1` | FastAPI, порт 8000 | `curl /health` → `{"status":"ok"}` |
| `local-test-deriver-1` | извлечение наблюдений из сообщений | `deriver_metrics_last_success_timestamp_seconds` |
| `local-test-database-1` | Postgres + pgvector, БД `postgres` | `psql -U postgres -d postgres` |
| `local-test-redis-1` | очередь | healthy |
| `local-test-embeddings-1` | TEI, порт 8080 | `/health` → 200, тело пустое (норма) |
| `local-test-dedup-1` | ночная уборка повторов | `event=run_finished status=ok` |

Имя базы — `postgres`, не `honcho`. Это стоило одного ложного диагноза: `psql -d honcho`
отвечает `database "honcho" does not exist`.

## Модели

Разделены на два независимых контура, и это главное, что надо помнить.

**Быстрый контур (deriver, summary, dialectic)** — два маршрута:

```text
deriver:  https://integrate.api.nvidia.com/v1   (ключ NIM_KEY, только в .env)
          nvidia/nemotron-3-ultra-550b-a55b     primary
   ↓ при трёх отказах подряд
          http://host.docker.internal:8645/v1   локальный прокси Nous
          upstage/solar-mini4:free              fallback
```

Почему напрямую в NVIDIA, а не через BotHub: та же модель, но на одном окне
12.9 с против 33.1 с, и без промежуточного посредника. Лимит, который отдаёт
сам сервис — `x-ratelimit-limit: 90` запросов с окном около минуты, для разбора
с парой вызовов в час это не ограничение.

## Выбор модели: каталог не равен обслуживаемому набору

`GET /v1/models` перечисляет и то, что ключ не обслуживает, и то, что не держит
JSON-схему. Проверено на BotHub и NVIDIA NIM 2026-11-08:

| Наблюдение | Пример |
|---|---|
| модель есть в каталоге, вызов → `404` | `nvidia/nemotron-3-super-120b-a12b`, `nvidia/nemotron-nano-3-30b-a3b` на ключе NIM |
| модель отвечает, но `response_format` с `json_schema` даёт пустое тело | `nemotron-3.5-lightning-30b-a3b` (NIM), `reflection/beam-1.0-501b` (прокси) |
| модель держит схему и слушает прозу | `nemotron-3-ultra-550b-a55b`, `upstage/solar-mini4:free` (но solar пересказывает разговор) |

Отсюда правило проверки: проба на коротком окне **не воспроизводит прод**.
Beam прошёл короткий стенд и развалился на промпте реального размера
(30 сообщений, 10.4 тыс. символов ≈ 2.6 тыс. токенов) — это и есть штатный
размер пачки deriver'а.

**Dream (deduction + induction)** идёт в BotHub, отдельными ключами:


```text
mistral-small-3.2-24b-instruct   primary
gemini-2.5-flash                 fallback
https://openai.bothub.chat/v1
```

**Embeddings** — локальный TEI, без внешнего провайдера:

```text
sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2
VECTOR_DIMENSIONS = 384
base_url = http://host.docker.internal:8080/v1
```

## Ловушка: env сильнее config.toml, и вложенность важна

`config.toml` — не источник истины. Источник — эффективные переменные **внутри**
контейнера:

```bash
docker exec local-test-api-1 env | grep -iE 'model|dream|embed|base_url' | grep -vi key
```

Имена переменных повторяют структуру TOML двойным подчёркиванием, а `base_url` и
`api_key` живут **внутри** `overrides`, не на верхнем уровне:

```text
DREAM__DEDUCTION_MODEL_CONFIG__MODEL=mistral-small-3.2-24b-instruct
DREAM__DEDUCTION_MODEL_CONFIG__OVERRIDES__BASE_URL=https://openai.bothub.chat/v1
DREAM__DEDUCTION_MODEL_CONFIG__OVERRIDES__API_KEY=...        # только в .env
DREAM__DEDUCTION_MODEL_CONFIG__FALLBACK__MODEL=gemini-2.5-flash
```

`DREAM__MODEL_CONFIG__...` (без `DEDUCTION`/`INDUCTION`) молча игнорируется —
правка выглядит применённой и не действует.

`docker compose restart` **не перечитывает** `.env`: нужен `docker compose up -d`.

## Настройки workspace: в базе, а не в файлах

Третий слой настроек, помимо `config.toml` и `.env`. Лежит в БД записи workspace,
задаётся и читается только через API (в v3.6.0 путей `/configuration` нет):

```bash
# задать — заменяет configuration целиком, {} очищает
curl -X PUT http://127.0.0.1:8000/v3/workspaces/hermes \
  -H 'content-type: application/json' \
  -d '{"configuration":{"reasoning":{"custom_instructions":"..."}}}'

# прочитать обратно
curl -X POST http://127.0.0.1:8000/v3/workspaces/list \
  -H 'content-type: application/json' -d '{}' \
  | jq '.items[]|select(.id=="hermes").configuration'
```

Уровни: `workspace` → `session` → `message`; отдельно `PeerConfig` с `observe_me` /
`observe_others`. Поле `reasoning.custom_instructions` бюджетно ограничено:
`DERIVER.MAX_CUSTOM_INSTRUCTIONS_TOKENS` (у нас `2000`), превышение — ошибка
валидации, а не тихое усечение.

Что задано сейчас (2026-11-08, основание — local-lab#214): в workspace `hermes`
`reasoning.custom_instructions` с правилом отбора наблюдений — писать только
устойчивое (факты о пире, решения с причиной, инварианты, повторяющиеся отказы),
не писать пересказ диалога, команды, пути, хеши, порты, логи, статусы CI и
промежуточные состояния.

Ловушка: этого текста нет ни в `config.toml`, ни в `.env`; `docker compose up -d`
его не сбрасывает, потому что он не в контейнере, а в базе. Искать только через
API, иначе настройка выглядит отсутствующей (RULE-019).

## Уборка повторов (dedup)

- Linux-контейнер с `supercronic`, расписание `15 4 * * *`, `Europe/Kaliningrad`.
- Режим `exact` — безопасный дефолт; `near` выключен, потому что ошибочное слияние
  необратимо.
- Удаление только через Honcho API, не SQL: так сохраняются soft-delete и семантика
  реконсиляции.
- Каждый прогон пишет `event=run_finished` в logfmt; успех — `ok` (что-то удалил) или
  `clean` (удалять было нечего). `failed`, `partial`, `guard_tripped` метку успеха не
  двигают намеренно.

## Наблюдаемость

```text
Alloy  --scrape job="honcho-api"-->  Grafana Cloud Prometheus
Alloy  --log-derived logfmt----->    honcho_dedup_* (runs_total, live_documents,
                                     deleted_total, last_success_timestamp_seconds)
```

Проверенные запросы (2026-11-08):

```promql
up{job="honcho-api"}                                  # 1
deriver_queue_items_pending                           # 3
deriver_queue_oldest_pending_age_seconds              # ~600
time() - deriver_metrics_last_success_timestamp_seconds  # ~52
dreams_due                                            # 0
honcho_dedup_runs_total{status="ok"}                  # 56
honcho_dedup_live_documents                           # 1971 на момент прогона 04:15
```

`live_documents` — снимок момента прогона, поэтому он отстаёт от `count(*) from documents`
(2161): это не расхождение, а разное время съёмки.

## Текущее состояние банка

```bash
docker exec local-test-database-1 psql -U postgres -d postgres -tAc \
  "select 'peers='||(select count(*) from peers)||' sessions='||(select count(*) from sessions)||' messages='||(select count(*) from messages)||' documents='||(select count(*) from documents)||' dreams='||(select count(*) from dream_runs);"
# peers=6 sessions=4 messages=223 documents=2161 dreams=17
```

Таблицы `conclusions` в схеме нет — этот факт проверен, а не предположен.

## Связь с Hermes

`memory.provider: honcho` в `%LOCALAPPDATA%/hermes/config.yaml`. Память Hermes читает
этот же API, поэтому падение Honcho не роняет чат, но выключает recall — и до issue #209
об этом никто бы не узнал.

## Hermes autorecall: зафиксированная компоновка

На стороне Hermes в
`C:/Users/s.poklad/AppData/Local/hermes/honcho.json` действует отдельный лимит и
фильтр автоматической вставки; это не настройки сервера Honcho:

```json
{
  "contextTokens": 2048,
  "injection": {
    "sessionStart": ["summary", "peerCard"]
  }
}
```

`contextTokens=2048` был перенесён при установке по аналогии с Hindsight; это не
upstream-дефолт Honcho (там отсутствие значения означает uncapped). Лимит оставлен как
предохранитель от разрастания Representation.

Почему включён фильтр: при прежней компоновке линейное обрезание стабильно заполняло
весь бюджет (около 8.2 KB) секциями Session Summary и началом User Representation.
Поздние Peer Card и Dialectic не доходили. С фильтром вставка занимает около 1.6 KB
для Summary и до 2.2 KB, когда готов 601-символьный Dialectic; Representation не
вставляется.

`peerCard` оставлен в allowlist, но в текущем plugin фактически не приходит. При
`ai.observeOthers=true` автоматический путь читает карточку как `svarog → semyon` и
получает пустой результат, тогда как self-card `semyon → semyon` содержит 18 фактов.
Явный accessor делает fallback на self-card, prefetch-путь — нет. Это не исправляется
узкой настройкой: отключение `ai.observeOthers` меняет семантику наблюдений, а patch
plugin создаёт локальный fork.

### В контекст пишут две разные поверхности

Фильтр `injection.sessionStart` действует только на сборку plugin. В тот же
`~/.honcho/injection.log` пишет и memory-provider Hermes, и это разные payload:

| Форма | Размер | Признак |
|---|---|---|
| A — сборка plugin | 1.9–2.5 KB | начинается с `## Session Summary` |
| B — memory-provider | 0.6 KB | проза «Based on …», без `## Session Summary` |
| пусто | 0 B | `reason=fetched-but-empty` |

Форма B обёрнута как `<memory-context>` и приходит к модели как
`[System note: The following is recalled memory context…]`; её формирует
`agent/memory_context.py` в Hermes. Поэтому «Representation не вставляется» верно
только для формы A: через memory-provider пересказ профиля продолжает приходить,
около 600 байт на ход, иногда пустым. Суммарный объём при этом остаётся около 3 KB
против прежних 8.2 KB.

Читая лог, различать формы по заголовку `## Session Summary`, а не по размеру: размер
меняется вместе с содержимым summary.

Решение по local-lab#214: оставить менее шумную компоновку, recall-nudge hook держать
выключенным, plugin не патчить и собственный memory engine не начинать. Устойчивые
факты доступны через USER.md и явные Honcho tools. Возвращаться к теме только при
наблюдаемой регрессии или после upstream-изменения `plastic-labs/honcho#1281` /
`#1295`. Откат фильтра: удалить корневой блок `injection` из `honcho.json` и
перезапустить gateway/Desktop.

## MCP-сервер: форк и границы

Официальный Honcho MCP (`plastic-labs/honcho`, каталог `mcp/`) умеет искать по
памяти и отдавать структурированные записи, но ограничивает только **число**
записей (`message_limit`), а `content` отдаёт целиком. Живая проба это показала:
при `message_limit=3` одно сообщение пришло на **15738** символов — это была
приложенная статья. Для автоматической доставки в контекст такой ответ
непригоден, а штатной настройки размера у MCP нет (`LLM_MAX_TOOL_OUTPUT_CHARS`
и `LLM_MAX_MESSAGE_CONTENT_CHARS` относятся к внутренним dialectic-tools, не к
форматтеру MCP).

Поэтому форк: <https://github.com/TeaShaman-cyber/honcho>, checkout
`E:/honcho-upstream` (`fork-main` отслеживает `fork/main`), база `3326373c35f6`.
Первый принятый merge `dad6848` ограничил отдельное сообщение; `a4c3680` добавил
opt-in бюджет всего search JSON. Действующий сервер — merge `d04090e` (PR #11),
переход на stateless MCP `2026-07-28`. Ограничения живут в MCP форматтере, а не в
вызывающей стороне: так один и тот же контракт получает любой harness, а хук
остаётся тонким.

Два слоя контракта:

- `MCP_MAX_MESSAGE_CONTENT_CHARS` — предел на сообщение, default `2000`,
  допустимо `1..100000` Unicode code points; невалидное значение откатывается
  к дефолту;
- многоточие входит в лимит, surrogate pair не разрывается;
- каждая запись несёт `content_chars` и `content_truncated`;
- настройка проведена во всех трёх хостах: Worker, HTTP, stdio;
- optional `max_result_json_bytes=512..65536` ограничивает UTF-8 размер всего
  `content[0].text` search JSON. Без параметра legacy-ответ не меняется;
- bounded envelope `honcho_search_bounded_v1` явно сообщает
  `retrieved/returned/omitted` отдельно для messages и conclusions,
  `total_matches=null` и `total_matches_status=UNKNOWN_UPSTREAM`; возвращённые
  conclusions сохраняют `source_ids`, `source_message_ids`, `observer_id`,
  `observed_id` и `session_id`;
- enclosing JSON-RPC frame в этот бюджет не входит.

Интеграция с Hermes hook остаётся опциональной: переменная
HONCHO_RECALL_MAX_JSON_BYTES (512..65536) задаёт размер JSON для запроса
MCP search. Без неё запрос не меняется. При явно заданном лимите hook
требует формат honcho_search_bounded_v1, иначе отказывается от recall
без блокировки turn, чтобы старый сервер не смог молча обойти ограничение.
В контекст переносятся source_ids, source_message_ids, уровень, наблюдатели,
а также coverage с числом полученных, возвращённых и опущенных записей.
Если все найденные записи не поместились, coverage сообщает об этом
отдельно от истинно пустой выдачи.

Hook не подрезает содержимое повторно. Размер итоговой вставки в Hermes
не равен max_result_json_bytes, потому что к ответу добавляются преамбула,
поисковый запрос и JSON-обёртка. Это изменение исходников и тестов,
не изменение живых переменных окружения Hermes.

Наблюдаемая проверка на живом стенде (лимит по умолчанию 2000):

```text
returned_chars [365, 2000, 317]   original_chars [365, 15738, 317]
truncated      [false, true, false]
```

Тот же per-message результат приходит через Hermes при вызове
`mcp__honcho_mcp_local__search`.

Живая opt-in проверка merge `a4c3680` через настоящий MCP-клиент (`mcporter`) на
изолированном candidate, затем на endpoint `127.0.0.1:3000`:

```text
legacy:       8463 compact UTF-8 bytes, старые keys messages/conclusions
bounded 512:   512 bytes, 1/5 messages, 0/10 conclusions, omissions accounted
bounded 2000: 2000 bytes, 2/5 messages, 2/10 conclusions, provenance retained
invalid 511:  rejected (exit 1)
```

Артефакт приходит из пайплайна, а не из локальной сборки. Workflow
`.github/workflows/docker-build-mcp.yml` собирает `mcp/Dockerfile` только на push
в `main` по пути `mcp/**` и публикует в
`ghcr.io/teashaman-cyber/honcho-mcp` **единственный** тег `sha-<полный SHA>`;
шаг `Verify only the source-commit tag is published` валит сборку, если появится
`latest` или плавающий алиас.

```text
merge:  d04090ec7fe9d191d94c616fe6341f4402b8ce38
tag:    ghcr.io/teashaman-cyber/honcho-mcp:sha-d04090ec7fe9d191d94c616fe6341f4402b8ce38
index:  sha256:544c5d46b83d90b36306716a99fc64e47be62d9045483a37a1c88545e9889b55  (OCI index, multi-arch)
amd64:  manifest sha256:78b5317a…  config sha256:74880f82…
run:    TeaShaman-cyber/honcho actions run 42888178408 (success)
```

Три digest — три разных объекта. `docker inspect .Id` на этом хосте (containerd
image store) равен digest индекса, а не config digest, поэтому в registry нет поля
`image_id`: конфигурация и манифест записаны отдельными ключами, чтобы проверка не
сравнивала несравнимое.

Контейнер `honcho-mcp-probe` запускается **по digest**, поэтому работающий образ
неподвижен и проверяем. Остановленные `honcho-mcp-rollback-f67c4d6` (локальная
сборка, временное отклонение) и `honcho-mcp-rollback-a4c3680` (legacy wire)
оставлены для отката. Корневой
`ghcr.io/teashaman-cyber/honcho:latest@sha256:7161a0a4…` — это другой пакет:
Python/FastAPI, порт 8000; MCP-образ берётся только из `honcho-mcp`.

Протокол `2026-07-28` — stateless: `initialize` и `Mcp-Session-Id` больше нет.
Клиент обязан прислать `MCP-Protocol-Version`, `Mcp-Method` и (для `tools/call`)
`Mcp-Name`, а в теле — `params._meta` с
`io.modelcontextprotocol/protocolVersion` и `.../clientCapabilities`; расхождение
заголовка и тела даёт `400/-32020`. Ответ приходит как `text/event-stream`, поэтому
разбирать нужно кадры, а не тело целиком. Legacy-клиент получает молчаливый отказ —
это решение (TeaShaman-cyber/honcho#10), а не регрессия.

Приёмка на живом порту `127.0.0.1:3000` 2026-11-10:

```text
hook (local-lab PR #247): 3 messages + 3 conclusions, 3583 bytes
hook bounded 2000:        honcho_search_bounded_v1, 2+2, coverage с вытеснением
native Hermes (mcp 2.3.0): initialize ok, 40 tools, search 2534 chars
```

Schema MCP tools кэшируется на старте сессии, поэтому уже открытая сессия может
показывать каталог старого сервера; нужен reload каталога или новая сессия. Это
клиентская staleness, серверный контракт отдельно доказан живым вызовом.

### Ловушки

- MCP-шлюз требует **непустой** `Authorization: Bearer …`; без него `tools/call`
  отдаёт `401`, и fail-open клиент молча ничего не вставляет. Локальный API
  значение не проверяет, удалённый — проверяет.
- Числовые env (`HONCHO_RECALL_MIN_QUERY`, `HONCHO_RECALL_TIMEOUT`) разбираются
  через `isdecimal()`, а не `isdigit()`: `isdigit()` пропускает `²`, на котором
  `int()` падает. Невалидное значение откатывается к default и больше не
  отключает recall целиком.
- Fail-open — это пустой stdout и код 0, но не молчание: причина уходит строкой
  `honcho-recall: failed open: <Type>: <detail>` в stderr, с редакцией токена и
  `Bearer`-заголовка. Харнесс эту строку не читает, человек — читает.
- `workspace_id` надёжнее передавать аргументом `tools/call`, а не только
  заголовком `X-Honcho-Workspace-ID`: заголовок задаёт лишь дефолт.
- `mutmut` на нативном Windows не работает («please use the WSL»); мутационные
  прогоны идут в контейнере `python:3.12-slim` с примонтированным репозиторием.

### QA

Локально собирается **только** QA-таргет — для проверок перед push. Production-образ
локально не собирается: его публикует пайплайн, и деплой берёт опубликованный digest.

```bash
docker build --target qa -t local/honcho-mcp-qa:dev E:/honcho-upstream/mcp
# production: push in mcp/** to main -> ghcr.io/teashaman-cyber/honcho-mcp:sha-<commit>
# deploy:     docker run <digest> (тег только для чтения, контейнер — по digest)
```

QA-таргет прогоняет `typecheck`, полный Bun-набор и Biome; то же самое делает
`.github/workflows/mcp-qa.yml`. В наборе есть строгие JSON-проверки (parse и
каноническая сериализация, кавычки, переводы строк, обратные слеши, Unicode),
границы лимита и невалидные числа, согласованность `content_truncated` с
размерами, а также то, что хук действительно отправляет `Authorization` и
`workspace_id`. Тесты, которые «просто не падают», здесь не считаются проверкой:
подпроцессный прогон хука уже один раз показал зелёный тест при живом дефекте.

### Границы этапа

Размер — собственность MCP: одна запись ограничена `MCP_MAX_MESSAGE_CONTENT_CHARS`
и несёт `content_truncated`; opt-in `max_result_json_bytes` ограничивает весь
search JSON и явно учитывает пропущенные records. Число upstream records — параметры
запроса (`message_limit`, `conclusion_top_k`), а не постобработка на клиенте. Хук
не режет и не выбрасывает записи: иначе ограничение расползлось бы по каждому
harness, а контракт перестал бы быть переносимым.

Не входит и намеренно не сделано: включение whole-response budget по умолчанию,
ограничение enclosing JSON-RPC frame или других search-like tools, включение hook'а
в Hermes, изменения Honcho plugin и MemoryProvider. Recall пока вызывается вручную;
автоматическая доставка — отдельный шаг.

## История решений

| Тема | Где зафиксировано |
|---|---|
| Компоновка Hermes autorecall и решение не патчить plugin | issue #214 |
| Dream-модели и вложенность overrides | PR #186 |
| BotHub fallback и доказанный failover | PR #188 |
| Dedup в контейнере по расписанию | PR #189 |
| Logfmt-события прогонов | PR #190 |
| Правила на dedup и skillpacks | PR #203, `infra/grafana/alertrules/` |
| Доступность API и backlog deriver | issue #209 |
| MCP-поверхность, per-message и whole-search бюджеты | local-lab#231, TeaShaman-cyber/honcho#1/#4/#5 |
