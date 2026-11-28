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

## On-demand acoustic trace (R17 investigation)

Issue #289, iteration186 and current acoustic trace candidate.
From an interactive Windows console, intentionally start a short capture:
`python tools/thinkpad_fan/acoustic_windows.py --capture --seconds 15`.
No background microphone service, scheduler, automatic triggering, or fan
command. Keep microphone position fixed and remain silent during the test.
Default output is local time-resolved spectrum and 20-ms peak/RMS envelope;
`--save-wav` additionally retains the raw audible waveform (may contain
room speech). Do not upload either without explicit consent. Store under
D:/theseus-hardware/acoustic and remove manually when no longer needed.
Budget admission uses the existing warehouse check. Capture is shared-mode;
session checks are best-effort and cannot guarantee dictation compatibility.
WASAPI packet position/QPC, discontinuity flags and before/after UTC/tick
anchors are retained; timing-invalid captures are discarded. Mic level is
uncalibrated; spectral peaks are candidate mechanical orders, not RPM truth.
No EC traffic or deliberate spin-up. Native capture timing needs an actual
user-initiated quiet Windows pilot before operational acceptance.

For diagnostic capture on a supported endpoint, pass `--raw` to request
RAW audio processing for this session only (no automatic fallback, no
endpoint-wide setting change). Exclude flagged startup packets only before
the first accepted sample, for at most one second; later flagged packets
invalidate the capture. Exclude the first second from acoustic comparison
to avoid startup transients. `order_contrast` compares power around a
tachometer-derived rotational order to neighboring bins; a match at one
constant speed is a candidate, not proof of fan attribution. Repeat across
naturally occurring speeds. Do not use audio to control the fan.

For cross-speed calibration, use `stable_spectral_peaks(trace)` (exclude
initial 2 seconds and last 0.5 seconds) followed by
`fit_harmonic_spacing(frequencies_hz, minimum_hz, maximum_hz)`.
Require at least five distinct harmonic orders in the 300–3500 Hz band;
report a candidate spacing, not a measured shaft speed. Equal-support aliases
are ambiguous. A tachometer-derived search band is conditional evidence,
not an independent speed measurement. Compare multiple operating points:
a fixed-frequency peak may coincide with one rotational order by chance.
Never use the fit as a controller input.

## Periodic acoustic diagnostics (issue #289)

Opt-in seven-day trial, independent of the ETL task. Windows native WASAPI
captures 15 seconds every five minutes in the interactive user session;
Docker Desktop Linux does not own the microphone. Explicit installation:
`python tools/thinkpad_fan/acoustic_task.py --install` on the Windows host.
The installer pins the current default capture endpoint by ID; later default
changes do not redirect collection. An inactive or missing pinned endpoint
causes rejection. Keep the contention guard even on a dedicated device. Verify the endpoint is the intended laptop microphone
before installation. No elevation, wake, raw audio files or full spectrograms;
only local stable spectral peaks, coarse one-second band energies and clock
anchors. These derived features may still reflect room sound and must not be
uploaded without consent. Session detection is best-effort, not a guarantee
against concurrent voice capture. No global audio settings or fan control.
Storage admission checks both the warehouse budget and a 64 MiB local cap;
no automatic deletion. Last outcome is records/status.json; capture records
are atomically published as trace-<utc ns>.json. The expiry also gates
capture in code. Disable early via Task Scheduler (Theseus Fan Acoustic
Diagnostics); remove the task to uninstall, keep or manually delete local data.
Manual on-demand full spectral capture remains separate.

Optional read-only Docker summary:
`docker compose -f infra/compose/hardware-analysis/docker-compose.yml --profile acoustic run --rm acoustic`.
The container has no network or microphone, mounts only the compact records.


## CPU limit telemetry (#289)

