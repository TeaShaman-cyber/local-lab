# Where knowledge goes

Four surfaces hold persistent knowledge, and only one of them is a guess-free choice.
This file exists because the guess was being made wrong: a user fact landed in
`MEMORY.md`, was carried through a cleanup back into `MEMORY.md`, and had to be moved
by hand (upstream `NousResearch/hermes-agent#84340`, observed 2026-08-11).

## The surfaces

| Surface | Owns | Loaded |
|---|---|---|
| `SOUL.md` | base agent identity — ships with Hermes, not edited here | every turn, slot #1 |
| `AGENTS.md` | project rules and entry map — **read-only for the agent** | every turn |
| `MEMORY.md` | agent anchor: style of work, environment facts, standing conventions | every turn |
| `USER.md` | **only** facts about the person | every turn |
| `docs/reference/` | reference material: procedures, API details, runbooks | on demand |
| `GLOSSARY.md` | project vocabulary: what a term means, and what to avoid | on demand |

## The rules

1. **A fact about the user goes to `USER.md`, never to `MEMORY.md`.**
   «Пользователь предпочитает…», «ценит…», «ожидает…», «режет спешку» — all `USER.md`.
   `MEMORY.md` describes the *agent's* world: environment, conventions, lessons.

2. **A detail that carries a path, a version, or a procedure belongs in `docs/`.**
   If the sentence would still be true next month and is only needed sometimes, it is
   reference, not memory. `MEMORY.md` may name the pointer («runbook:
   `docs/reference/pve.md`»), not repeat the content.

3. **A word with a specific meaning in this project belongs in `GLOSSARY.md`.**
   Including agent names: Лад = Codex, Сварог = Hermes, Шут = ChatGPT. Vocabulary is
   not a user preference and not an environment fact.

4. **`AGENTS.md` is not a memory file.** The agent reads it; the owner edits it. A
   lesson learned during work goes to a skill or `docs/`, and reaches `AGENTS.md` only
   by the owner's decision.

5. **Both memory files are budgeted.** `MEMORY.md` and `USER.md` are injected every
   turn, so their size is a cost paid on every request, forever. When either passes
   ~85%, run `memory-defrag` — not "add one more line and hope".

## How to check

```bash
wc -c "$LOCALAPPDATA/hermes/memories/MEMORY.md" "$LOCALAPPDATA/hermes/memories/USER.md"
```

A user-profile-shaped line in `MEMORY.md` is the specific defect this file exists to
prevent.

**There is no automated check for it yet.** A checker was named in the first draft of
this document and then never written — claiming otherwise would be exactly the false
assurance this contract is supposed to prevent, so it is stated plainly: the discipline
is manual. Before writing to `MEMORY.md`, ask one question: does the sentence describe
the *person* (then `USER.md`) or the agent's own world (then `MEMORY.md`)?

The detector is tracked as item 4 of #52, together with the phrase patterns it would
need and the warning-not-block behaviour: false rejection teaches people to bypass a
check, which is worse than no check.

## Why not one file

One file would be simpler and would guarantee the two never contradict each other. It
is rejected for a concrete reason: `USER.md` is the person's own record and is allowed
to be long and personal; `MEMORY.md` is the agent's working context and must stay
short. Merging them means either trimming the person's record to make room for agent
notes, or letting agent notes crowd out the person. Separation is what keeps each
budget honest.
