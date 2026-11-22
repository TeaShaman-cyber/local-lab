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

Текущий follow-up в `local-lab#281` (локальный candidate commit `5c5aef7`) не
переписывает PR #672 и добавляет более узкую terminal-hardening правку:

- `hermes_otel/live_store.py` — terminal close guard, сериализация `flush()` и
  `close()`, запрет повторного открытия SQLite после terminal close; строка,
  принятая во время `close()`, сохраняется для terminal flush, а отказ после
  `_closed` считается в `dropped_after_close` вместо молчаливой потери;
- `hermes_otel/live_store_registry.py` — store остаётся зарегистрированным до
  доказанного успешного close, чтобы ошибка terminal flush сохраняла владельца
  и допускала повторную попытку;
- `hermes_otel/pipeline.py` — release store после остановки providers/logger и
  снятия log handler;
- unit/integration regression tests для repeated shutdown и concurrent
  flush/close.

### Что дополнительно поймало усиление

Исходная проблема была уже не только в `WinError 32` при unload. Проверка класса,
а не одного случая, обнаружила ещё несколько независимых границ:

- `close()` мог пересечься с обычным `flush()` и снова открыть SQLite после
  terminal close;
- store удалялся из registry до доказанного успешного close, поэтому retry
  терял владельца и pending rows;
- pipeline мог освободить store раньше providers/logger, которые ещё способны
  записать событие;
- timed-out writer мог потерять сильную ссылку до фактического завершения;
- первая версия terminal guard молча выбрасывала строку, принятую во время
  `close()`. Это расхождение сначала показала Lean-модель, затем воспроизвёл
  Python-тест на базе `e23f1d2` (`1` строка) и кандидате до фикса (`0` строк).

Гейт после исправления: focused LiveStore tests `48 passed`; mutation suite
`M1..M9` — `9/9 killed`, `0 survived`, `0 harness errors`; SHA-256 проверенного
diff — `b5727820c1729d82f76db3f3b683baaf49ef10001cff4d377e1fc1eb4535e395`.
Lean negative control теперь называется `preFixAdd`: run
`45002881933`, `LEAN_REJECTED/PASS`, probe SHA-256
`c80c4cd86906f81c6535e7ab2a4d3563168abd793c5203d505319d2cff94f6b9`.

Следовательно, live сейчас содержит upstream `1.33.1` плюс этот overlay.
Проверка базовой lifecycle-правки — `grep -c release_live_store
hermes_otel/live_store_registry.py` даёт `1`, а `grep on_unload
hermes_otel/__init__.py` возвращает регистрацию unload callback.

## Upstream CI runner

Локальный Docker-runner для upstream `make ci` является постоянной частью
инфраструктуры `INFRA-024`, а не одноразовым экспериментом. Его назначение —
проверять exact candidate SHA на upstream base до live-install и upstream handoff.
Сам контейнер каждого прогона одноразовый; image и dependency caches сохраняются
для следующей задачи по этому плагину. Канонические имена, digest, volumes и
команда находятся в `infra/registry.yaml` под `INFRA-024.location.ci_gate`.

В runner нельзя переносить host-specific probe environment в полный CI. Перед
`make ci` удаляется `/.dockerenv`, а `HERMES_OTEL_QUERY_HOST` не задаётся. Иначе
adapter/dashboard tests переписывают адреса backend’ов и создают ложные массовые
падения, не связанные с candidate.

Для текущего hardening candidate `2f87283` Docker `make ci` остаётся отдельным
обязательным gate. Live-install/restart допустим только после `PASS` именно для
этого SHA; предыдущая live-приёмка `5c5aef7` на новый head не переносится.

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

После первого ручного rollout это правило выявило реальный drift: live
`plugin.yaml` был `1.33.1`, а metadata всё ещё указывала на `1.33.0`. Во время
приёмки candidate `5c5aef7` `.hermes-catalog.json` и
`plugins/.install-metadata.json` согласованы с upstream base `e23f1d21...`;
локальный overlay остаётся отдельно названным и не выдаётся за upstream SHA.

## Откат

```text
scratch/hermes-otel-live-backup-<date>/
  hermes-otel-plugin-<version>.zip # распаковать в $HERMES_HOME/plugins/
  hermes_otel.yaml                # вернуть конфиг
  SHA256SUMS
```

Для текущего rollout проверенный backup находится в
`scratch/hermes-otel-live-backup-20261122-104445/`. После отката —
`hermes gateway restart` из внешнего shell и readback версии/hooks/traces;
ожидать `Registered 15 hooks` можно только при откате на 1.18.2.

## Известный остаток

Исходный `WinError 32` был связан с unload cleanup и закрыт PR #672. Для
terminal LiveStore path добавлен follow-up `local-lab#281`. После установки
candidate `5c5aef7` и controlled restart gateway сменил PID `18880 → 14804`;
три live production-файла byte-for-byte совпали с кандидатом, а
`hermes plugins doctor --ci hermes_otel` завершился `rc=0` с 34 hooks.
Tempo trace `d16c2673f5b5ff002a55637a5b7125eb` содержит свежие спаны PID `14804`,
service version `1.33.1`, включая `input.value` и `output.value`. Bounded Loki window
`2026-11-22T08:40:00Z..08:50:00Z` не содержит совпадений по `WinError 32` или
`hermes_otel_live.db`.

Это подтверждает текущий rollout, но не является upstream CI и не доказывает
отсутствие всех будущих race-сценариев. До upstream handoff остаются exact-head
independent review, fork PR и hosted CI readback.

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
