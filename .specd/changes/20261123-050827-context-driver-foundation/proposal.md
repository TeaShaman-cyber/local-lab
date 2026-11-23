# Proposal: context-driver-foundation

## Motivation

Context-backend decisions and evidence currently span #230, ADRs, research notes and implementation PRs. The project needs an executable SpecD lifecycle so future context-driver work preserves human intent, separates requirements from implementation, and fails closed when required artifacts are missing.

## Current behaviour

The repository has accepted source-boundary and high-fidelity-mirror ADRs plus deterministic schema-coverage tooling. It has no SpecD project configuration, pinned framework runtime, active change lifecycle, living specs, or canonical QA gate for SpecD artifacts. Long-form discovery context is therefore easy to collapse into an incomplete ADR or session summary.

## Proposed solution

Adopt the official SpecD CLI and `@specd/schema-std` through a pinned Docker runtime; establish an owned default workspace; create the first living context-driver contract; preserve discovery in `specd-exploration.md`; and add structural validation to repository QA. Defer agent-plugin generation and code-graph indexing until their interaction with the existing project contract is reviewed.

## Specs affected

### New specs

- `default:context-driver/core-contract`: normative authority, evidence, projection, privacy and failure semantics for future context-driver changes.
  - Depends on: none

### Modified specs

None.

## Impact

- New repository tooling: `package.json`, `package-lock.json`, `specd.yaml`, `tools/specd/`.
- New SpecD workspace and active change artifacts under `specs/` and `.specd/changes/`.
- `tools/dev/check.sh` gains a structural SpecD gate.
- No Hermes hooks, live databases, PostgreSQL data, cloud targets, agent authority or runtime configuration change.

## Technical context

The host is Windows and native SpecD `0.2.0` currently fails repository-root validation because Git emits forward-slash paths while Node resolves backslashes. Docker is the canonical route for local and CI execution. The image pins Node by digest, installs exact framework packages from `package-lock.json`, allows only reviewed native install scripts, and proves `specd --version` during image build.

Hermes `state.db` remains source authority. Verified immutable snapshots are accepted evidence; private mirrors are source-specific and non-replayable; runtime-neutral canonical records are explicitly decoded; consumer projections are read-only, disposable and rebuildable. This change records those requirements but does not implement a new runtime slice or choose Python, Go or Rust.

## Open questions

None that block this foundation change. Language selection, code-graph adoption and the next decoder slice are separate follow-up changes governed by the resulting SpecD workflow.
