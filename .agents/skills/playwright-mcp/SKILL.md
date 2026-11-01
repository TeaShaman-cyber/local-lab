---
name: playwright-mcp
description: "Use when a setting lives only in the web UI, not the API."
version: 1.0.0
author: Semyon Poklad + Svarog
license: MIT
metadata:
  hermes:
    tags: [browser, playwright, mcp, ui-automation]
    related_skills: [playwright, agent-browser-monitoring]
---

# Playwright MCP (Hermes)

## Официальный пак — проверен, не подходит

`microsoft/playwright-mcp` поставляет ровно один скилл:

```text
.claude/skills/playwright-triage/SKILL.md   6325 B
```

Он **не про использование браузера**. Это триаж issues самого Playwright MCP внутри их GitHub Actions: клоны `$REPO_DIR` и `$PLAYWRIGHT_DIR`, сборка, драфт-PR в `microsoft/playwright`. Пользовательского скилла («водить браузер через MCP») у Microsoft нет, поэтому этот файл — не копия чужого, а своё.

## When to Use

- The setting you need exists **only in a web UI** and the API cannot read or write it (GitHub Project workflow filters, Grafana adaptive-metrics rules, account consoles).
- A task needs form filling, many sequential clicks, navigation, or network/console inspection in a real browser.
- You must see what the user sees to decide the next step, and a screenshot in chat is not enough.

Do **not** use it for: reading one page with a single click (the desktop preview pane is faster and lighter), scripted repeatable test flows (`playwright-cli` via the `playwright` skill), or anything where the API already answers the question.

Drive a real browser through the 25 `mcp__playwright__*` tools. Use this when a setting exists **only in a web UI** — GitHub Project workflows, Grafana adaptive metrics, any console without an API.

For CLI-first automation use the `playwright` skill instead. This one is MCP-only and assumes headed Chromium with the persistent profile.

## The setup here

```
profile    C:/Users/s.poklad/playwright-profile   (logins persist between sessions)
mode       headed — the window is visible and the user can interact
reload     /reload-mcp after config changes, otherwise the old args stay in effect
```

Reach for it when the API is closed: GitHub Project **workflow filters** are readable and writable only through the UI — GraphQL `ProjectV2Workflow` exposes `name`, `enabled`, `number` and nothing else.

## Core loop

```
browser_navigate(url)
browser_snapshot()          -> accessibility tree with refs
browser_click(target=...)
browser_snapshot()          -> refs change after navigation
```

**Snapshot before every interaction.** Refs are per-snapshot; after navigation, a modal, or a tab switch they are dead.

## Traps that already cost time

| trap | what happens | fix |
|---|---|---|
| **parameter name** | `browser_click(ref=...)` fails: schema requires `target` | always `target`, plus `element` for the permission prompt |
| stale ref | click reports a missing ref | re-snapshot, use the new ref |
| overlay blocks click | sticky header swallows the click, nothing happens | `browser_evaluate` with a JS `.click()` |
| wrong URL shape | `/orgs/<owner>/projects/N/workflows` -> 404 | user projects are `/users/<owner>/projects/N/workflows` |
| login wall | page title becomes "Verify two-factor authentication" | stop and ask the owner; never type the code |
| config not applied | changed args, behaviour unchanged | `/reload-mcp`, then verify by snapshot |

`browser_exec` and `browser_run_code_unsafe` are not a substitute for snapshot-driven work — prefer explicit tools, use JS only for the overlay case.

## Never

- Type a password, a 2FA code or a card number. On a login or 2FA form: call `browser_vault` (action=list) and if the app is not there, stop and ask.
- Accept a code in chat, even if offered.
- Claim the setting is applied — confirm with a fresh snapshot (the disabled/enabled state and the saved value are both visible).

## Verifying a change actually landed

Saving is not proof. After `Save workflow` the page returns to viewing mode, and the filter shows as a **disabled** textbox with the saved value. Read that value from the snapshot; if it still shows the old text, the save did not happen.

Then verify behaviour, not the form: create a throwaway item, wait, and check via the API whether it appeared. A workflow marked `enabled: true` in GraphQL can still match nothing because its filter is wrong.

## Choosing between browser surfaces

| task | tool |
|---|---|
| read a page, one click, screenshot in chat | desktop preview pane (side browser) |
| forms, many clicks, navigation, network/console | Playwright MCP |
| scripted repeatable flow, no MCP runtime | `playwright-cli` via the `playwright` skill |
