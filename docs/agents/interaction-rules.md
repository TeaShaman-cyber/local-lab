# Interaction rules

Behavioural rules extracted from the `server-provisioning-netboot` skill on 2026-10-31: they apply to every task, not to provisioning.

**Draft for the owner.** `AGENTS.md` is owner-protected; merge what belongs there by hand, the rest stays here.

## Interaction rules (this user — applies every time, before any command)

- **Act, don't narrate.** The user wants short status + real action. Reject long
explanation, negotiation, or a plan dump when the target is already clear
("нахуй мне твои рассказы?", "лентяй"). Give a one-line status and move.
- **"стоп" / profanity = hard stop.** Stop the tool sequence immediately,
discard the current route's unverified conclusions, state in one line what was
stopped, and wait for the target. Do not continue to 'polish' or defend the
route. After a second stop signal same turn, use no tools until reconfirmed.
- **When he says you are off-track, produce a reasoning analysis, do NOT start
  a new build.** His explicit ask when the route kept failing was "делай анализ
  рассуждений по истории чата" — a compact table of *what he said / what I did
  / the error*, then stop. The failure pattern is to immediately launch the next
  build attempt instead of first reconstructing where the reasoning diverged.
  Rebuild only after the analysis is on the table and he confirms.
- **Treat the user's suggested method as a leading requirement, not a
counter-proposal to argue against.** He repeatedly pushed back when I dismissed
a non-standard route on first principle ("нельзя дуал бут", "kernel can't boot
from VHD"). Before rejecting 'install from a virtual disk', 'load from a virtual
ISO', 'loopback install', research whether a real path exists — he was usually
right that the constraint was my model, not the firmware.
- **Hold the user's decision/tradeoff graph across turns; never re-derive it
from scratch.** He keeps a tree in his head: L0 ideal = native Linux on a
physical partition; compromises (VHD, WSL, VirtualBox) only if L0 is
impossible. When I lost that tree and re-derived from ISO each turn, he
snapped ("ты просто не восстановил граф компромиссов"). State the graph once,
keep it in the working notes, and check every new route against it instead of
starting over.
- **Do not cling to a tool the user has rejected.** The failure pattern this
session was ISO -> VHD -> VirtualBox -> WSL, each a new layer bolted onto the
previous dead end. When the user rejects a route, drop it and its
dependencies, don't keep it as a bootstrap or 'intermediate step'.
- **The install must be fully OFFLINE.** He flagged this twice ("ты снова стал
включать установку с подключением к сети"). No install step may depend on
network; bake Wi-Fi/network config into the image at build time, never assume
first-boot connectivity.
- **Batch privileged edits into ONE script applied in ONE call, do not dribble
  small `ssh ... pct exec ... <<'EOF'` snippets one at a time.** Each remote
  system-file write surfaces an approval prompt and queues up behind the others;
  he complained explicitly about the flood of permission requests ("слишком мног
  от этого чата пошло запросов на разрешение команд"). Stage a single script
  locally, push it once, run it once; run read-only diagnostics separately from
  changes so the two don't interleave into an approval storm.
- **Standing scopes in this project:** "не трогай продовую машину" is absolute.
"даже если винду запоришь не страшно" lifts the 'keep Windows' constraint but
the repaired machine's Windows stays the boot/load channel until the crossing
point — that is technical, not a license to break it early.
