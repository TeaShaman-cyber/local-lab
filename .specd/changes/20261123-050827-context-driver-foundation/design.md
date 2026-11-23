# Design: context-driver-foundation

## Non-goals

- Change Hermes acquisition, decoding, mirroring or projection runtime behaviour.
- Choose Python, Go or Rust for the next backend slice.
- Install a SpecD agent plugin or overwrite project-owned agent assets.
- Enable code-graph indexing, hooks, MCP, cloud export or a custom harness.
- Claim that every runtime scenario in the new living spec is already implemented. The spec is the contract future slices must satisfy; unimplemented capabilities remain explicitly unverified.

## Affected areas

- `.gitignore`
  - Change: ignore developer-local `specd.local.yaml` while retaining versioned project configuration.
  - Dependents: Git and all contributors; additive, low risk.
- `tools/dev/check.sh`
  - Change: include `tools/specd/*.sh` in shell syntax/ShellCheck discovery and invoke `tools/specd/check.sh` as a fail-closed gate when Docker is available; `STRICT=1` treats missing Docker as failure.
  - Dependents: local QA, GitHub Actions and every PR; high fan-in, medium risk because the new gate builds a container.
- `.dockerignore`
  - Change: keep package manifests and SpecD tooling in build context while excluding `node_modules`, `.git` and unrelated generated data.
  - Dependents: existing Docker builds; low risk because existing exclusions remain.
- `docs/adr/0001-context-backend-source-boundary.md`, `docs/adr/0002-high-fidelity-runtime-mirror.md`, `docs/research/context-backend-coverage-schema33.md`
  - Change: none. They remain evidence inputs referenced by the new living spec.
- `tools/context_backend/` and `tools/state_db_backup/`
  - Change: none. Existing tests remain regression evidence for acquisition and schema-drift invariants.

No existing application symbol or runtime API changes. The only integration hotspot is the canonical QA script.

## New constructs

- `package.json`
  - Shape: private tooling manifest with exact `@specd/specd@0.2.0`, `@specd/schema-std@0.1.1`, reviewed install-script allowlist and security overrides for `tar@7.5.23` and `zod@4.6.7`.
  - Responsibility: declare reproducible SpecD build inputs; it does not define an application runtime.
- `package-lock.json`
  - Shape: npm lockfile generated from the tooling manifest.
  - Responsibility: bind the full dependency graph and integrity hashes.
- `specd.yaml`
  - Shape: `@specd/schema-std`, owned `default` workspace at `specs/`, filesystem-backed change/draft/discard/archive stores under `.specd/`.
  - Responsibility: canonical SpecD project configuration.
- `tools/specd/Dockerfile`
  - Shape: multi-stage build from digest-pinned `node:24.18.0-trixie-slim`; build stage installs reviewed native scripts with `npm ci --strict-allow-scripts`; runtime stage contains Git and the installed framework; entrypoint is `specd`.
  - Responsibility: provide one Linux framework runtime for Windows and CI without host Node dependencies.
- `tools/specd/run.sh`
  - Interface: `tools/specd/run.sh <specd arguments...>`.
  - Responsibility: build/cache the pinned image, map the current Git worktree and common Git directory, pass the already-configured host Git identity ephemerally, and execute official CLI arguments in `/workspace`.
  - Relationships: called by maintainers and `tools/specd/check.sh`; depends on Docker and Git, not WSL or host npm.
- `tools/specd/check.sh`
  - Interface: no arguments; non-zero on any framework/config/schema/change validation failure.
  - Responsibility: prove CLI version, project status, schema validity and complete structural validation of `context-driver-foundation`.
- `tools/specd/README.md`
  - Shape: local command, lifecycle, boundary and troubleshooting guide.
  - Responsibility: make the containerized route and pending-review semantics usable without reconstructing this implementation session.
- `specs/default/context-driver/core-contract/{spec.md,verify.md}` after archive
  - Responsibility: living normative context-driver contract and its scenarios.
