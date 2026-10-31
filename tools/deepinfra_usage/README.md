# DeepInfra billing → Grafana Loki

Пуш фактических биллинговых цифр DeepInfra в Loki, который уже есть в стеке
(Alloy → Grafana Cloud). Нужен потому, что Langfuse считает эту модель по
неверному прайсу, а панель DeepInfra иначе доступна только глазами.

Tracking issue: [local-lab #81](https://github.com/TeaShaman-cyber/local-lab/issues/81).
Исторический контекст — `theseus-local-lab` #58 (historical, in the archive).

## Источник данных

Ключ инференса DeepInfra даёт доступ и к биллингу — документированные эндпоинты:

```text
GET https://api.deepinfra.com/payment/checklist?compute_owed=true
    stripe_balance  минус = предоплаченные средства, плюс = долг
    suspended, recent, limit, overdue_invoices
GET https://api.deepinfra.com/payment/usage?from=current
    months[].items[]: model.model_name, pricing_type, units, rate, cost
```

Семантика единиц: `cost` — **в центах**, `rate` — центы за токен.
`pricing_type` бывает `input_tokens`, `cached_tokens`, `output_tokens`;
кэш-чтение у DeepInfra в 33 раза дешевле входа, поэтому его нельзя игнорировать.
`payment/usage?from=current` возвращает накопительный billing-интервал месяца
(`months[].interval.fr/to`, Unix milliseconds), а не суточную дельту. Сравнивать
его с Loki можно только после проверки полного покрытия этого интервала логами.

## Как запускается

Windows Task Scheduler, задача `Theseus DeepInfra billing`, каждые 15 минут,
системный Python (`%LOCALAPPDATA%\Programs\Python\Python313\python.exe`).

Сознательно **не** через Hermes cron: мониторинг не должен зависеть от
мониторируемого процесса. Alloy остаётся транспортом для остальных метрик, но
он не умеет выполнять скрипты, поэтому fetch JSON-API живёт здесь.

Проверить:

```powershell
Get-ScheduledTaskInfo -TaskName 'Theseus DeepInfra billing'
```

## Что уходит в Loki

Стрим `service_name="deepinfra", log_type="billing"`, строки в logfmt:

```text
metric=balance_usd value=5.0000
metric=owed_usd value=0.0000
metric=spend_recent_usd value=0.0800
metric=spend_limit_usd value=...
metric=spend_month_usd value=0.0800
metric=suspended value=0
metric=overdue_invoices value=0
metric=cost_cents value=5.0000 pricing_type=input_tokens model=deepseek-ai/DeepSeek-V4.1-Flash-1017
metric=units value=489318 pricing_type=input_tokens model=...
```

LogQL-панели читают их так:

```logql
last_over_time({service_name="deepinfra", log_type="billing"} | logfmt | metric="balance_usd" | unwrap value [1h])
```

Дашборд: `https://tealaphid71.grafana.net/d/hermes-providers` (панели 9–15).

## Креды

Скрипт не хранит секретов. Ключ DeepInfra читается из `%LOCALAPPDATA%\hermes\.env`
(`DEEPINFRA_API_KEY`), креды Loki — из `config.alloy` (единый источник правды,
тот же, что использует Alloy). В stdout секреты не попадают.

## Проверка

```bash
python tools/deepinfra_usage/push_deepinfra_usage.py
# loki=204 lines=12 balance_usd=5.0000 spend_month_usd=0.0800
```

## Алерты

Группа `deepinfra-billing`:

- `DeepInfra: отказы по балансу (HTTP 402)` (critical) — срабатывает только когда
  запрос действительно ушёл и был отклонён; фильтры литеральные и привязаны к
  провайдеру, поэтому чужой 402 сюда не попадёт;
- `DeepInfra: аккаунт заблокирован (suspended)` (critical) — **на паузе**:
  `suspended` — известное постоянное состояние, а не событие, письма раз в 4 часа
  про него были шумом. Включать обратно после пополнения баланса.

## Окно сверки: почему суммы не сравниваются напрямую

- Период billing: `2026-10-01 07:00` → `2026-11-01 06:59` UTC (31 день), поля
  `usage_period_start_unix` / `usage_period_end_unix`.
- Телеметрия Loki начинается `2026-10-26 00:37` UTC — **5.7 дня из 31**.
- Оставшиеся 25.3 дня периода не имеют логов: это `UNKNOWN`, а не ноль.
  Сравнение `spend_month_usd` с суммой по логам за сутки даёт ложное расхождение.

`vector(time())` в этом Loki не поддерживается (`parse error`), поэтому текущее
время внутри запроса недоступно и доля покрытия остаётся расчётом, а не метрикой.

## Дашборд как код (gcx)

Дашборды ведутся файлами в `infra/grafana/dashboards/` и применяются официальным
`gcx` (grafana/gcx), а не руками в UI. Токен `GRAFANA_API_TOKEN` (`glc_`) для этого
не годится — это access policy для логов и метрик; нужен OAuth-логин или
service-account token.

```bash
gcx login tealaphid71 --server https://tealaphid71.grafana.net --oauth
gcx resources pull     dashboards/hermes-providers -p ./infra/grafana/dashboards
gcx resources validate -p ./infra/grafana/dashboards
gcx resources push     -p ./infra/grafana/dashboards --dry-run
gcx resources push     -p ./infra/grafana/dashboards
gcx dashboards snapshot hermes-providers --panel 14 --output-dir ./.snap
```

Две вещи, на которых легко потерять время:

- в agent-режиме gcx браузер не открывает: печатает URL и код, который надо сверить
  глазами, и ждёт подтверждения;
- официальный `install.ps1` на этой машине падает на `Get-FileHash`. Рабочий путь —
  бинарь из релиза (`gcx_*_windows_amd64.zip`), сверенный по `gcx_<ver>_checksums.txt`
  через `sha256sum`.
