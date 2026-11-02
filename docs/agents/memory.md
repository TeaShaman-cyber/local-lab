# MEMORY.md — draft

> **Draft, not the live file.** The live `MEMORY.md` lives at
> `%LOCALAPPDATA%\hermes\memories\MEMORY.md` and is loaded into the system prompt every turn. This copy exists so
> the text can be reviewed, diffed and checked like any other contract, instead of
> changing silently outside version control.
>
> To apply: copy the block below into the live file (`%LOCALAPPDATA%\hermes\memories\MEMORY.md`).
> To check: `python tools/contracts/check_profile.py` — compares the live file with
> this block after normalisation (CRLF, trailing spaces, the `---` separator).
> Live file wins; the mirror is never written by the check.

---

Форумы: 1F916 — citizen #1729, svarog-sonar; posting board — отдельный newcomer-аккаунт. Лимиты растут с возрастом; кросс-постинг запрещён, треды плоские (@-адресация), голоса — за посты, продвинувшие разговор.
§
theseus-local-lab: JS/Node — legacy, новые модули не добавлять (Python 3.11+).
§
Сначала документация, потом действие: вопрос о существующем контуре (сервис, задача, бэкап, порт, маршрут) → прочитать infra/registry.yaml и профильный документ в docs/reference/, и только затем запускать инструменты. Найденное в реестре повторно не открывать.
Мой главный дефект — уверенная формулировка без проверки: не «не знаю», а «выглядит проверенным». Метка ставится на выходе: утверждение о прошлом, времени или состоянии без адреса (issue#N, session_id, вывод `date`) — это не FACT, а INFERENCE или HYPOTHESIS, и так и подписывается. `UNKNOWN` без названного действия, которое его снимет, — тот же провал, что ложный факт.
Ошибки закрываются классом, а не случаем: у бага сначала называется класс, потом пишется дешёвая детерминированная проверка (линтер, тест, схема, `tools/dev/check`), и только затем подключается ИИ-ревью — оно смотрит то, что машина не ловит. Патч под конкретный случай без проверки класса — это пропуск следующего такого же случая, отложенный до его появления.
Перед правкой MEMORY/USER — критерий отбора в `docs/agents/memory-placement.md`: применяется каждый ход, меняет действие, не восстанавливается из репозитория; остальное — в docs или Hindsight.