- `.specd/changes/<timestamp>-context-driver-foundation/`
  - Shape: `manifest.json`, `specd-exploration.md`, `proposal.md`, scoped `spec.md`/`verify.md`, `design.md`, `tasks.md`.
  - Responsibility: active change record and preserved human reasoning.

## Approach

1. Use official `@specd/specd` and `@specd/schema-std`; do not reproduce lifecycle logic locally.
2. Build the framework only inside Docker. The image build proves the native dependency set can load by executing `specd --version`.
3. Pin the base image by digest and npm graph by lockfile. Allow only the five reviewed native install-script packages. Override vulnerable transitive `tar`/`zod` versions and require zero known npm audit findings at lock generation time.
4. Map Git metadata explicitly so official CLI works in linked worktrees. Host identity is read from existing Git config, passed through process environment and never written to project/global config.
5. Configure an owned default workspace and create `default:context-driver/core-contract` as the first living spec.
6. Keep `specd-exploration.md` outside the schema DAG so the human causal history remains richer than the normative artifacts.
7. Gate repository QA on official config/schema/change validation. In CI (`STRICT=1`), unavailable Docker is a failure, not a silent skip.
8. Preserve existing domain checks. SpecD implementation coverage is not substituted for `coverage_matrix.yaml`; each has a distinct name and purpose.
9. Archive the change only after implementation, canonical QA and independent review pass. Archive materializes the living spec under `specs/`.

The adopted spec covers all core requirements, while this foundation implementation supplies only framework/lifecycle enforcement. Runtime requirements without an implementation are explicitly future work and MUST NOT be reported as verified by this change.

## Key decisions

**Containerized CLI is canonical** → native Windows `0.2.0` fails path normalization and WSL would introduce a second local package/runtime path. **Alternatives rejected** → native host install (observed failure), WSL (works but differs from CI and duplicates dependency state).

**Use the official meta-package and standard schema** → preserves upstream lifecycle semantics and future code-graph capability. **Alternative rejected** → a local Markdown convention or validator would silently diverge.

**Execute reviewed native install scripts only in the build stage** → SpecD imports `lbug` through code-graph even for basic CLI startup. **Alternatives rejected** → `--ignore-scripts` produces an unloadable CLI; unrestricted host install expands supply-chain and host-state risk.

**Debian trixie runtime** → upstream `lbug@0.14.3` prebuilt requires `GLIBC_2.38`; bookworm supplies `2.36`. **Alternative rejected** → bookworm was exercised and failed `ERR_DLOPEN_FAILED`.

**Defer agent plugins** → project-owned `AGENTS.md` and `.agents/skills` already define authority and routing. **Alternative rejected** → generated assets without compatibility review could overwrite the contract.

**Create a global contract before selecting language** → failure semantics and authority must constrain every language equally. **Alternative rejected** → choosing a language first would encode architecture before requirements.

## Trade-offs

- Container image build is heavier than host CLI execution → digest pinning and Docker layer cache make repeat checks cheap; the same route runs locally and in CI.
- The SpecD meta-package installs code-graph native dependencies before code graph is enabled → install scripts are exact-version allowlisted, isolated in build stage and audited.
- Host Git identity is passed into the container → values are read from pre-existing configuration, not printed or persisted by the wrapper.
- The new living spec contains runtime scenarios not yet implemented → validation establishes the contract only; future changes must attach concrete evidence and cannot mark those scenarios verified by inheritance.
- `tools/dev/check.sh` gains Docker dependency in strict mode → this is intentional IaC for a framework whose runtime is containerized; local non-strict runs report an explicit skip if Docker is absent.

## Dependency map