The existing Windows warehouse ETL task also samples the signed IntelMSR
0.2.12 module from the previously verified local probe directory. Exact
module hash, Intel family 6 model 142, single processor group and eight
logical processors required. No MSR writes, sticky-flag clearing or EC access.
Every approximately two seconds during the 240-second ETL interval (not
gapless), collect package 606/610/611/1B1 and per-logical-processor
64F/19C/198/E7/E8 with start/end UTC and tick brackets. The raw register
order is fixed by schema 1; each core row begins with its logical CPU index.
The 64F live reason bits 10/11 are separate from sticky log bits 26/27.
Decode MSR package PL1/PL2 watts and enable flags; these are not a guarantee
of the effective MMIO/platform limits. APERF/MPERF and PERF_STATUS are raw
evidence, not measured MHz without interval calculations.

Each ETL segment gets a local cpu-limits.json sidecar; the same warehouse
writer imports cpu_limit_samples and cpu_limit_batches transactionally.
An unavailable/partial collection is explicit, not zero limits. Portable
snapshots optionally include both tables. Manual on-demand export still
uses hardware_snapshot.py when the writer is idle. Rollback: revert code;
existing historical records remain. No fan or power policy changes.

Capture requests a one-second per-stream buffer (actual size read back).
This provides headroom against scheduling delays, not a guarantee of lossless
capture. Packet discontinuity/timestamp errors still invalidate the trace;
status records flag, position, elapsed time, actual buffer and maximum
observed outer-loop interval, without retaining audio. Successful compact
records include capture-quality metadata. No global audio settings changed.

Compact periodic summaries also retain the five strongest 20 ms RMS blocks
and three strongest spectral frames per frequency band, as offsets and dB
above the median of the same trace. Exclude the initial two seconds and final
half-second. These are uncalibrated transient candidates, not a source
classifier; adjacent peaks can represent the same event. No full envelope,
spectrogram, audio waveform or speech reconstruction data are persisted.

Independent Intel package power-limit surfaces (model 8E): MSR 0x610 and
MCHBAR+0x59A0 are evaluated separately (Intel 10th Gen datasheet vol.2,
section 3.3.31). The R17 CtdpB SSDT's CTCN/CTCD explicitly write the latter.
The existing signed IntelMSR module remains unchanged. Optional read-only
IntelMCHBAR 0.2.12 (same official release; pinned SHA-256) reads only the
package limit and unit registers, with independent acquisition timestamps
and power-unit agreement. No register writes, no lock changes, no EC I/O.
Stage the pinned module at E:/tpfan-qa/msr-probe-289/IntelMCHBAR-0.2.12.bin.
On load/read/validation failure the MMIO sample is unavailable, never zero;
MSR collection continues. New rows are in cpu_mmio_limits keyed by the
same batch_sha/sample_index; legacy samples have no corresponding row.
Both surfaces remain separate in DuckDB and portable snapshots. Existing
240s/5min acquisition schedule unchanged, not continuous coverage.

## R17 regulator diagnostic row candidate (not scheduled)

Issue #289, iterations 62/118/145/192/227. The selector-56 row exposes
target RPM, processed tach, output PWM and upper/lower PWM bounds on
the observed R17ET45W firmware. Two readings are compared field by field;
even identical fields are not proof of atomicity or physical RPM. Original
firmware replay demonstrated torn rows at 8 of 11 tested update boundaries.

The one-shot Windows adapter is deliberately separate from the recurring
warehouse task. It requires exact L14 20U2/R17ET45W, the verified signed
existing module, recent acknowledged level-1 polls and CPU below 78 C
(provisional pilot guard, not a change to the configured 85 C control policy).
It uses the existing shared lock with no wait, only the known row, strict
response readiness, no drain/layout fallback or retry, a 900 ms admission
budget and 600 ms restoration admission budget. These are soft budgets;
native driver calls are not guaranteed to return by a deadline. Restore
the selector only when transaction synchronization is known and the page
is still ours. Uncertain state or a process crash leaves a persistent marker,
which must not be removed automatically. A failed restoration cannot be
guaranteed safe by software alone.

The warehouse passively imports immutable records from its local
r17-row-spool, including failed/unknown records, never calls the sampler.
Parquet exports preserve source, raw words, acquisition interval, quality
and accepted fields. No raw observation leaves the machine. Do not schedule
or enable the sampler until independent review and a supervised matching
Windows pilot have verified page restoration, acquisition time and impact
on existing Smart polling. Tests use fake port/transport and real DuckDB;
the original firmware replay is separate evidence, not a native-driver test.
