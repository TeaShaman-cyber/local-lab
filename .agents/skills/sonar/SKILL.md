---
name: sonar
description: Use when a task materially depends on missing or ambiguous prior-chat context, especially an earlier decision, correction, constraint, reference, or episode, and the exact historical source is unknown or wording may differ; use for recall, recovery, comparison, or continuation across sessions. Do not use when current-conversation context is sufficient, an exact historical locator is already known and directly readable, or only current authoritative state is needed.
---

# Sonar (local)

Навигатор по доступной поверхности исторического поиска. Не бэкенд памяти и не слой авторитета.

Поверхности поиска:

```text
SURFACE A  Hermes session_search        DEFAULT   FTS5 по сессиям Hermes
SURFACE B  self-hosted session-search   ON DEMAND corpus Barn Doctor / экспорт
SURFACE C  MarcoPolo Session Search     ON DEMAND история ChatGPT/MarcoPolo
```

Начинать с A. Переходить на B или C только когда нужной истории в Hermes нет: другой харнесс, другой аккаунт, экспорт, более ранний период. Переход на другую поверхность — материальная смена маршрута, её надо называть явно.

## Non-negotiable boundaries

```text
retrieval result != truth
search miss != historical absence
semantic-region hit != exact locator
repeated fragment != independent corroboration
source metadata != proposition provenance
historical evidence != current authority
session_search <= LOCATED
VERIFIED requires authority + exact object + readback
```

Не обходить историю массово. Пробы ограничены. Сбой транспорта или инструмента — `DEGRADED`, а не «ничего не найдено».

## Trigger contract

```text
DISCOVERY != AUTO_TRIGGER

TRIGGER when:
  - пользователь просит вспомнить, восстановить, сравнить или продолжить
    что-то из прошлых сессий, и точный источник неизвестен;
  - задача материально зависит от прежнего решения, поправки, ограничения,
    ссылки или эпизода, которых нет в текущем контексте;
  - конкурирующие эпизоды или дрейф формулировок делают ответ по памяти
    небезопасным без поиска.

DO_NOT_TRIGGER when:
  - текущего контекста достаточно;
  - точный исторический объект известен и читается напрямую;
  - нужна только текущая авторитетная величина, а не реконструкция истории;
  - вопрос общий и не зависит от истории пользователя или проекта.
```

## Probe slots

Структурные слоты, не арифметический счётчик.

```text
PROBE_SLOTS
  LIGHT_LITERAL     OPTIONAL  UNUSED
  LIGHT_FUNCTIONAL  PRIMARY   UNUSED
  LIGHT_RELATIONAL  PRIMARY   UNUSED
  FULL_EXTRA        ESCALATE  LOCKED
```

Переходы монотонны: `UNUSED -> USED`, `LOCKED -> UNUSED -> USED`. Слот используется не более одного раза.

- `LIGHT_LITERAL` можно пропустить, если подлинного точного якоря нет.
- `LIGHT_FUNCTIONAL` — основная различающая проба.
- `LIGHT_RELATIONAL` — когда нужна независимая связь.
- Останавливаться сразу при достаточности для `LOCATED`; неиспользованные слоты не обязаны быть израсходованы.
- `FULL_EXTRA` разблокировать только при недостаточности LIGHT_SONAR и обоснованной эскалации; он разрешает ровно одну дополнительную пробу.
- Если нужный слот уже `USED` или свободных нет — завершать как `UNKNOWN`.
- Сбой транспорта — `DEGRADED`.

## LIGHT_SONAR control block

```text
LIGHT_SONAR
  LITERAL?      -> точное имя или фраза, когда есть
  FUNCTIONAL    -> восстановление по процессу или функции без аттрактора заголовка
  RELATIONAL    -> восстановление по связям, ролям или структуре авторитета

LOCATE only when:
  - минимум два разных режима проб несут STRONG-свидетельство;
  - эти STRONG-квитанции принадлежат минимум двум разным correlation groups;
  - хотя бы один STRONG-режим — FUNCTIONAL или RELATIONAL;
  - не осталось неразрешённых DRIFT или CONFLICT.

Otherwise:
  - использовать один свободный слот для следующей различающей пробы;
  - свободных слотов нет -> UNKNOWN;
  - транспорт недоступен -> DEGRADED.
```

