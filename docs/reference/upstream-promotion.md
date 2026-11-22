# Upstream promotion: exact-SHA live acceptance

This procedure owns one boundary: moving a branch that already feeds an external
upstream pull request. A push to that branch is publication, even when the branch
lives in a project-owned fork.

Tracked by [local-lab#281](https://github.com/TeaShaman-cyber/local-lab/issues/281).

## Invariant

```text
candidate branch in our fork != upstream-linked branch
push to upstream-linked branch == external publication
```

A candidate may advance the upstream-linked branch only when the **same 40-character
commit SHA** has all of these receipts:

1. deterministic repository QA;
2. outside review of the exact SHA;
3. installation of that exact SHA in the local runtime;
4. controlled restart;
5. live readback: installed file hashes, new process identity, Plugin Doctor,
   telemetry produced by that process, and a bounded error-log query;
6. a machine-readable receipt that passes
   `python tools/contracts/check_upstream_promotion.py`.

A receipt from an ancestor never transfers to a descendant. Passing tests, Docker
CI, mutation tests, model checking, or Codex review are necessary evidence when the
change requires them, but none substitutes for live acceptance of the exact SHA.

## Branch topology

```text
candidate/<topic>             mutable internal work
        |
        | fork PR + QA + exact-head review + live receipt
        v
upstream/<topic>              protected publication branch
        |
        +--------------------> external upstream PR head
```

A legacy branch already used as an upstream PR head is treated as an
`upstream/*` branch even if its name begins with `fix/`.

Direct push to an upstream-linked branch is prohibited. It advances only through a
fork pull request whose checks include `gate-checks`, `gate-review`, and
`live-acceptance`. The fork ruleset is the enforcement point; prose is only the
explanation.

## Receipt schema

Receipts live in `receipts/upstream-live/<40-char-sha>.json`. The filename and
`candidate.sha` must match. Required fields:

```json
{
  "schema": "local-lab.upstream-live-acceptance.v1",
  "status": "VERIFIED",
  "candidate": {
    "repo": "TeaShaman-cyber/hermes-otel",
    "sha": "<40 hex>",
    "files": {
      "hermes_otel/live_store.py": "<sha256>",
      "hermes_otel/live_store_registry.py": "<sha256>",
      "hermes_otel/pipeline.py": "<sha256>"
    }
  },
  "runtime": {
    "gateway_pid": 12345,
    "plugin_doctor_rc": 0,
    "plugin_hooks": 34,
    "tempo_trace_id": "<32 hex>",
    "tempo_process_pid": 12345,
    "loki": {
      "start": "<ISO-8601 UTC>",
      "end": "<ISO-8601 UTC>",
      "queries": ["WinError 32", "hermes_otel_live.db"],
      "matches": 0
    }
  },
  "verified_at": "<ISO-8601 UTC>",
  "issue_url": "https://github.com/TeaShaman-cyber/local-lab/issues/281"
}
```

The checker verifies structure, exact SHA, candidate file hashes, process identity,
Doctor result, Tempo identity, and a bounded Loki interval. It cannot manufacture
runtime truth: the receipt is created only after observed readback, then versioned
and reviewed in `local-lab`.

## Failure semantics

- Missing receipt: `BLOCKED`.
- Receipt SHA differs from candidate head: `STALE` and `BLOCKED`.
- Candidate file hash differs from the receipt: `STALE` and `BLOCKED`.
- Doctor non-zero, PID mismatch, invalid Tempo trace, or absent Loki bounds:
  `BLOCKED`.
- Runtime restarted after receipt or candidate changed: `REPROBE_REQUIRED`.
- A working fallback does not make the preferred gate healthy; report the failure.

## Recovery from premature publication

1. Preserve the unverified commit on `candidate/<topic>`.
2. Restore the upstream-linked branch to the last exact-SHA live-verified commit.
3. Verify the external PR head by readback.
4. Complete QA and live acceptance on the candidate.
5. Advance the upstream-linked branch through the protected fork PR only.
