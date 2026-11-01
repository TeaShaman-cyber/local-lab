# Мониторинг лимитов подписки ChatGPT/Codex — research

Дата: 2026-11-01. Issue: #28. Вопрос: как получить **остаток** лимита подписки (5-часовое и недельное окна), а не расход токенов, и не изобретать велосипед.

## 1. Официальные способы (первоисточники)

### 1.1 Codex App Server — документированный программный API

`account/rateLimits/read` (JSON-RPC) возвращает:

```text
usedPercent, windowDurationMins, resetsAt, rateLimitReachedType
rateLimitResetCredits { availableCount, credits[{ id, resetType, status, grantedAt, expiresAt }] }
```

Плюс `account/rateLimits/updated` (notify), `account/usage/read` (token-activity), `account/rateLimitResetCredit/consume`.

- Документация: <https://learn.chatgpt.com/docs/app-server>, раздел «6) Rate limits (ChatGPT)».
- Подтверждение в коде: `codex-rs/docs/codex_mcp_interface.md`, PR openai/codex#5302 («[app-server] read rate limits API», merged 2025-10-20).
- Локально доступно: `codex-cli 0.144.1`, команда `codex app-server` (`daemon`, `proxy`, `generate-json-schema`).

**Ограничение:** «App-server authentication has never been permitted for commercial or hosted services» — для личного использования это не блокер, но границу надо помнить.

### 1.2 Slash-команды и UI

- `/usage` — «View account token usage or use a rate-limit reset»; есть `daily`, `weekly`, `cumulative`. <https://developers.openai.com/codex/cli/slash-commands>
- `/status` — «Display session configuration and token usage». Там же.
- Веб: Settings → Usage, и «Codex usage dashboard». <https://help.openai.com/en/articles/20001625-understanding-chatgpt-and-codex-usage-limits> (обновлено 2026-10-31).

### 1.3 Лимиты Plus/Pro

- «The usage limits for local messages and cloud chats share a five-hour window. Additional weekly limits may apply». <https://developers.openai.com/codex/pricing>
- Reset-кредиты: earned, с типом и сроком действия. <https://developers.openai.com/codex/changelog>
- Термин «banked resets» в официальных источниках не встречается — используется «earned rate-limit reset credits».

### 1.4 Заголовки `x-ratelimit-*`

Документированы для **PAYG API** (организация/проект, RPM/TPM), не для подписки. <https://developers.openai.com/api/docs/guides/rate-limits>

`UNKNOWN`: официального утверждения «к подписке не применимы» нет — вывод сделан из разделения документации.

## 2. Юридическая граница

Terms of Use (действуют с 2026-01-01), <https://openai.com/policies/row-terms-of-use/>:

- «You may not: … Automatically or programmatically extract data or Output.»
- «…circumvent any rate limits or restrictions or bypass any protective measures…»

Прямой `GET https://chatgpt.com/backend-api/wham/usage` в официальной документации **не найден** — `UNKNOWN`. Это недокументированный внутренний путь, и его использование подпадает под риск ToS.

**Следствие:** если строить мониторинг, приоритет — `codex app-server` (`account/rateLimits/read`), а не парсинг внутреннего endpoint. `wham/usage` остаётся как fallback с явно записанным риском.

## 3. Готовые решения (не изобретать велосипед)

| решение | лицензия | что меряет | как берёт данные | применимость |
|---|---|---|---|---|
| [martinge17/codex-exporter](https://github.com/martinge17/codex-exporter) | MIT | остаток подписки: 5h, 7d, code review | OAuth + `wham/usage` | Prometheus `/metrics`, Docker; путь недокументированный |
| [k1ll4p1x3l/12-llm-usage-exporter](https://github.com/k1ll4p1x3l/12-llm-usage-exporter) | Apache-2.0 | квоты Codex через app-server | `account/rateLimits/read` | Go-бинарник, Windows, `:9738`; beta |
| [durandom/token-burn](https://github.com/durandom/token-burn) | MIT | quota windows, burn rate, прогноз исчерпания | провайдерские usage endpoints | OTLP; бинари macOS/Linux |
| [jasoncodes/codex-usage-exporter](https://github.com/jasoncodes/codex-usage-exporter) | не указана | 5h/weekly, reset credits | device auth + `/wham/usage` | JSON/Influx, не Prometheus |

Смежные, но не про подписку: `foxdalas/openai-exporter` (PAYG-биллинг), `saturdaifever/grafana-codex-observability` (OTel Codex CLI), официальная интеграция Grafana Cloud «OpenAI Codex» (OTel, квоту не показывает).

**Готового Grafana-дашборда под квоту подписки нет** — только метрики.

## 4. Что это значит для решения

```text
источник       codex app-server, account/rateLimits/read   (документирован)
альтернатива   wham/usage                                  (недокументирован, риск ToS)
формат         logfmt → Loki, как в deepinfra_usage        (конвенция репозитория)
независимость  не через Hermes: свой процесс + codex CLI   (правило мониторинга)
```

**Не берём готовый экспортёр как зависимость** по двум причинам: все они тянут Docker/Go-бинарь и внешний scrape-контур, а у нас уже есть конвенция push в Loki; и три из четырёх используют недокументированный endpoint, то есть не решают юридическую часть.

**Берём из них урок:** `k1ll4p1x3l` показывает, что app-server отдаёт те же поля, что и `wham/usage` — значит переход на официальный путь не теряет данные.

## 5. Открытые вопросы (UNKNOWN)

0. ~~Есть ли методы в протоколе~~ — **снято**: `codex app-server generate-json-schema --out .` (0.144.1) содержит `account/rateLimits/read` и `account/usage/read` в `ClientRequest.json` и в `codex_app_server_protocol.v2.schemas.json`. Метод документирован и в схеме, и в коде.
1. Отвечает ли `codex app-server` по stdio без интерактивного логина в нашей среде — нужен пробный вызов.
2. Есть ли риск конфликта с Hermes, который сам использует Codex-провайдер (общий auth, лимит соединений).
3. Стабилен ли контракт app-server между версиями CLI (0.144.1 сейчас).
4. Документирован ли срок жизни `access_token` и нужен ли refresh в экспортёре.