Пропозициям из одного найденного фрагмента или группы контекста присваивать одинаковый `correlation_id`. Повторная выдача одной группы не является независимым подтверждением, даже если пришла в другом режиме или формулировке.

`SEMANTIC` может вернуть полезный регион, но `LITERAL + SEMANTIC` недостаточно для `LOCATED`.

## Probe DSL

```text
PROBE <LITERAL|SEMANTIC|FUNCTIONAL|RELATIONAL>
TARGET "<операторский ярлык цели>"
MUST "<обязательный якорь поиска>"
MUST "<необязательный второй якорь>"
SHOULD "<поддерживающая связь или подсказка>"
SHOULD "<необязательная вторая подсказка>"
MUST_NOT "<известный отвлекающий эпизод>"
TIME "<необязательное ограничение времени>"
LIMIT <1..10>
```

Правила:

- `TARGET` — обязательные метаданные оператора, в текст запроса не попадает никогда.
- Нужен минимум один `MUST` или `SHOULD`.
- Не больше двух клауз каждого повторяемого вида; порядок канонический: TARGET, MUST, SHOULD, MUST_NOT, TIME, LIMIT.
- `LIMIT` по умолчанию 5, держать малым (у `session_search` максимум 10).
- `MUST_NOT` — метаданные исключения после поиска, никогда не положительное свидетельство. Текст `MUST_NOT` в запрос не рендерится.

### Отображение DSL на session_search

| DSL | session_search |
|---|---|
| `MUST` / `SHOULD` | `query` (FTS5; boolean-выражения допустимы) |
| `TIME` | `after` / `before` (ISO-дата или относительный интервал) |
| `LIMIT` | `limit` |
| `MUST_NOT` | `exclude_session_ids` — id уже осмотренных сессий и их lineage |
| `TARGET` | не рендерится |
| уточнение региона | `sort`: `newest` для «где остановились», `oldest` для «как началось» |

Порядок работы по найденной сессии:

```text
query      -> discovery: top-N сессий, верхняя гидратируется
session_id + around_message_id -> scroll: окно вокруг якоря
session_id -> read: вся сессия (так разрешается ссылка @session:<profile>/<id>)
```

`role_filter` менять только осознанно: по умолчанию ищутся `user,assistant`; `tool` — для отладки поведения инструментов.

### MUST_NOT exclusion mechanic

Для каждой найденной correlation group:

1. Сравнить группу с операторскими `MUST_NOT`-дистракторами.
2. Совпавшую группу помечать исключённой до допуска провенанса.
3. Исключённая группа не даёт Receipt, не вносит STRONG/WEAK/DRIFT/CONFLICT и не участвует в `LOCATED`.
4. В трассу писать только идентичность группы, не повторять текст дистрактора в корректирующий запрос.
5. Если полезного свидетельства мало и есть свободный слот — одна корректирующая FUNCTIONAL или RELATIONAL проба без аттрактора и без формулировок дистрактора; осмотренные id передавать через `exclude_session_ids`.
6. Свободных слотов нет — `UNKNOWN`.

## Proposition provenance admission

Классифицировать каждую показанную пропозицию отдельно от метаданных контейнера.

```text
RETRIEVED_HISTORY     поддержана как историческое содержимое
QUERY_CONSTRAINT      пересказана или внедрена из текущего запроса/инструкции
SYNTHESIZED_CONTEXT   собрана, суммаризирована или выведена слоем поиска
UNKNOWN               происхождение не установлено
```

```text
RETRIEVED_HISTORY + STRONG -> STRONG
QUERY_CONSTRAINT  + STRONG -> WEAK
SYNTHESIZED_CONTEXT + STRONG -> WEAK
UNKNOWN           + STRONG -> WEAK
```

