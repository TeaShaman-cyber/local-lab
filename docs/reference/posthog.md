# PostHog: инфраструктурный контур и проверка drift

Единственный источник адресов, идентификаторов, route names и текущей paused posture —
`INFRA-023` в [`infra/registry.yaml`](../../infra/registry.yaml). Этот документ описывает
процедуру; он не повторяет значения реестра.

Владельцы решений:

- `local-lab#234` — mutation evidence, Git-owned Scout source и классификация;
- `theseus-cloud-shell#16` — provider credential/runtime boundary, Scout dispatch и HTTP 429;
- GitHub Actions — авторитет точного PR head и результатов CI;
- PostHog warehouse — производная история, а не merge authority.

## Три независимых runtime route

| Route | Для чего | Граница |
|---|---|---|
| Hermes hosted connector | типизированные read-only analytics tools | OAuth/scopes коннектора не переносятся в другие runtime |
| Hermes direct API/CLI | fallback и операции, отсутствующие в MCP | personal key читается только из окружения; значение не печатается |
| exe.dev native integration | provider-managed REST внутри связанной VM | credential хранит provider; attachment не даёт Hermes тот же токен |

Успех одного route не доказывает доступность другого. Не переносить ключ между ними и
не объяснять exe.dev HTTP 429 отсутствующими scopes hosted connector без фактического
server-side reason.

## Git-owned Scout source

`docs/posthog/mutant-scout/` владеет инструкцией и calibration cases. Деплой в PostHog —
проекция. `INFRA-023.artifacts` хранит SHA-256 Git-файлов, а
`python tools/contracts/check_infra.py` пересчитывает digest. Текстовые artifacts явно
объявляют `text-lf`: перед хешированием checker приводит CRLF и lone CR к LF, поэтому
Windows checkout не выглядит source drift. Это ловит забытое обновление registry при
изменении source, но не доказывает, что live projection синхронизирована.

Live projection проверяется отдельно:

1. Прочитать `INFRA-023` и взять project/config/skill facts.
2. Выполнить `posthog-cli api info` для каждого используемого tool до `api call`.
3. Через read-only `skill-get` получить полное body; проверить `body_total_length`, версию,
   длину и SHA-256 против Git source и `INFRA-023.artifacts`.
4. Через `scout-config-list`, `scout-metadata-get` и `scout-runs-list` подтвердить paused,
   non-emitting posture и отсутствие созданного run.
5. Любое различие — `DRIFT`; не исправлять live config автоматически.

## Warehouse freshness

Перед использованием CI history:

1. `external-data-sources-list` — оба source из `INFRA-023` должны быть включены,
   завершены без `latest_error` и иметь подходящую свежесть.
2. Сначала читать schema таблицы, затем выполнять bounded SQL по нужному repo/head/window.
3. Точный head и результаты проверки сверять с GitHub Actions.
4. Mutation verdicts не выводить из обычных workflow tables: полные specs/diffs находятся
   в GitHub mutation artifacts.
5. В PR писать receipt формата `POSTHOG_CI_HISTORY`, заданного в
   `.agents/skills/local-core-router/SKILL.md`.

`Completed` у source не гарантирует, что самый новый head уже импортирован. Нулевая выборка
по head означает `STALE` или `NO_SIGNAL`, но не успех и не отказ CI.

## Paused Scout и HTTP 429

Scout остаётся выключенным и non-emitting. Наличие ненулевого default interval не является
активным расписанием при `enabled=false`; cron отсутствует. Не создавать дубликат и не
менять schedule.

Один ранее разрешённый manual dispatch был отклонён HTTP 429 до создания run. Видимые quota
счётчики не объяснили отказ, а error body первого запроса не был сохранён. Поэтому причина
остаётся `UNKNOWN`, artifact access внутри sandbox — `UNKNOWN_NOT_EXECUTED`.

До следующего POST:

1. получить documented/UI diagnostic reason или sanitized error category;
2. записать `status`, `detail/code` и `Retry-After`, не сохраняя Authorization/header/token;
3. проверить границы расхода;
4. только по отдельному разрешению выполнить максимум один non-emitting run;
5. требовать чтения конкретного authoritative shard artifact и rehash stable mutation ID;
6. receipt runtime/dispatch писать в Cloud Shell #16, mutation conclusion — в Local Lab #234.

## Drift matrix

| Сравнение | Проверка | Что означает mismatch |
|---|---|---|
| Registry ↔ Git files | `check_infra.py` artifact SHA-256 | Git source изменён без обновления receipt |
| Git body ↔ PostHog skill | `skill-get` + length/SHA-256 | deployed projection устарела или усечена |
| Registry ↔ Scout config | config/metadata/runs GET | live posture изменилась |
| Registry ↔ warehouse | source list + schema + freshness query | source выключен, сломан или отстаёт |
| Registry ↔ exe.dev | provider integration/attachment readback | credential route перемещён или расширен |
| Registry ↔ Honcho runtime | Docker/GitHub readback по `INFRA-022` | draft source ошибочно принят за deployment |

## Проверка после правки

```bash
python tools/contracts/check_infra.py
python tools/contracts/check_infra.py --self-test
bash tools/dev/check
```

Затем нужен live readback по read-only API/CLI. Канонический QA доказывает только структуру
и Git artifact digests; он намеренно не делает сетевые запросы и не запускает Scout.
