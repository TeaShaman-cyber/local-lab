---
name: talarion-exa-parallel-chain
description: Use when an issue needs chained current-web research.
version: 0.1.0
author: Semyon Poklad, Hermes Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [research, Talarion, Exa, Parallel, MCP, issues, evidence]
    related_skills: []
---

# Talarion → Exa → Parallel Research Chain

Use this skill to investigate a current issue by executing a research chain, not by writing a comparison of providers. Each stage must consume the output of the previous stage. The result is an evidence-backed decision for the issue, with UNKNOWNs preserved.

## When to Use

Use for an issue that needs current web facts, official documentation, MCP/API discovery, or a technical integration decision. Do not use this as a generic web-search shortcut when no issue-specific decision is required.

## Core Contract

Run the stages in this order:

```text
issue question and acceptance
  → Talarion freshness/surprisal scan
  → Exa primary-source discovery and fetch
  → Parallel independent verification and omission check
  → reconcile facts, conflicts, and UNKNOWNs
  → decision-ready issue evidence
```

Do not replace the chain with a vendor overview. Do not call Exa or Parallel before Talarion unless Talarion is unavailable; if the fallback is needed, record the missing stage and why.

## Prerequisites

- A precise issue question and a bounded acceptance criterion.
- Native Talarion MCP binding when available; the current Hermes binding may expose `mcp__talarion__search` even when Talarion documentation calls the capability `research_brief`.
- `npx` for one-off `mcporter` calls. Prefer `npx --yes mcporter`; a missing global `mcporter` binary is not a blocker.
- Use only read-only search/fetch tools. Do not enter credentials, start OAuth, or invoke account-connected research tools without explicit approval.

## Procedure

### 1. Frame the issue

Read the actual issue body, comments, and acceptance criteria before calling any research service; the title alone is not sufficient context. Write one focused research question. Define what would count as an actionable result: official MCP, official API, native client route, rejected integration, or insufficient evidence. Keep the issue number and target product exact.

Do not guess a product, capability, prompt name, resource ID, endpoint, or tool name from the issue title. Verify the entity exists before building the chain around it.

Completion criterion: the question can be answered by evidence from official docs, a verified repository, or a reproducible live probe, and the issue's actual acceptance criteria are represented.

### 2. Run Talarion first

Use the exact native Talarion MCP tool exposed in the current session with one focused query about the target product and integration question. Do not substitute `get_prompt`, guess a resource identifier, or invent a tool name; inspect the live tool schema when the binding is unclear. Ask for the current state of the product, official MCP/API names, and recent changes; do not encode an expected answer into the query.

Extract:

- surprising or recently changed facts;
- exact product, capability, endpoint, and repository names;
- dates and source URLs;
- search terms that should guide Exa.

Talarion is a freshness and omission layer, not the final source for granular technical claims. Its output must change or sharpen the next Exa query.

Completion criterion: the next-stage Exa query contains concrete terms learned from Talarion, or the absence of useful terms is explicitly recorded.

### 3. Use Exa for primary-source discovery

Search Exa with the Talarion-derived terms. Prefer official vendor documentation, official repositories, protocol specifications, and issue discussions over SEO pages. Use the bounded default surface:

```text
https://mcp.exa.ai/mcp?tools=web_search_exa,web_fetch_exa
```

For a one-off terminal fallback:

```bash
npx --yes mcporter call 'https://mcp.exa.ai/mcp.web_search_exa' \
  --args '{"query":"<Talarion-informed query>","objective":"Find official documentation, repositories, endpoint details, authentication, and integration constraints for <issue target>. Exclude generic SEO pages and distinguish verified facts from UNKNOWNs.","numResults":8}' \
  --output json
```

Fetch only the strongest official URLs. Prefer excerpts first; use `web_fetch_exa` when exact wording, schema, endpoint, or privacy details are needed. Do not enable `agent_run` unless the user explicitly approves authenticated or potentially costly research.

The Exa command must actually run and produce a captured result; a proposed command or prose description is not completion. Pass concrete names and terms from the Talarion result into the Exa query.

Completion criterion: Exa yields a bounded set of candidate primary sources and at least one source is fetched or otherwise directly verified.

### 4. Use Parallel for independent verification

After Exa has identified the source vocabulary, search Parallel independently. Ask it to verify the Exa claims and find omissions or contradictory primary sources. Do not merely repeat the Exa query; include the issue decision and the terms that need checking.

Prefer the current unified endpoint:

```text
https://mcp.parallel.ai/mcp?tools=web_search,web_fetch
```

For the standalone legacy Search MCP, which may still be useful as a bounded fallback:

```text
https://search.parallel.ai/mcp
```

A one-off call uses `npx --yes mcporter`, `web_search`, and a stable session identifier. Keep `web_research` and authenticated research disabled unless explicitly approved.

The Parallel call must actually run after the Exa stage and its objective must name what Exa found that needs verification. Do not report a chain step as complete from a command template or a provider description.

