# Exploration: context-driver foundation

## Почему мы остановились и формализуем работу

Context backend перестал быть маленьким экспортёром `sessions`/`messages`. Исследование Hermes schema 33 показало 48 observed tables, отдельные источники identity/lineage, durable receipts, transient coordination state, derived FTS projections и чувствительные payloads. При этом Hermes продолжает владеть `state.db`, а локальный PostgreSQL остаётся производной проекцией.

Технические проверки предыдущих slices были зелёными, но две истории показали пределы одних тестов:

- issue #231: система могла быть внутренне согласованной вокруг неправильной архитектурной идеи (`is_first_turn` вместо recall по текущей содержательной реплике);
- OTel plugin incident: lifecycle SQLite store, writer thread, terminal close, два `LiveStore`, Windows file locks и ordering проявились только на реальном runtime boundary.

Опасение не в том, что Python «плохой», а в том, что зелёный локальный механизм не доказывает правильность намерения, ownership и lifecycle. До выбора языка или следующего decoder slice нужен долговечный человеческий и нормативный контракт.

## Что уже принято

1. Hermes `state.db` остаётся source authority.
2. Канонический ingestion route начинается с independently verified immutable snapshot, а не live-table connection.
3. Private mirror сохраняет source-specific evidence с fidelity, но не получает право replay в Hermes runtime.
4. Runtime-neutral canonical layer отделяется от source table names и переносит только явно декодированные semantics.
5. PostgreSQL projection read-only для потребителей, disposable и rebuildable из принятых evidence artifacts.
6. FTS shadow tables, SQLite mechanics, locks, leases, PIDs, heartbeats и pending queues не становятся canonical state автоматически.
7. Secrets и sensitive payloads не попадают в логи, fixtures или публичные receipts.
8. Custom harness, hooks, MCP, embeddings, cloud export и смена authority не входят в текущий scope.

Эти решения уже записаны в `docs/adr/0001-context-backend-source-boundary.md`, `docs/adr/0002-high-fidelity-runtime-mirror.md` и research/coverage artifacts #230.

## Почему SpecD

Нам нужен не ещё один ADR, а framework, который удерживает разные виды знания раздельно:

- exploration сохраняет живую причинную цепочку и сомнения;
- proposal фиксирует WHY и scope;
- spec определяет observable behaviour независимо от реализации;
- verify определяет доказательство этого behaviour;
- design описывает выбранный механизм;
- tasks превращает design в ограниченные проверяемые изменения;
- CLI валидирует структуру и lifecycle вместо надежды на память агента.

Поэтому используется официальный SpecD CLI/schema, а не каталог с похожими именами.

## Обсуждавшиеся варианты

### Оставить всё как ADR + issue thread

Недостаточно: контекст распределён, lifecycle не исполняется, требования и verification scenarios не образуют машинно проверяемый DAG.

### Написать локальный «SpecD-подобный» валидатор

Отклонено: мы получили бы ещё один собственный framework без upstream semantics и tooling.

### Установить SpecD непосредственно в Windows runtime

Проверка выявила upstream path-normalization failure: native CLI сравнивает `E:/...` от Git с `E:\\...` от Node и ошибочно сообщает, что config находится вне repository root. Локальный host route не принят.

### Использовать WSL

Работоспособно, но добавляет второй package installation/runtime path и различается с CI. После обсуждения выбран Docker как один Linux boundary для Windows и CI.

### Установить официальный agent plugin сразу

Отложено. Existing `AGENTS.md` и `.agents/skills` являются project contract; generated plugin assets нельзя перезаписывать без отдельной compatibility review.

### Python, Go или Rust для backend

Решение не принято в этом change. SpecD code graph сейчас поддерживает Python и Go, но не заявляет Rust adapter; это станет одним из критериев будущего language decision. Framework foundation не должен заранее выбирать язык реализации.

## Наблюдения при внедрении framework

- Официальный SpecD CLI `0.2.0` и schema-std `0.1.1` опубликованы как npm packages.
- Meta-package тащит native code-graph dependencies; install scripts должны быть allowlisted и исполняться только внутри build container.
- Первичный `npm audit` выявил vulnerabilities в transitive `tar`/`zod`; repository overrides закреплены на исправленных версиях, после чего audit сообщает zero known vulnerabilities.
- `lbug` prebuilt требует `GLIBC_2.38`; Debian bookworm (`2.36`) непригоден. Container использует pinned `node:24.18.0-trixie-slim` digest и проходит реальный `specd --version` during build.
- Git worktree metadata и существующая host identity передаются контейнеру ephemerally; container не меняет global identity/config.

## Что должно быть доказано первым срезом

1. Clean checkout может построить pinned container без host Node runtime.
2. Container запускает официальный CLI и резолвит официальный schema.
3. Active change содержит human exploration и пять schema artifacts.
4. Structural validation fail-closed включена в canonical QA.
5. Existing context-backend tests и repository QA не регрессируют.
6. Срез не меняет Hermes/PostgreSQL/live configuration и откатывается удалением tooling/artifacts.

## Открытые вопросы для следующих changes

- Какой язык выбрать для ingestion/mirror core после отдельного prototype and failure-model comparison?
- Когда включать SpecD code graph и какие implementation links считать обязательными?
- Какой следующий decoder slice после framework foundation: full-row private envelope, `state_meta` registry или canonical message identity?

Эти вопросы не блокируют foundation: данный change создаёт механизм, в котором они будут решаться и проверяться.
