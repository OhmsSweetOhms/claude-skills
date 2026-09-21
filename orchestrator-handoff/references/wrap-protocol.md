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
Your boot surface is a thread's `## Current truth` block, and which one
depends on your role:

- a **worker** or **coordinator**: the Current truth of the thread you own;
- a **campaign or lane orchestrator**: the Current truth of your charter
  thread — your launch prompt names it;
- **never an orchestrator cache.** A cache holds pointers and decisions,
  never status; a ruling enters it in the commit that makes the ruling,
  not at wrap.

Full rewrite to present state: what is DONE (with commits), what is
ACTIVE, what is next, which rulings landed (pointing at where each one is
recorded), and your **watch items** — what the successor must verify or
expect, the suspicions you have not settled and how to settle them.
Delete every claim that describes the past ("X is pending" when X
shipped). Update the file's header/date metadata in the same edit. Keep
the block within its bound (8,192 B; the threads pre-commit guard refuses
one that grows past it). If you write nothing else before dying, this was
the right thing to have written.

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

**4. Then the wrap narrative (session-log entry, or a dated `SESSION-HANDOFF-*.md`).**
History only. Its allowed content: what happened; what was decided, with
commits; what the session got wrong; evidence pointers. Nothing
forward-looking — next moves and watch items went into the boot surface
in step 1, and a narrative is on no one's boot path. Point it AT the boot
surface ("Current-truth is current as of this wrap"), never the reverse.
A `SESSION-HANDOFF-*.md` file carries, in its first ten lines, exactly:

    > Immutable session narrative — history, not a boot surface.

and is never edited after its commit; the threads pre-commit guard refuses
a new one without that line and any later edit of one.

**5. Leave the successor pointer.**
Where the next session starts: the boot surface, the active plan, and —
if one exists — the banked launch prompt. If you have budget left, draft
the launch prompt yourself (see `launch-prompt-template.md`); you know
the mechanics better than anyone who will audit you.

**6. Put session-earned facts where they are found, not where they are read every boot.**
Before the final commit, write anything this session PROVED that a future
agent would otherwise re-derive at board/build cost — mechanisms, gotchas,
diagnosis patterns, measured numbers, retired approaches — into the hop's
`findings-*.md`. From there each fact reaches the owning **skill reference
chapter** when the hop closes (the threads skill's **Promote facts**
workflow), where a session loads it on demand. The project `CLAUDE.md` —
and any file every session reads at startup — gets at most a one-line
pointer, never the fact itself: a fact written there is paid for by every
future boot (one program's `CLAUDE.md` regrew 45 KB of facts in 16 days
this way). Facts that live only in findings files get found; facts that
live only in the transcript die. Scrub any claims the session refuted
(live docs reference current state).

**6a. Reconcile the work-order surfaces your boot protocol points at.**
The cache is not the only thing a cold successor reads. Walk your own
resume protocol's reading list — the playbook, the roadmap, the
forward-work table, whatever comes *after* the cache — and ask of each,
"is this still true after today?" A correct cache pointing at a stale
work order still produces the wrong next move, because the successor
boots exactly as instructed and lands there. Move finished work into the
done/history section with its measured results **and whatever it left
open**, and put the successors in its place marked drafted-and-unfired.
This is the step most easily skipped, because the cache edit feels like
the job is done.

**7. Commit, explicit paths, before doing anything else.**
A perfect wrap in the working tree is a wrap that doesn't exist. Run the
project's scans/checks; commit the boot surface + narrative together.
On shared checkouts, check `git diff --cached --name-only` first — a
concurrent session's staged work can race into your commit between your
status check and the commit itself; unstage theirs, commit yours,
restage theirs.

**8. End by handing the USER their next moves — with absolute paths.**
The final chat message of a wrap is a copy-paste-ready "what you do
next" list: the USER's reserved actions in order (merges to fire,
sessions to launch, packets to emit), each with the ABSOLUTE paths and
commands they need — session CWDs, launch-prompt files to paste,
worktrees, gate scripts. Absolute paths are correct HERE and only here:
this is chat guidance for the human at their own terminal, not a
tracked file. Sanitization applies to what gets committed, not to what
gets handed to the operator.

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
