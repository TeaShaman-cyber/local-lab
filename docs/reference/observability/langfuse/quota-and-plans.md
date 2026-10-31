# Quota, deletion and plans

## What a unit is

Billing counts **ingested units** per billing period: traces + observations + scores, as
they arrive. One agent turn usually lands as a trace plus a dozen or more observations
(LLM calls, tool calls), so a "50k event" hobby cap is a few hundred turns, not fifty
thousand.

## Deletion does not refund

Deleting traces is a retention operation. It does not return units already ingested, so
pruning old data never restores headroom in the current period. When asked whether
cleanup will buy quota back, answer no and say why — the counter measures what was sent,
not what is kept.

## Data access is not data existence

Free-tier plans expose only the last 30 days of history in UI and API, and the same 30-day
window applies to the public API's list and metrics endpoints. Older data is still stored
and becomes visible again after an upgrade (Core: 90 days, Pro: 3 years), so an empty
query for last quarter is not evidence the data was deleted or never written.

## Exceeding the cap

Ingestion suspends once a billing cycle reaches 250k units; there is no time-limited grace
period. Incoming data is then dropped until the cycle resets (the reset date is in the
warning email) or an upgrade. Existing data stays accessible. Treat new telemetry as
best-effort between suspension and reset, and take any baseline you need from data already
written.

## Programs (eligibility differs)

| Program | Requires | Notes |
|---|---|---|
| Non-Profit | A **registered** non-profit (501(c)(3) or equivalent) | Application form + verification, monthly credit |
| Research Grant | Academic/research affiliation **or** work intended for peer review | Email route; publication-intent accepted without academic / institutional affiliation |
| Fast Grant | Self-certified researcher/academic/student/teacher | Lighter credit (~$200/mo); **form route requires an academic email** + institution name/website, so it cannot serve an independent researcher |
| Open Source | An OSS project **with a licence** | Monthly credit for the first year |

A voluntary, unregistered, unaffiliated research project satisfies none of them on paper.
State that plainly to the vendor and ask which path fits, rather than claiming eligibility
you do not have — and name the concrete alternative (adding a licence, or self-hosting) so
the question is cheap to answer.

Publication-intent independents should not force the academic Fast Grant form (it needs a
university email and institution details). The operative route is a direct email to
`research@langfuse.com` with the project plus a concrete peer-review publication plan;
that team verifies eligibility for email applications. Track which route was actually taken
(e.g. check the Sent/thread for the `research@` email) before assuming the grant is in motion.
