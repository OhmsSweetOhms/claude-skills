# Launch prompt template (worker or successor session)

A launch prompt is a committed file, not a chat message. Chat evaporates;
if the user loses the message, the prompt is gone and the successor boots
from a verbal summary. Bank it in the worker's thread/workspace dir
(e.g. `successor-session-launch-prompt.md`) and point the cache's
live-workers table at it.

Sanitize before committing: no absolute paths (`$WORKBASE/...` or "the
main checkout"), no usernames, no machine-local details — privacy guards
will catch some of this, but the deeper reason is that the successor may
run from a different checkout.

## Why each section exists

1. **Role + reporting line.** Sessions drift toward re-deciding things.
   Naming what is ALREADY DECIDED and not theirs to reopen ("the freeze
   is declared — do not re-litigate it") saves an entire wasted
   exploration arc.
2. **Boot sequence.** Cold sessions over-read. "Read exactly these, in
   this order, and do NOT bulk-read anything else first" is the highest
   token-leverage sentence in the prompt. Route deep reads to cheap
   subagents.
3. **Active task with mechanics pre-reconned.** Every fact you already
   know (branch names, entry points, which script is smoke-only, which
   venv to use, known gotchas) that you leave out, the successor
   re-derives at full context cost — or worse, gets wrong.
4. **The queue.** Without "what comes after", sessions stall or freelance
   when task #1 finishes.
5. **Boundaries.** Escalation rules, reserved decisions, shared-resource
   rules (who holds the heavy-compute slot), and mechanical disciplines
   (staging, scanning, pre-commit checks). State them even though they're
   "known" — the successor only knows what's written.

## Skeleton

```
You are the <ROLE> session (<successor of X / worker owning thread Y>).
You own <thread/workspace>. You report to <orchestrator/user>; these
decisions are already made and NOT yours to reopen: <list>.

Work from <checkout placeholder>. Refresh first — you need <ref/commit>.

Boot exactly like this — do not bulk-read anything else first:
1. Read <boot surface> — <what it is, and whether it's current>.
2. Read <plan/charter>.
3. Deeper reads (findings, ADRs, research) go to a cheap read-only
   subagent with a specific question — never inline.

ACTIVE TASK (priority #1): <task>. <Pre-reconned mechanics: branches,
entry points, formats, known traps.> RESOLVE FIRST: <any open question
that gates the task, with the recon already scoped>.

Then the queue, in order: (2) <...>; (3) <...>; (4) <...>.

Boundaries: <escalation rules — what stops work and goes up>.
<Reserved user decisions — bring drafted, never decided.>
<Shared-resource rule.> <Disciplines: staging, scans, checks.>
<Reporting: update your current-truth block at every hop transition —
the orchestrator reads it instead of your transcript.>
```

## Successor-specific additions

When the prompt launches a successor of a dead session:

- Say explicitly whether the boot surface is trustworthy ("Current-truth
  was rewritten at succession, <date>; it IS current") — otherwise the
  successor wastes time cross-checking it against the log.
- Point at the predecessor's wrap entry as narrative backfill, not as
  the boot surface.
- Re-commission anything that was in flight and lost ("a read-only recon
  was in flight at wrap — NOT processed; re-run it") rather than letting
  it silently drop.
- Defuse known ambiguities inline (e.g. "the ruling is ARRAYS — the
  word, not a letter; two write-ups letter the options differently").
