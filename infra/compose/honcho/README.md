# Honcho: локальные эмбеддинги

## Зачем

Honcho по умолчанию берёт `text-embedding-3-small` у внешнего провайдера: векторы считаются вне машины, а память зависит от чужого прайса и доступности. Для Hindsight мы этот класс уже проходили — там стоит `HINDSIGHT_API_EMBEDDINGS_PROVIDER=local` с мультиязычной моделью.

Здесь то же самое, но отдельным сервисом: у Honcho нет встроенного `local`, он умеет только OpenAI-совместимый endpoint.

## Состав

| Файл | Что |
|---|---|
| `docker-compose.override.yml` | TEI (text-embeddings-inference), CPU, порт `127.0.0.1:8080`; кладётся в каталог профиля и мержится с `docker-compose.yml` |
| модель | `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`, **384** измерения |

Модель та же, что у Hindsight, — чтобы банки были сопоставимы и не пришлось объяснять расхождение метрик.

## Запуск

```bash
cp infra/compose/honcho/docker-compose.override.yml \
   "$HOME/.honcho/profiles/<profile>/docker-compose.override.yml"
cd "$HOME/.honcho/profiles/<profile>" && docker compose up -d
```

```bash
docker compose config --services        # api, database, deriver, embeddings, redis
curl -s localhost:8080/health
curl -s localhost:8080/embed -H 'Content-Type: application/json' \
     -d '{"inputs":"тест мультиязычности"}' | python -c "import sys,json;print(len(json.load(sys.stdin)[0]))"
```

Первый старт скачивает ~470 МБ весов в volume `tei-data`.

Отдельный `docker compose -f embeddings.compose.yml up -d` больше не нужен: сервис входит
в общий стек, и один `up -d` поднимает все пять сервисов. Прежний отдельный файл давал
`Found orphan containers (local-test-embeddings-1) for this project` при каждом запуске
основного стека.

## Подключение к Honcho

`config.toml` профиля:

```toml
[embedding]
VECTOR_DIMENSIONS = 384

[embedding.model_config]
transport = "openai"
model = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"

[embedding.model_config.overrides]
base_url = "http://host.docker.internal:8080/v1"
```

`host.docker.internal`, а не `127.0.0.1`: Honcho работает в контейнере.

## Грабля: `.env` перебивает `config.toml` (RULE-018)

Порядок приоритета у Honcho: **переменные окружения → `config.toml` → дефолт**. Ключ
`EMBEDDING_MODEL_CONFIG__OVERRIDES__BASE_URL` в `.env` сильнее, чем
`[embedding.model_config.overrides] base_url` в `config.toml`.

Мы на это наступили: поправили `config.toml` на `:8080`, а запросы продолжали уходить
на старый `:8645`. Ошибка выглядела как проблема модели:

```
400 Model sentence-transformers/paraphrase-multilingual-minilm-l12-v2 does not exist
```

Хотя модель была, а неверным был маршрут.

Второй слой: `docker restart` **не перечитывает** `.env`. Нужно пересоздание:

```bash
docker exec local-test-api-1 env | grep EMBEDDING      # что реально загружено
docker compose up -d api deriver                       # пересоздать, не restart
docker exec local-test-api-1 env | grep EMBEDDING      # подтвердить :8080
```

Правило зафиксировано как `RULE-018` в `docs/reference/infrastructure.md`.

**Миграция размерности.** `documents.embedding` и `message_embeddings.embedding` объявлены как `vector(1536)`. При переходе на 384 нужно пересоздать колонки и пересчитать векторы; пока банк маленький — дешевле очистить и дать deriver пересобрать, чем писать backfill.

## Dream: модель, провайдер, окно записи

Dream наследует общий OpenAI-совместимый endpoint, если не задан собственный. Собственный
задаётся **только через `overrides`** — пример в `dream.env.example`:

