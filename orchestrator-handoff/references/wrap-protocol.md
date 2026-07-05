# Wrap protocol — for the session that is running out of context

This is the checklist for the DYING session itself (worker, coordinator,
or orchestrator), the moment it decides to wrap. It is ordered by what
survives if you run out of budget partway through — do the steps in this
order, not the order that feels natural.

The natural order is wrong: under context pressure, sessions write the
fresh narrative first (it's what's in their head) and "reconcile the old
state block later" — and later never comes. The result is an accurate
wrap entry nobody's reading order points at, sitting under a stale boot
surface everybody's reading order points at. Successors boot from the
stale block. Invert the order and the failure inverts: if you die
mid-wrap, the boot surface is right and only the narrative is missing —
the survivable failure.

## The order

**1. Overwrite the boot surface FIRST (current-truth / state block).**
Full rewrite to present state: what is DONE (with commits), what is
ACTIVE, what is next, what rulings landed. Delete every claim that
describes the past ("X is pending" when X shipped). Update the file's
header/date metadata in the same edit. If you write nothing else before
dying, this was the right thing to have written.

**2. Capture in-flight work explicitly.**
Anything running or unprocessed — recons, subagents, background jobs,
half-triaged results — either process it into the boot surface now or
write it as an explicit re-commission line ("a read-only recon was in
flight — NOT processed; re-run it"). Untracked in-flight work doesn't
pause when you die; it vanishes.

**3. State uncommitted state honestly.**
One line: what of yours is uncommitted, or "nothing of mine — all
committed (last: <hash>)". The successor cannot tell your intentional
working tree from another session's debris without this.

**4. Then the wrap narrative (session-log entry).**
The story: what happened, what was decided, evidence pointers. Point it
AT the boot surface ("Current-truth above is current as of this wrap"),
never the reverse — the narrative is backfill, the boot surface is the
product.

**5. Leave the successor pointer.**
Where the next session starts: the boot surface, the active plan, and —
if one exists — the banked launch prompt. If you have budget left, draft
the launch prompt yourself (see `launch-prompt-template.md`); you know
the mechanics better than anyone who will audit you.

**6. Commit, explicit paths, before doing anything else.**
A perfect wrap in the working tree is a wrap that doesn't exist. Run the
project's scans/checks; commit the boot surface + narrative together.

## Signals you should wrap NOW, not "after this next thing"

- You're summarizing your own earlier work to yourself.
- Tool results are being truncated or you're re-reading files you've
  already read.
- The "one more task" you're eyeing has multi-step tool fanout.

Wrapping one task early costs a session boot (~minutes). Wrapping one
task late costs a stale boot surface and a successor who trusts it.

## Relationship to the audit

`succession-audit.md` is this checklist inverted — it's what the
orchestrator checks because dying sessions skip steps. Every audit
finding maps to a step above: boot-surface contradiction → step 1
skipped; lost recon → step 2; ambiguous working tree → step 3;
reading-order miss → step 4's pointer direction. Do the steps and the
audit finds nothing.
