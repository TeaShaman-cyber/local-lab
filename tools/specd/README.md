# SpecD in local-lab

SpecD is repository tooling for context-driver changes. The canonical runtime is Docker; do not install or invoke the CLI directly on the Windows host.

## Commands

```bash
# Build the pinned image if needed and run the official CLI
tools/specd/run.sh --version

# Inspect project/change state
tools/specd/run.sh project status --format json
tools/specd/run.sh changes status context-driver-foundation --format json

# Ask the schema what artifact comes next
tools/specd/run.sh changes artifact-instruction context-driver-foundation --format json

# Run the deterministic repository gate
tools/specd/check.sh
```

`tools/dev/check.sh` runs the same gate. `STRICT=1` treats missing Docker as a failure; a non-strict local run reports an explicit skip.

## Change workflow

1. Create or reuse the narrow GitHub issue required by `AGENTS.md`.
2. Create the SpecD change with `tools/specd/run.sh changes create ...`.
3. Preserve discovery in `specd-exploration.md`.
4. Follow `artifact-instruction` for proposal, specs, verify, design and tasks.
5. Validate each changed artifact in dependency order. When an upstream artifact changes, downstream artifacts become `pending-review` until explicitly revalidated.
6. Use `changes transition` only when `changes status` reports the transition available.
7. Run `tools/specd/check.sh` and canonical repository QA before review.
8. Archive only after implementation and verification are accepted; archiving materializes living specs under `specs/`.

## Boundaries

- `specd.yaml`, package manifests, active changes and archives are versioned.
- `.specd/config/` and `specd.local.yaml` are local runtime state and ignored.
- The Docker build pins the Node image digest and npm lockfile. Reviewed native install scripts run only in the build stage.
- Agent plugins and code-graph indexing are not enabled. They require separate compatibility decisions because project-owned `AGENTS.md` and `.agents/skills` must not be overwritten.
- SpecD implementation coverage and `tools/context_backend/coverage_matrix.yaml` source-schema coverage are different gates.

## Troubleshooting

- Native Windows SpecD `0.2.0` is not the supported route here: path normalization can misclassify `E:/...` as outside the repository.
- The image uses Debian trixie because `lbug@0.14.3` requires `GLIBC_2.38`; bookworm fails during CLI startup.
- To force a clean framework rebuild:

```bash
docker image rm local-lab-specd:0.2.0
tools/specd/run.sh --version
```
