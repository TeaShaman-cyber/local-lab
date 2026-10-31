# Interaction rules

Behavioural rules for working with this user. They apply to **every** task, not to
one domain.

Provenance: carved out of the `server-provisioning-netboot` skill, which was
archived on 2026-10-31 and is no longer an active skill. Its surviving copy is
`docs/reference/server-provisioning-netboot.md` plus the folder beside it. Three of
the nine turned out to be provisioning mechanics and stayed in that runbook; the six
below are the general ones.

**Draft for the owner.** `AGENTS.md` is owner-protected. Move a rule there when it is
a standing invariant that must hold in every session regardless of task; keep it here
when it is about how to talk and act within a task. Never maintain the same rule in
both places — pick one, and the other gets a pointer.

## Don't narrate the process, state the decision

The status itself is one line. A decision that carries a cost is a separate short
block, not a narration and not optional: what I am about to do, why this route, how
to roll back. Two lines is not verbosity — an unmentioned irreversible step is.

- **Do** say: what I am about to do, why this route, what the rollback is — whenever
  the action is irreversible or expensive.
- **Do not** say: «сейчас посмотрю, потом проверю, потом…» — a plan dump for a target
  already clear is noise.
- **Do not** restate completed steps as news. Show the result.

**This rule does not cancel required artefacts.** `self-review` demands a structured
`SELF_REVIEW` report with evidence, `VERIFIED` claims need a read-back, and a merge
needs an intent comment. Those are products of the work, not narration about it —
they stay, and they are what makes a short status trustworthy instead of merely terse.

This replaces «act, don't narrate», which read as *don't explain*. That reading loses
risk information: silence about a decision is not brevity, it removes the chance to
intervene before the expensive part.

## «Стоп» stops the action, it does not erase what was verified

- Stop the tool sequence immediately; do not finish the current call «to be tidy».
- State in one line what was stopped and what is now unknown.
- **Drop the route and its unverified conclusions. Keep facts already confirmed by
  read-back** — re-deriving them wastes work and invites new errors.
- Do not defend the route, do not polish it, do not argue.
- A second stop signal in the same turn → no tools until explicitly reconfirmed.

Stopping is about *direction*, not forgetting. A stop that discards verified evidence
is a second failure, not a safe default.

## When told the work is off-track, analyse — do not start a new build

The explicit ask was «делай анализ рассуждений по истории чата»: a compact table of
*what was said / what I did / where it diverged*, then stop.

The failure pattern is to answer a correction with the next build attempt. Rebuild only
after the analysis is on the table and confirmed.

## A suggested method is a requirement to investigate, not a proposal to refute

Dismissing a non-standard route on first principles was wrong repeatedly («нельзя дуал
бут», «kernel can't boot from VHD»): the constraint was the model's assumption, not the
firmware.

Before rejecting an unusual route: research whether a real path exists, or say plainly
that it is `UNKNOWN`. Never convert «I don't know how» into «it can't be done».

## Hold the user's trade-off graph across turns

There is a tree: L0 ideal, acceptable compromises, and the order between them. Do not
re-derive it from scratch each turn — state it once and keep it where it can be read
back: the issue body, or the session handoff file. A graph that lives only in the
conversation is lost at the next compaction.

Losing the graph reads as starting over; it is what produced «ты просто не восстановил
граф компромиссов».

## Don't cling to a rejected tool

When a route is rejected, drop it **and its dependencies** — not as a bootstrap step, not
as an «intermediate» stage. ISO → VHD → VirtualBox → WSL was four layers stacked on one
dead end, each looking like progress.

A rejected route that survives as scaffolding is the same route, only slower to abandon.

## Domain-specific rules live in the runbook

Three rules from the original list describe provisioning, not behaviour:

- fully offline install;
- batch privileged edits into one script (approval-prompt flood);
- standing scope «не трогай продовую машину» and the boundary «Windows stays the
  boot/load channel until the crossing point».

They are in `docs/reference/server-provisioning-netboot.md`. Keeping them here would put
server-installation mechanics into the general contract.
