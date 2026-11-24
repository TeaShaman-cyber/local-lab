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
