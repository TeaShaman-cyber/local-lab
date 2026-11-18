# Плагин hermes_otel (Hermes OTel)

Экспорт телеметрии Hermes в Grafana Cloud (Tempo + Mimir + Loki): трейсы,
метрики, логи. Плагин живёт в `$HERMES_HOME/plugins/hermes_otel/`, конфиг —
`$HERMES_HOME/hermes_otel.yaml`.

## Текущее состояние

```text
версия     1.33.0
источник   https://github.com/briancaffey/hermes-otel @ 32554cf76c8dbeb8d382eaff9007112abf3f7b2c
патч       локальный, release_live_store + on_unload (см. ниже)
хуки       34
```

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

Патч (он же PR https://github.com/briancaffey/hermes-otel/pull/672):

- `hermes_otel/__init__.py` — `getattr(ctx, "on_unload", None)` + регистрация
  `tracer.shutdown`;
- `hermes_otel/live_store_registry.py` — `release_live_store()`, который убирает
  store из `_LIVE_STORE` и `_STORES_BY_PATH` и затем закрывает его (иначе после
  reload остаётся закрытый singleton);
- `hermes_otel/pipeline.py` — shutdown вызывает `release_live_store`.

Пока PR не влит, патч нужно накладывать заново после каждого `plugins install/update`:
проверка — `grep -c release_live_store hermes_otel/live_store_registry.py`
должен давать `1`, а `grep on_unload hermes_otel/__init__.py` — непустой вывод.

## Обновление

`hermes plugins update hermes_otel` для этого плагина не работает: каталог видит
его как untracked/pinned и файлы не меняет. Рабочий порядок:

```text
1. backup:  плагин + hermes_otel.yaml  (scratch/hermes-otel-live-backup-<date>)
2. hermes plugins install briancaffey/hermes-otel/hermes_otel \
       --force --ref <40-char-sha> --yes-deps --enable
3. проверить, что plugin.yaml действительно сменил version;
   если нет — скопировать дерево из worktree нужного SHA вручную
4. наложить локальный патч
5. hermes pm repair           # зависимости (1.33 требует exporter-otlp-proto-grpc)
6. hermes gateway restart     # из ОТДЕЛЬНОГО shell: изнутри gateway запрещено
7. readback: hermes gateway status -> version/hooks/content_capture/logs
8. живой trace в Tempo: наличие input.value и отсутствие старых 15 hooks
```

Правку provenance делать явно: `.hermes-catalog.json` в каталоге плагина и
`plugins/.install-metadata.json` (`revision`, `catalog.sha`) должны указывать на
тот же SHA, иначе следующий `check-updates` покажет ложный дрейф.

## Откат

```text
scratch/hermes-otel-live-backup-<date>/
  hermes-otel-plugin-1.18.2.zip   # распаковать в $HERMES_HOME/plugins/
  hermes_otel.yaml                # вернуть конфиг
  SHA256SUMS
```

После отката — `hermes gateway restart` из внешнего shell и проверка, что снова
`Registered 15 hooks` и traces идут.

## Известный остаток

`hermes plugins doctor --ci hermes_otel` после обновления всё ещё завершается
`WinError 32`: похоже на второй LiveStore в logs-пайплайне. На live-экспорт не
влияет; закрывается в PR #672 (issue #671). Проверять так:

```text
hermes plugins doctor --ci hermes_otel   # ожидается ERROR WinError 32
hermes gateway status                    # при этом трейсы и логи идут
```

## Проверка в Grafana

```text
# трейсы хода
{ resource.service.name = "hermes-agent" && resource.service.version = "1.33.0" }
# содержимое
{ name =~ "api\\..*" } -> input.value / output.value присутствуют
```

Версия в `resource.service.version` — самый быстрый способ отличить старый
процесс от нового после restart.
