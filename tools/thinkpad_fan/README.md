# ThinkPad L14 Gen 1 fan investigation

Scope and acceptance: [local-lab #289](https://github.com/TeaShaman-cyber/local-lab/issues/289).
Own Windows fan curve, retain PSC7 performance, avoid whine, approximate 85 C upper
target to be validated, no firmware/BIOS modification. Lenovo factory fan selection
is not an invariant. This PR is a narrow CQL tool, not the fan controller.

The original one-shot DYTC command and independent GET were verified on 20U2
([receipt](https://github.com/TeaShaman-cyber/local-lab/issues/289#issuecomment-7143293286)).
This revised implementation has not been live-tested. Default mode is GET only.
The explicit --clear-once flag clears the active CQL state, not automatic reactivation.
A transport failure leaves the result unknown: inspect GET before any further action.
No autostart or persistent policy change; firmware can reassert CQL on a later event.
Do not use this tool as a permanent override. The original active state can be
restored by Lenovo's own CQL event; manual inverse command is documented in the receipt,
not exposed by this CLI.

Run from the repository root on the matching Windows machine:
python -m tools.thinkpad_fan.cql (read only).
Tests: python -m pytest tools/thinkpad_fan -q (fake device; no hardware).
Rollback of code: revert this PR; no service or installed component is created.
Prior experimental scripts and logs remain evidence only, not deployment candidates.

## Offline curve candidate

Issue #289 also tracks user-defined smooth fan behavior. The pure Python
`curve.py` follows the linear interpolation idea in
[tpnsfand](https://github.com/h4fnp/tpnsfand/tree/132c6e2adabc381445ce17babc7c738742020c22),
not that model's register protocol. `target_rpm` produces a continuous target;
`next_level` models the fallback for a calibrated discrete actuator using
RPM midpoint hysteresis and at most one adjacent level per evaluation.

No default curve, calibration, polling interval, hardware writes, daemon or
temperature policy is provided. Tests use synthetic values only. A clamped
curve endpoint is not a thermal protection mechanism. The model cannot establish
smooth physical speed, cooling capacity or PSC7 performance; actual stable RPM
measurements, acoustic response, time-domain behavior and independent temperature
observations are required before selecting live settings. If levels are not
distinct and monotonic, the discrete model rejects them rather than guessing.
Rollback: revert these source files; no runtime changes.

## Calibration-only profile candidate

`python -m tools.thinkpad_fan.calibration_profile SOURCE NEW_DESTINATION`
derives a separate INI from the current passive TPFanCtrl2 configuration.
It refuses to overwrite any existing file. It starts in Auto (Active=1),
preselects manual level 5, and makes both Smart profiles return Auto at any
valid positive temperature. This makes the application's manual-temperature
exit an experimental handback, not a permanent factory-policy requirement.
The observed TPFanCtrl2 v2.7.0 engine checks ManModeExit with strict greater-than;
85 C is the configured secondary limit, not a guarantee of an exact 85 C stop.
Independent DPTF monitoring and a bounded trial with verified restoration are
still needed. No installation, restart, command or manual activation here.
Source reference: TPFanCtrl2 3acd20ca52c018f30e3a2d376bbe9bd3d1cb52f8,
fancontrol.cpp OnTimer, fanstuff.cpp SmartControl/SetFan, misc.cpp INI parser.
Rollback: remove the new candidate file; the original is unchanged.

## Raw tachometer calibration (read only)

`python -m tools.thinkpad_fan.tach 5` samples the existing v2.7.0 shared
engine state; it neither changes mode nor opens PawnIO. Output includes every
distinct observed raw frame and, if stable, a median RPM with accepted/rejected
counts. The upstream CSV can substitute previous RPM values for bad readings,
so it is not independent tachometer confirmation. Two identical memory reads
reduce, but cannot eliminate, torn-frame risk (upstream has no seqlock).
Provisional criteria for the tested 4/5 band: ten observations over at least
eight seconds, no command/mode change, no stale interval over three seconds,
at least 80% valid within 2500–6500 RPM, most recent sample valid, valid
range at most 150 RPM. These are measurement criteria, not a control policy.
No invented values, no automatic fan command. Thermal supervision is separate.

## TPFanCtrl2 transport diagnostic candidate (not installed)

Pinned upstream v2.7.0, commit 3acd20ca52c018f30e3a2d376bbe9bd3d1cb52f8.
Research and upstream discussion: [#289](https://github.com/TeaShaman-cyber/local-lab/issues/289)
and [upstream #2](https://github.com/mews-se/TPFanCtrl2/issues/2).
The patch in `upstream/checked-transport.patch` adds explicit PawnIO operation
results to the ordinary EC mailbox path, enables strict OBF for this diagnostic
build and removes extra I/O from failure logging. A valid FF byte is distinct
from a failed read. It is intentionally not a complete fan-controller patch.

For an isolated, disposable checkout of that exact upstream commit, run
`python tools/thinkpad_fan/upstream/check_transport.py UPSTREAM_CHECKOUT`
from local-lab (Linux, g++ available). Before patching it must fail on the
known faults; use `git apply --check` and `git apply` with the absolute patch
path from the disposable upstream checkout, then rerun the harness.
The harness extracts actual C++ functions and simulates the PawnIO boundary:
delayed/absent OBF, valid FF, and failure at each ordinary read/write step.
It performs no EC I/O; it is a separate offline diagnostic, not the Windows build
or the regular Python QA gate. To undo, discard the disposable checkout.

Still unresolved: compatibility of strict OBF on the L14 LPC channel,
automatic layout retries after uncertain transactions, higher-level SetFan
acknowledgement and selector writes, extended-sensor legacy wrappers, TVicPort
(no per-operation error reporting), live thermal supervision and physical RPM.
Do not deploy or start this candidate; review and Windows build are required
before any separately governed live experiment.


## Diagnostic file export

`snapshot.py` is a read-only file collector, not a hardware sampler. See [host monitoring](../../docs/reference/observability/grafana-host-monitoring.md) for the deployed task and Loki streams. Run `python -m unittest discover -s tools/thinkpad_fan -p test_snapshot.py` for checkpoint, partial-line, and rotation tests. Do not use Alloy to hold active TPFan CSV/log files open.
