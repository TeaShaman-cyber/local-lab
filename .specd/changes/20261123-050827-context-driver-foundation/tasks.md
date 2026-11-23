# Tasks: context-driver-foundation

## 1. Reproducible framework runtime

- [x] 1.1 Pin official SpecD packages and audited dependency graph
      `package.json`, `package-lock.json`: declare exact CLI/schema versions, reviewed native install-script allowlist and fixed transitive overrides
      Approach: keep the manifest private and tooling-only; bind integrity with npm lock; require `npm audit` to report zero known vulnerabilities after overrides
      (Req: SpecD lifecycle, Privacy and sensitivity)
- [x] 1.2 Build the official CLI in an isolated pinned container
      `tools/specd/Dockerfile`: multi-stage image that installs build dependencies only in the build stage and executes `specd --version`
      Approach: use digest-pinned `node:24.18.0-trixie-slim`; `npm ci --strict-allow-scripts`; copy only installed runtime into the final image
      (Req: SpecD lifecycle, Failure semantics)
- [x] 1.3 Add worktree-aware container wrapper
      `tools/specd/run.sh`: `tools/specd/run.sh <args...>` — map repository worktree/common Git metadata and existing host identity into `/workspace`
      Approach: compute paths with `git rev-parse --path-format=absolute`; mount common Git metadata read-only; pass identity ephemerally via `GIT_CONFIG_*`; never modify host config
      (Req: SpecD lifecycle, Privacy and sensitivity)
- [x] 1.4 Initialize official project configuration
      `specd.yaml`, `.gitignore`: configure `@specd/schema-std`, owned default workspace and filesystem change stores; ignore only developer-local override
      Approach: retain versioned project config and artifacts; do not install agent plugins or generate project-owned agent assets
      (Req: SpecD lifecycle)

## 2. Human context and normative contract

- [x] 2.1 Preserve discovery and proposal separately
      `.specd/changes/*-context-driver-foundation/specd-exploration.md`, `proposal.md`: capture the human incident history, accepted boundaries, alternatives and scope
      Approach: keep exploration outside the schema DAG; keep proposal concise and list exactly one new spec with no unresolved blocking question
      (Req: SpecD lifecycle)
- [x] 2.2 Define the context-driver living specification
      `.specd/changes/*-context-driver-foundation/specs/default/context-driver/core-contract/spec.md`: encode authority, acquisition, lineage, mirror, canonical, transient-state, projection, replay, privacy, drift, failure and evidence invariants
      Approach: use independently testable SHALL/MUST requirements; distinguish private evidence from canonical state and SpecD implementation coverage from Hermes schema coverage
      (Req: Source authority, Acquisition boundary, Source lineage, Private mirror fidelity, Canonical decoding, Derived and transient state, Projection semantics, Replay prohibition, Privacy and sensitivity, Schema drift, Failure semantics, Verification evidence, SpecD lifecycle)
- [x] 2.3 Define edge-focused verification scenarios
      `.specd/changes/*-context-driver-foundation/specs/default/context-driver/core-contract/verify.md`: provide exact requirement-matched GIVEN/WHEN/THEN scenarios
      Approach: emphasize rejection, rollback, idempotency, privacy and false-authority cases; mark runtime scenarios without an implementation as future acceptance targets rather than current proof
      (Req: Source authority, Acquisition boundary, Source lineage, Private mirror fidelity, Canonical decoding, Derived and transient state, Projection semantics, Replay prohibition, Privacy and sensitivity, Schema drift, Failure semantics, Verification evidence, SpecD lifecycle)
- [x] 2.4 Record concrete implementation design and task history
      `.specd/changes/*-context-driver-foundation/design.md`, `tasks.md`: map repository files, container route, QA integration, risks, rollback and verification
      Approach: distinguish framework implementation in this change from runtime requirements that remain specified but unimplemented
      (Req: SpecD lifecycle, Verification evidence)

## 3. Canonical QA integration

- [x] 3.1 Add a deterministic SpecD gate
      `tools/specd/check.sh`: execute version, project-status, schema and complete active-change validation
      Approach: use only `tools/specd/run.sh`; any official CLI/config/schema/change error exits non-zero
      (Req: SpecD lifecycle, Failure semantics)
- [x] 3.2 Integrate scripts and SpecD into repository QA
      `tools/dev/check.sh`: include `tools/specd/*.sh` in ShellCheck/syntax discovery and invoke the SpecD gate
      Approach: run when Docker is available; with `STRICT=1`, missing Docker is a hard failure; otherwise print an explicit skip
      (Req: SpecD lifecycle, Verification evidence)
- [x] 3.3 Keep Docker build context bounded
      `.dockerignore`: verify `node_modules`, `.git`, local caches and generated data remain excluded while package manifests and `tools/specd/` are available
      Approach: extend exclusions only when required; do not break existing maintenance-image contexts
      (Req: Privacy and sensitivity, SpecD lifecycle)
- [x] 3.4 Document the canonical operator route
      `tools/specd/README.md`: record commands, lifecycle order, versioned/ignored state and Windows/GLIBC troubleshooting
      Approach: make Docker the only supported local route and call out that pending-review artifacts must be revalidated in dependency order
      (Req: SpecD lifecycle, Verification evidence)

## 4. Verification and review

- [x] 4.1 Prove official structural validation succeeds
      `tools/specd/check.sh`: run against the complete change and record CLI `0.2.0`, schema validation and passed change validation
      Approach: capture exact command/result; do not equate structural validity with runtime scenario implementation
      (Req: SpecD lifecycle, Verification evidence)
- [x] 4.2 Exercise a missing-artifact negative control
      temporary worktree copy of one required artifact: remove or rename it, run `tools/specd/check.sh`, then restore it
      Approach: require non-zero validation with the missing artifact named; verify restored run returns zero
      (Req: SpecD lifecycle, Failure semantics)
- [x] 4.3 Run existing backend and full repository QA
      `tools/context_backend/`, `tools/state_db_backup/`, `tools/dev/check.sh`: prove no regression in source-boundary/schema-coverage gates
      Approach: run the existing deterministic suites and canonical QA; preserve exact counts and statuses
      (Req: Acquisition boundary, Schema drift, Privacy and sensitivity, Verification evidence)
- [ ] 4.4 Complete independent review and checkpoint evidence
      branch `feat/specd-context-288`, issue #288: review scope, security, rollback and exact test evidence before PR promotion
      Approach: commit/push exact head, read it back, open a focused PR linked to #288 and keep unresolved runtime scenarios explicitly unverified
      (Req: Verification evidence, SpecD lifecycle)