Completion criterion: Parallel returns independent evidence, a contradiction, or an explicit failure/coverage gap; never report “confirmed” from a duplicated snippet alone.

### 5. Reconcile the evidence

Build a compact table with these columns:

```text
claim | Talarion signal | Exa primary evidence | Parallel check | status | source URLs
```

Classify every material claim as `Confirmed`, `Refuted`, `Superseded`, or `Unknown`. Distinguish:

- product marketing from an actual endpoint;
- a live endpoint from a configured Hermes server;
- a tool name from a stale client catalog;
- an anonymous keyless probe from an authenticated production capability;
- documentation instructions from a successful local read-back.

When sources disagree, fetch the authoritative page or inspect the live schema. Do not average conflicting claims and do not silently choose the newest snippet.

Completion criterion: every decision-driving claim has a status and a source, and every unresolved gap is named.

### 6. Gate the decision

If the target entity, official endpoint, or decisive evidence remains unverified, stop at `INSUFFICIENT_EVIDENCE`. Do not turn thin search snippets into an integration recommendation. The named chain is not evidence that the chain was executed.

### 7. Produce the issue decision

Return the result in this order:

1. Decision: `ADOPT_OFFICIAL_MCP`, `ADAPT_THROUGH_DISCOVERY_LAYER`, `USE_NATIVE_API`, `REJECT_PRIVACY/AUTH`, or `INSUFFICIENT_EVIDENCE`.
2. Evidence chain: what Talarion changed, what Exa found, what Parallel verified or challenged.
3. Minimal integration surface: exact endpoint and smallest tool allowlist.
4. Auth and privacy boundary: what is known, what was not touched, and what remains UNKNOWN.
5. Reproducible read-only probe and observed result.
6. Issue-ready next step, without mutating GitHub unless the user explicitly asks.

## Quick Reference

```text
Talarion native search
→ Exa web_search_exa
→ Exa fetch official URLs
→ Parallel web_search
→ Parallel fetch conflicting/decisive URLs
→ reconcile and classify
→ issue decision
```

Recommended bounded tool surfaces:

```text
Exa:     web_search_exa, web_fetch_exa
Parallel: web_search, web_fetch
```

## Reading limits: never truncate, always finish the page

A truncated source is worse than a missing one: it reads as complete. Two rules, both
paid for by measurement in the lab (2026-10-31: 23 of 25 extracts below the tool
default; 37 pages truncated in 48 h, 1 followed up).

1. **Do not lower the budget.** For `web_extract`, omit `char_limit` entirely — the tool
default is 15 000 chars and any lower value cuts the document. For Exa's
`web_fetch_exa`, pass `maxCharacters` explicitly at 15000 or more; its own default is
3000, five times smaller than web_extract's. Lifting Exa to its default is not enough.
2. **Finish what was cut.** A response containing `[TRUNCATED]` carries a footer with
`Full text saved to: <path>` and the exact `read_file path="…"` call for the omitted
middle. Read it before making any claim about the document. If the middle is genuinely
not needed, say so and name what was skipped — silence is the failure mode.

The failure this prevents is not a lost byte; it is a decision made on the first fifth
of a page that looked whole.

## Pitfalls

- Talarion is not a replacement for web search; it is the freshness/omission preflight.
- Exa and Parallel are independent evidence routes, not two names for the same service.
- `search.parallel.ai/mcp` is the standalone legacy Search MCP; `mcp.parallel.ai/mcp` is the current unified endpoint for new installations.
- A successful `mcporter list` proves only that the ad-hoc endpoint answered; it does not prove Hermes configuration or persistence.
- A `405` during Talarion discovery can indicate a transport mismatch; do not call the provider unavailable without checking the native binding and documented transport.
- Never expose advanced or research-agent tools merely because the provider offers them.
- Never print or save API keys, OAuth tokens, cookies, browser state, or private research payloads.
- Never report a page as read when only a fragment was shown: `[TRUNCATED]` in the result
  means the middle was cut, and the footer names the file that still has it.
- Do not create or update an issue merely because the chain produced a result; separate research from the requested external write.

## Verification

Before claiming the chain completed, verify all of the following:

- The actual issue body and acceptance criteria were read before research.
- Talarion was actually called first, or its failure and fallback were recorded.
- Exa actually ran and its query visibly uses terms learned from the Talarion stage.
- Parallel actually ran after Exa and had an independent verification objective.
- The evidence shows execution and handoff between stages; a narrated pipeline without tool output fails this gate.
- At least one decisive URL was read directly or returned with sufficient official excerpt evidence.
- No `web_extract` call lowered `char_limit` below its 15 000 default, and no Exa fetch
  asked for fewer than 15 000 characters.
- Every `[TRUNCATED]` result was followed by reading the stored file, or the skipped
  middle is named explicitly as not read.
- Tool names, endpoint URLs, auth requirements, and local configuration state are not conflated.
- Counts and statuses match the enumerated evidence; do not report a larger total than was actually parsed.
- No credentials, OAuth flow, write operation, or persistent Hermes configuration change occurred without explicit approval.