Историческая метка времени, ярлык `USER_FACT` или исторически выглядящая группа сами по себе не устанавливают провенанс показанной пропозиции.

## Evidence classification

```text
STRONG    отличительное свидетельство идентифицирует нужный эпизод
WEAK      релевантно или смежно, но идентичность частична
DRIFT     поиск ушёл в близкий, но материально другой эпизод
CONFLICT  свидетельства материально противоречат друг другу
NONE      полезного исторического свидетельства нет
```

Ранг — не уверенность. Всплытие результата наверх при повторном вызове не делает его сильнее.

## Attractor and correction mechanics

1. Не повторять тот же запрос с косметической правкой формулировки.
2. Добавить FUNCTIONAL или RELATIONAL пробу без аттрактора.
3. Известный дистрактор — в `MUST_NOT` / `exclude_session_ids`.
4. Если у эпизода была поздняя поправка — искать поправку отдельной пробой.
5. Сохранять исходное утверждение и поправку до разрешения через авторитет.
6. При остающемся конфликте — `UNKNOWN`, не среднее из уверенности.

## Temporal and falsifier mechanics

- `TIME` использовать только когда время различает конкурирующие эпизоды. Свежесть — подсказка маршрута, не доказательство нужности эпизода.
- Для сильного кандидата спросить, какое наблюдение показало бы, что эпизод не тот. Предпочитать фальсификатор, меняющий идентичность, связь, состояние поправки или временное окно; синонимичный повтор запроса фальсификатором не является.

## Authority handoff

При `LOCATED` прекратить широкие пробы и разрешать точный объект:

```text
историческое утверждение чата -> SURFACE A/B/C, точное сообщение
состояние репозитория/Git    -> GitHub / канонический репозиторий
утверждение памяти           -> memory provider с readback
файл или документ            -> Files или владеющий провайдер
```

Если нужный авторитет недоступен — `DEGRADED`. Результат поиска не повышать до `RESOLVED` или `VERIFIED`.

```text
ROUTED    авторитет выбран, точный объект не найден
RESOLVED  точный объект найден, readback не проверен
VERIFIED  объект найден и readback подтверждён
DEGRADED  авторитет недоступен или исполнение сорвалось
```

## FULL_SONAR escalation

Не входить автоматически после обычного промаха. Одна выбранная механика через единственный слот `FULL_EXTRA`:

```text
SEMANTIC             -> более широкий смысловой регион
RELATIONAL/AUTHORITY -> отличительная роль или владение
TEMPORAL             -> конкурирующие эпизоды по времени
CORRECTION           -> поздняя поправка или разворот
FALSIFIER            -> наблюдение, опровергающее кандидата
EPISTEMIC_BOUNDARY   -> восстановить оговорку или явное не-утверждение
SOURCE               -> восстановить вероятный маршрут авторитета
```

Останавливаться, когда слот израсходован, когда следующая проба повторяет тот же аттрактор или фрагмент, либо когда свободных слотов нет. Завершать как `UNKNOWN`, а не открывать новую пробу.

## Evaluation trace

```text
SONAR_TRACE v0
STATE <UNLOCATED|LOCATED|ROUTED|RESOLVED|VERIFIED|DEGRADED|UNKNOWN>
SLOT <LIGHT_LITERAL|LIGHT_FUNCTIONAL|LIGHT_RELATIONAL|FULL_EXTRA> <UNUSED|USED|LOCKED>
PROBE <MODE> <EVIDENCE> <PROVENANCE> GROUP <correlation_id>
EXCLUDE GROUP <correlation_id> REASON MUST_NOT_MATCH
AUTHORITY <SESSION_SEARCH|SELF_HOSTED|MARCOPOLO|GITHUB|MEMORY_PROVIDER|FILES|UNKNOWN>
NEXT <PROBE|RESOLVE_AUTHORITY|VERIFY_READBACK|NONE>
BOUNDARY "<короткая причина>"
```

Для обычного ответа трассу не вываливать; она нужна для тестов, отладки или явного запроса. Неопределённость и деградировавшие маршруты называть прямо.