```mermaid
graph LR
  QA[tools/dev/check.sh] --> Check[tools/specd/check.sh]
  Check --> Run[tools/specd/run.sh]
  Run --> Image[tools/specd/Dockerfile]
  Image --> Lock[package-lock.json]
  Run --> Config[specd.yaml]
  Config --> Schema[@specd/schema-std]
  Schema --> Change[context-driver-foundation]
  Change --> Living[default:context-driver/core-contract]
  ADR[ADR-0001 / ADR-0002 / research] -. evidence .-> Change
```

```text
┌────────────────────┐      ┌──────────────────────┐      ┌──────────────────┐
│ tools/dev/check.sh │─────▶│ tools/specd/check.sh │─────▶│ tools/specd/run.sh│
└────────────────────┘      └──────────────────────┘      └────────┬─────────┘
                                                                  │
                                     ┌────────────────────────────┼──────────┐
                                     ▼                            ▼          ▼
                           ┌──────────────────┐          ┌────────────┐ ┌───────────┐
                           │ Dockerfile+lock  │          │ specd.yaml │ │ Git worktree│
                           └────────┬─────────┘          └─────┬──────┘ └───────────┘
                                    │                          │
                                    ▼                          ▼
                           ┌──────────────────┐          ┌───────────────────────┐
                           │ official CLI     │─────────▶│ active SpecD change   │
                           └──────────────────┘          └───────────┬───────────┘
                                                                   ▼
                                                        ┌────────────────────────┐
                                                        │ living core-contract   │
                                                        └────────────────────────┘

┌──────────────────────────┐  evidence only  ┌────────────────────────┐
│ ADRs + coverage research │─ ─ ─ ─ ─ ─ ─ ─▶│ proposal/spec/verify   │
└──────────────────────────┘                 └────────────────────────┘
```

## Migration / Rollback

Deployment is repository-only: merge package/config/tooling/change artifacts and run canonical QA. No runtime or data migration occurs.

Rollback removes `package.json`, `package-lock.json`, `specd.yaml`, `tools/specd/`, the SpecD QA invocation and unarchived change/workspace artifacts. No Hermes, PostgreSQL or cloud data rollback is required.

## Testing

### Automated tests

- `docker build -f tools/specd/Dockerfile .`
  - proves exact package installation, native `lbug` loading and official CLI version.
- `npm audit` during dependency lock preparation
  - proves zero currently known vulnerabilities after exact overrides.
- `tools/specd/check.sh`
  - proves CLI version, project config, official schema resolution and complete artifact validation;
  - negative control: removing one required artifact MUST make the command non-zero, covering “Missing required artifact blocks acceptance”.
- `tools/specd/README.md`
  - documents the exact local/CI route, artifact review order, ignored runtime state and known Windows/GLIBC constraints.
- `bash -n tools/specd/*.sh` and ShellCheck
  - cover wrapper syntax and unsafe shell expansion paths.
- Existing `tools/state_db_backup` tests
  - retain acquisition receipt mismatch, immutable snapshot and sensitive-output evidence.
- Existing `tools/context_backend` tests
  - retain schema-drift, authority-contract, sensitivity and coverage-matrix evidence.
- Full `tools/dev/check.sh`
  - proves the new gate composes with all existing repository contracts.

Runtime scenarios for projection promotion, replay and canonical decoding that have no implemented runtime remain `UNKNOWN` after this change. Their named scenarios become mandatory acceptance targets for the changes that implement those capabilities.

### Manual / E2E verification

1. From the linked worktree run `tools/specd/run.sh --version`; expect `0.2.0`.
2. Run `tools/specd/run.sh project status --format json`; expect project root `/workspace`, schema `@specd/schema-std`, owned workspace `default` and one active change.
3. Run `tools/specd/run.sh changes status context-driver-foundation --format json`; inspect state, artifact DAG and blockers.
4. Run `tools/specd/check.sh`; expect every command to exit zero after all artifacts exist.
5. Confirm `git status` contains only intended repository artifacts and no `node_modules` or local identity/config files.
6. Run full canonical QA and record exact command/output in #288.
