# PostHog Local Lab mutation-survivor Scout — paused source

Owner: Local Lab issue #234. Credential and runtime boundary: Theseus Cloud Shell issue #16.
Deployed infrastructure identity, routes and current paused posture are owned by
`INFRA-023`; this directory owns only the Git source and calibration contract.

The canonical Scout instruction body is scout-body.md and seven source-cited review examples are in known-cases.json. Six of seven cases intentionally carry partial ID prefixes; they are ONLY pedagogical labels and must never be joined to current mutant data by prefix. The full stable ID of the killed workspace-header mutant is verified.

The install name, target project and provider routes are read from `INFRA-023`, not
duplicated here. Mandatory initial posture remains disabled, non-emitting and without
write scopes; no active schedule or AI trial is authorized.

The present PostHog warehouse ingests GitHub workflow jobs, NOT complete mutation specs and diffs. If precise current survivor diff, identity and manifest cannot be retrieved, Scout must close with INPUT_INCOMPLETE. Default repo checkout alone does not prove PR-head or workflow artifact access. Never generate synthetic conclusions from CI success.

The PostHog skill/config are deployed PROJECTIONS of Git-owned source. Validate version and disabled posture by readback before claiming installed success. Static fixture validation does NOT constitute an LLM evaluation. Any future run or enablement may consume AI quota, and is separately authorized.

## Mutation artifact contract — updated under Local Lab #234

The CI artifact now identifies the actual checked-out commit and tree, plus
its GitHub Actions run URL; each surviving mutant includes complete canonical
mutation spec, full diff, stable 64-character fingerprint and explicit
worker/test verdict. Aggregation fails when runs or source trees differ.

This does NOT prove that PostHog's Scout sandbox can fetch the artifact.
No extra data warehouse pipeline or Scout execution is implied. Keep the
Scout disabled and non-emitting until read-only artifact access is verified.
