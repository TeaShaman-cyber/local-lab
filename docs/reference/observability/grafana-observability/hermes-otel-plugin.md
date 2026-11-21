# Плагин hermes_otel (Hermes OTel)

Экспорт телеметрии Hermes в Grafana Cloud (Tempo + Mimir + Loki): трейсы,
метрики, логи. Плагин живёт в `$HERMES_HOME/plugins/hermes_otel/`, конфиг —
`$HERMES_HOME/hermes_otel.yaml`.

## Текущее состояние

```text
версия     1.33.1
источник   https://github.com/briancaffey/hermes-otel @ e23f1d21d8c4d6898ec2bc295fd4fc63d02378f8
патч       upstream PR #672 + локальный terminal LiveStore follow-up (#281)
хуки       34
```

Состояние выше относится к live gateway после controlled restart 2026-11-22.
Upstream PR #672 уже слит; follow-up из `#281` пока является локальным overlay
и не должен считаться частью upstream `1.33.1`.

Проверенный признак живого маршрута — строка в `hermes gateway status`:

```text
[hermes-otel] ✓ grafana-tempo at https://otlp-gateway-prod-<region>.grafana.net/otlp/v1/traces
[hermes-otel] ✓ content_capture=full — complete prompts and responses on every api.* span
[hermes-otel] ✓ Logs → 1 backend(s) (attached to root, level=INFO)
[hermes-otel] Registered 34 hooks
```

`Registered 15 hooks` означает старую 1.18.2 — плагин не обновился или gateway
не перезапускался.

## Захват содержимого

Полный capture включён намеренно: телеметрия строится для аудита работы агента,
а не для счётчиков. Значения в `hermes_otel.yaml`:

```yaml
content_capture: "full"            # промпты и ответы целиком
capture_sender_id: true
capture_conversation_history: true
capture_logs: true                 # плюс logs: true у otlp-backend
capture_tool_command: true         # аргументы инструментов
capture_tool_target: true          # пути и цели
capture_error_message: true        # тексты ошибок
```

В спанах это `input.value` / `output.value`, `hermes.tool.command`,
`hermes.tool.target`, `error.message`. Если видно только `*_chars`, capture
выключен — это регресс, а не «экономия».

Требование к backend: `logs: true` в записи `backends:` — иначе плагин пишет
`logs.capture is on but no configured backend accepts OTLP logs`.

## Локальный патч

Причина: `hermes plugins doctor` и hot reload на Windows падали с
`PermissionError: [WinError 32]` на `hermes_otel_live.db` — процесс-глобальный
SQLite LiveStore не закрывался при выгрузке плагина.

Базовая lifecycle-правка уже вошла в
https://github.com/briancaffey/hermes-otel/pull/672:

- `hermes_otel/__init__.py` — `getattr(ctx, "on_unload", None)` + регистрация
  `tracer.shutdown`;
- `hermes_otel/live_store_registry.py` — `release_live_store()`, который убирает
  store из `_LIVE_STORE` и `_STORES_BY_PATH` и затем закрывает его (иначе после
  reload остаётся закрытый singleton);
- `hermes_otel/pipeline.py` — shutdown вызывает `release_live_store`.

Текущий follow-up в `local-lab#281` не переписывает PR #672 и добавляет более
узкую terminal-hardening правку:

- `hermes_otel/live_store.py` — terminal close guard, сериализация `flush()` и
  `close()`, запрет повторного открытия SQLite после terminal close;
- `hermes_otel/pipeline.py` — release store после остановки providers/logger и
  снятия log handler;
- unit/integration regression tests для repeated shutdown и concurrent
  flush/close.

Следовательно, live сейчас содержит upstream `1.33.1` плюс этот overlay.
Проверка базовой lifecycle-правки — `grep -c release_live_store
hermes_otel/live_store_registry.py` даёт `1`, а `grep on_unload
hermes_otel/__init__.py` возвращает регистрацию unload callback.

## Обновление

`hermes plugins update hermes_otel` для этого плагина не работает: каталог видит
его как untracked/pinned и файлы не меняет. Рабочий порядок:

```text
1. backup:  плагин + hermes_otel.yaml  (scratch/hermes-otel-live-backup-<date>)
2. hermes plugins install briancaffey/hermes-otel/hermes_otel \
       --force --ref <40-char-sha> --yes-deps --enable
3. проверить, что `plugin.yaml` действительно сменил version на `1.33.1`;
   если нет — скопировать дерево из worktree нужного SHA вручную
4. убедиться, что установлен upstream base `e23f1d21...`;
5. наложить только актуальный локальный overlay из `local-lab#281`;
6. hermes pm repair           # зависимости (1.33 требует exporter-otlp-proto-grpc)
7. hermes plugins doctor --ci hermes_otel
8. hermes gateway restart     # из ОТДЕЛЬНОГО shell: изнутри gateway запрещено
9. readback: hermes gateway status -> version/hooks/content_capture/logs
10. живой trace в Tempo: `resource.service.version=1.33.1`, наличие
    `input.value`/`output.value` и отсутствие старых 15 hooks
```

Правку provenance делать явно: `.hermes-catalog.json` в каталоге плагина и
`plugins/.install-metadata.json` (`revision`, `catalog.sha`) должны указывать на
тот же SHA, иначе следующий `check-updates` покажет ложный дрейф.

После текущего ручного rollout это правило выявило реальный drift: live
`plugin.yaml` уже `1.33.1`, а `.hermes-catalog.json` и
`plugins/.install-metadata.json` всё ещё содержат catalog SHA
`32554cf76c8dbeb8d382eaff9007112abf3f7b2c` от `1.33.0`. До следующего
managed install/update metadata нужно безопасно reconcile-нуть; не считать
этот drift исправленным только потому, что код и telemetry уже работают.

## Откат

```text
scratch/hermes-otel-live-backup-<date>/
  hermes-otel-plugin-<version>.zip # распаковать в $HERMES_HOME/plugins/
  hermes_otel.yaml                # вернуть конфиг
  SHA256SUMS
```

Для текущего rollout проверенный backup находится в
`scratch/hermes-otel-live-backup-20261122-000327/`. После отката —
`hermes gateway restart` из внешнего shell и readback версии/hooks/traces;
ожидать `Registered 15 hooks` можно только при откате на 1.18.2.

## Известный остаток

Исходный `WinError 32` был связан с unload cleanup и закрыт PR #672. Для
terminal LiveStore path добавлен follow-up `local-lab#281`; после его live
rollout текущий `hermes plugins doctor --ci hermes_otel` завершился `rc=0`, а
bounded Loki window после restart не содержит совпадений по `WinError 32` или
`hermes_otel_live.db`.

Это подтверждает текущий rollout, но не является upstream CI и не доказывает
отсутствие всех будущих race-сценариев. До upstream handoff остаются exact-head
independent review, fork PR, hosted CI readback и reconcile metadata drift.

Проверять текущий runtime так:

```text
hermes plugins doctor --ci hermes_otel   # ожидается rc=0
hermes gateway status                    # 34 hooks, Logs -> 1 backend(s)
```

## Проверка в Grafana

```text
# трейсы хода
{ resource.service.name = "hermes-agent" && resource.service.version = "1.33.1" }
# содержимое
{ name =~ "api\\..*" } -> input.value / output.value присутствуют
```

Версия в `resource.service.version` — самый быстрый способ отличить старый
процесс от нового после restart.
