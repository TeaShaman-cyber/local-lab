---
name: local-core-router
description: Use when work involves local runtime state, Git/GitHub route selection, repository currentness, 403 failures, or verified persistence in the local-lab runtime.
---

# Local Core Router

Тонкий операционный guardrail. Не заменяет `AGENTS.md` и не дублирует его.

## Fast path

1. Определи домен авторитета и границу рантайма до выбора инструмента.
2. Выбери тончайший маршрут, сохраняющий свежесть, разрешение, верификацию и нужную долговечность.
3. Загружай только минимально нужный раздел операционного руководства.
4. `capability != permission`.
5. Кэш и видимое состояние — свидетельство, не текущий авторитет.
6. Успех — только после наблюдаемого postcondition.

## Локальный рантайм

- Хост: Windows 11; `terminal` работает через bash (MSYS). Нативные программы (git, rg, node, python) получают пути вида `C:/...`, а не `/c/...`.
- Репозиторий: `E:\local-lab` — канон. Remote: `origin` = GitHub.
- `session_search` — встроенный инструмент Hermes (FTS5 по сессиям Hermes).
- Google Drive — через Pipedream (`mcp__pipedream__google_drive_*`).
- Shell, файлы, git и сеть — локально.
- Секреты в репозиторий не попадают; ключи живут в окружении.

## Currentness

- Перед выводами о свежести: `git fetch`, затем сравнение с `origin/main`.
- Локальный checkout, ветка или кэшированный ref не являются авторитетом свежести только потому, что существуют.
- Не сбрасывать и не переключать грязный или активный worktree ради чистого вида. Читать авторитетный ref напрямую либо использовать изолированный чистый worktree.

## GitHub routing

- Ограниченное чтение текущего состояния: нативный `gh` CLI.
- Мутация: `gh` CLI с текущим разрешением, затем точный remote readback.
- `visibility != existence`: если профиль не видит объект, это `UNKNOWN` до подтверждения с авторитетной поверхности.
- Успешное чтение не даёт права на запись.

## 403 rule

403 — сигнал маршрутизации, не диагноз.

- Если отказ случился до исполнения цели, не утверждать, что команда выполнилась или упала.
- Сначала проверить postcondition цели.
- Многострочный или кавычко-чувствительный payload нормализовать до повтора.
- Не повторять тот же падающий payload без изменений.
- Если граница не выяснена — `DEGRADED` или `UNKNOWN`.

## Verification and persistence

Держать раздельно:

```text
local working tree -> local branch -> tracking ref -> live remote ref -> GitHub object
```

- Успешный ответ исполнителя недостаточен, когда доступна независимая проверка.
- Persist только по требованию задачи, канонической записи или непрерывности.
- Важные записи требуют readback.
- Если нужное свидетельство не установлено — `BLOCKED`, `DEGRADED`, `UNKNOWN` или `REPROBE_REQUIRED` вместо выдуманного успеха.

## PR gate

- `gate-review` в `.github/workflows/gate.yml` не пропускает merge, пока не появится внешнее ревью **точного head** PR. Принимаются четыре формы: review-объект Codex, receipt «Reviewed commit: <sha>», 👍-реакция после даты head, либо `APPROVED` review человека.
- **Ручной апрув — штатный fallback, а не обход.** Codex-бот может быть недоступен; тогда владелец репозитория ставит `APPROVED` на тот же head, и гейт пропускает. Причину указать в PR (бот недоступен, ревью человеком).
- После каждого push head меняется: проверить `codex-review-state` (`has_unresolved_findings`) и при необходимости попросить `@codex review`.
- Гейт ждёт 10 минут, затем падает с `P1 change without outside review`; проверки при этом пройдены. Если ревью пришло позже — `gh run rerun <run-id>`, не новый коммит.
- Мерж — по точному head: `gh pr merge --match-head-commit <sha>`; ветку не удалять, если проектом не решено иначе.
- Комментарий бота с findings — не шум: P1/P2 закрывать правкой или явным ответом, «gate pass» этого не заменяет.

## Skills

- Источник истины — `.agents/skills/<name>/SKILL.md` в репозитории. `$HERMES_HOME/skills/local-lab/` — только проекция.
- Порядок правки: ветка → PR → merge → `scripts/sync-skills.sh` (readback; отказывает при пустом источнике или пустом `HERMES_HOME`).
- Не создавать параллельный `.hermes/skills/`. `skill_manage` пишет в профиль (installed) — для проектных скиллов этот путь не использовать.
- Внешние скилпаки (matt-pocock, grafana, langfuse, timeweb) не патчить в installed: свежесть — `python tools/dev/skillpacks` (state в `E:/skillpacks-state.json`, mtime после checkout врёт), отличия — в `docs/agents/deviations.md`.
- Скилл вендора не копировать: сверить, что он уже есть у вендора, и держать указатель, а не устаревающую копию.

## Scope

Скилл содержит только повторяющиеся инварианты маршрутизации. Подробные процедуры, редкие сбои и восстановление остаются в `docs/` и в cookbook; при нехватке деталей загружать наименьший нужный раздел и останавливаться.