```bash
DREAM__DEDUCTION_MODEL_CONFIG__MODEL=mistral-small-3.2-24b-instruct
DREAM__DEDUCTION_MODEL_CONFIG__OVERRIDES__BASE_URL=https://openai.bothub.chat/v1
DREAM__DEDUCTION_MODEL_CONFIG__OVERRIDES__API_KEY=<из окружения>
```

**Грабля: верхнеуровневые `__BASE_URL` и `__API_KEY` не работают.** В
`ConfiguredModelSettings` эти поля живут внутри `ModelOverrideSettings`:

```text
ConfiguredModelSettings: model, transport, ..., overrides: { api_key, api_key_env, base_url }
```

Ключи вида `DREAM__DEDUCTION_MODEL_CONFIG__BASE_URL` принимаются молча и остаются
неприменёнными. Симптом ровно такой: `model` подхватился, а `base_url=None`, ключ
пустой — запрос уходит на общий endpoint. Если общий endpoint прокси с fair-share,
это выглядит как `429 reached this model's current fair-share rate limit`, то есть как
проблема модели, хотя проблема в имени переменной.

Проверка effective-конфигурации:

```bash
docker exec local-test-deriver-1 python -c "
from src.config import settings
d = settings.DREAM
for n in ('DEDUCTION_MODEL_CONFIG', 'INDUCTION_MODEL_CONFIG'):
    m = getattr(d, n); o = m.overrides
    print(n, m.model, o.base_url, bool(o.api_key))
"
```

Секреты не печатать: достаточно `bool(o.api_key)`.

`DREAM__WRITE_WINDOW_ITERATIONS` задаёт, сколько итераций dream может потратить на
запись. Увеличение окна не помогает модели, которая вообще не вызывает write-инструменты:
проверять надо по `dream_runs.created_count` и по наличию вызовов
`create_observations_deductive` / `create_observations_inductive`, а не по тексту лога.

## Peer card: формат задаёт код, а не модель

Карточка принимает только четыре префикса, и это валидатор, а не рекомендация промпта:

```text
IDENTITY:      каноническое имя, вид, алиасы, ID
ATTRIBUTE:     устойчивое свойство, включая явно заявленные предпочтения
RELATIONSHIP:  устойчивая связь с другой сущностью
INSTRUCTION:   явно заявленное правило взаимодействия
```

Запись длиннее 200 символов отклоняется. `TRAIT:` и поведенческие `PREFERENCE:`
отклоняются структурно (`PEER_CARD_ALLOWED_PREFIXES` в `src/utils/agent_tools.py`).

Отсюда неочевидное следствие: когда dream обновляет карточку, он **обязан** выбросить
legacy-записи в формате `PREFERENCE:` / `TRAIT:` — он не «теряет» их по небрежности,
а исполняет контракт. Предпочтения и правила надо сразу оформлять как `ATTRIBUTE:`
(«ATTRIBUTE: Prefers tea» — допустимая форма) или `INSTRUCTION:`.

Содержание можно направлять без правки кода — через `configuration` workspace или peer:

```bash
PUT /v3/workspaces/{workspace_id}
{"configuration": {"peer_card": {"custom_instructions": "..."}}}
```

`peer_card.custom_instructions` добавляются поверх dream-инструкций и действуют внутри
разрешённых типов; `peer_card.create=false` отключает автоперезапись карточки dream'ом.
Бюджет задаётся `DERIVER.MAX_CUSTOM_INSTRUCTIONS_TOKENS` (по умолчанию 2000).

## Проверка

Признак того, что всё локально: в ответе на embed нет заголовков `x-railway-request-id` / `X-Nous-Token-Sharing` (они появлялись, когда запрос уходил через Nous).

## Ограничения

- TEI `cpu-1.8` использует ONNX-сборку; при первом старте качает `onnx/model.onnx`.
- Модель 384 измерения уступает `text-embedding-3-small` на длинных текстах; для памяти о разговоре этого достаточно, для поиска по коду — проверять отдельно.
- Порт `8080` локальный (`127.0.0.1`), наружу не публикуется.
