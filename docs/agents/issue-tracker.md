# Issue tracker: GitHub

Issues and specs for this repo live as GitHub issues.

Two routes, chosen by what the operation does:

| Operation | Route |
|---|---|
| Read: view, list, search, comments, linked PRs | `gh` CLI **or** GitHub MCP (`mcp__github__issue_read`, `list_issues`, `search_issues`, `pull_request_read`) |
| Write: create, comment, label, close, assign | `gh` CLI only — the MCP server exposes no write tools |

Choose the thinner route: MCP when the session already has it bound and the call is a single read; `gh` when scripting, filtering with `jq`, or doing any mutation. Neither route replaces a read-back when the result matters.

**This split describes today's bound tool surface, not a permanent architecture.** Legacy `theseus-local-lab#123` (open) plans a discovery-only MCP layer with lazy tool binding: a capability search surface instead of hundreds of injected schemas, `gh`/GraphQL staying the complete route for Projects v2, and no silent fallback that changes a mutation route. When that lands, re-probe the surface before treating "MCP cannot write" as a rule.

## Conventions

- **Create an issue**: `gh issue create --title "..." --body "..."`. Use a heredoc for multi-line bodies.
- **Read an issue**: `gh issue view <number> --json number,title,body,labels,comments`.
- **List issues**: `gh issue list --state open --json number,title,body,labels,comments --jq '[.[] | {number, title, body, labels: [.labels[].name], comments: [.comments[].body]}]'` with appropriate `--label` and `--state` filters.
- **Make an issue a sub-issue of a parent**: `gh issue create --parent <parent> ...`, or `gh issue edit <parent> --add-sub-issue <child>` afterwards (`gh` 2.94+). Older `gh`: `gh api --method POST repos/<owner>/<repo>/issues/<parent>/sub_issues -F sub_issue_id=<child-db-id>` (database id, as in **Blocking** below). Without sub-issues, put `Part of #<parent>` at the top of the child body.
- **Comment on an issue**: `gh issue comment <number> --body "..."`
- **Apply / remove labels**: `gh issue edit <number> --add-label "..."` / `--remove-label "..."`
- **Close**: `gh issue close <number> --comment "..."`

Infer the repo from `git remote -v`; `gh` does this automatically when run inside a clone.

## Project as a coordination view

The board is `TeaShaman-cyber` Project **#19 "Local Lab — Operations"** (public).

- Git, Issues, PRs, CI receipts and exact remote readback remain authoritative. The Project is an overview only: it does not authorize implementation, release, publication or runtime mutation.
- Add an issue to the board: `gh project item-add 19 --owner TeaShaman-cyber --url <issue-url>`.
- Read the board: `gh project item-list 19 --owner TeaShaman-cyber --format json`.
- Fields: `Status` (Triage, Ready, In Progress, Blocked, Deferred, Verify, Done, Retired), `Area` (Infra, Memory, QA, Observability, Backup, Research, Docs, Skills), `Priority` (P0–P3).
- `Status` reflects observed state, not intent. `Priority` is set only when the issue text gives a reason. An empty field means `UNKNOWN`.
- `Maturity`, `Blocker` and `Disposition` are deliberately not configured: in the legacy board they were filled 10/73, 1/73 and 0/73 times, and empty fields created false structure.

## Pull requests as a triage surface

**PRs as a request surface: no.** _(Set to `yes` if this repo treats external PRs as feature requests; `/triage` reads this flag.)_

When set to `yes`, PRs run through the same labels and states as issues, using the `gh pr` equivalents:

- **Read a PR**: `gh pr view <number> --comments` and `gh pr diff <number>` for the diff.
- **List external PRs for triage**: `gh api --paginate 'repos/{owner}/{repo}/pulls?state=open' --jq '.[] | select(.author_association | IN("OWNER","MEMBER","COLLABORATOR") | not) | {number, title, author: .user.login, author_association, labels: [.labels[].name]}'`.
- **Comment / label / close**: `gh pr comment`, `gh pr edit --add-label`/`--remove-label`, `gh pr close`.

GitHub shares one number space across issues and PRs, so a bare `#42` may be either: resolve with `gh pr view 42` and fall back to `gh issue view 42`.

## When a skill says "publish to the issue tracker"

Create a GitHub issue.

## When a skill says "fetch the relevant ticket"

Read it as in **Read an issue** above.

## Wayfinding operations

Used by `/wayfinder`. The **map** is a single issue with **child** issues as tickets.

- **Map**: a single issue labelled `wayfinder:map`, holding the Notes / Decisions-so-far / Fog body. `gh issue create --label wayfinder:map`.
- **Child ticket**: an issue linked to the map as a GitHub sub-issue (see **Make an issue a sub-issue of a parent**). Where sub-issues aren't enabled, add the child to a task list in the map body and put `Part of #<map>` at the top of the child body. Labels: `wayfinder:<type>` (`research`/`prototype`/`grilling`/`task`). Once claimed, the ticket is assigned to the driving dev.
- **Blocking**: GitHub's **native issue dependencies**, the canonical, UI-visible representation. Add an edge with `gh api --method POST repos/<owner>/<repo>/issues/<child>/dependencies/blocked_by -F issue_id=<blocker-db-id>`, where `<blocker-db-id>` is the blocker's numeric **database id** (`gh api repos/<owner>/<repo>/issues/<n> --jq .id`, _not_ the `#number` or `node_id`). GitHub reports `issue_dependencies_summary.blocked_by` (open blockers only, the live gate). Where dependencies aren't available, fall back to a `Blocked by: #<n>, #<n>` line at the top of the child body. A ticket is unblocked when every blocker is closed.
- **Frontier query**: list the map's open children (`gh issue list --state open`, scoped to the map's sub-issues / task list), drop any with an open blocker (`issue_dependencies_summary.blocked_by > 0`, or an open issue in the `Blocked by` line) or an assignee; first in map order wins.
- **Claim**: `gh issue edit <n> --add-assignee @me`, the session's first write.
- **Resolve**: `gh issue comment <n> --body "<answer>"`, then `gh issue close <n>`, then append a context pointer (gist + link) to the map's Decisions-so-far.
