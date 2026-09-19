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

**Two files, never one — and the launch file is paste-block ONLY.** The
PLAN (rows/steps/acceptance) and the LAUNCH PROMPT ("You are the …")
are separate committed files. The launch file's first line after its
title is `You are the …`; it contains no producer-side meta prose
("banked cold-launch prompt", "this file wraps the kickoff", "paste the
block below") and no authoring notes — the reader pastes the whole file
and reads every line as addressed to THEM. Producer meta goes in the
commit message or the orchestrator's own log. Earned 2026-08-16/17: a
worker booted from a launch file that opened with orchestrator-voice
meta and proceeded to act as the orchestrator — drafting and launching
another packet instead of executing its rows.

**Name the role's negative space.** After the reporting line, state
what the session is NOT: a worker does not emit packets, author
kickoffs, launch Codex/agent sessions, or schedule work; its outputs are
the task's artifacts plus its own handoff/Current-truth. "The plan is
already written in <file> — execute it; do not re-plan or re-emit it."

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
5. **Mailbox line.** Name the orchestrator's `ListAgents` session name
   and the doorbell rule (file first, ring second, pointer only —
   threads skill `references/codex-handoff.md` §Ambiguity mailbox).
   Names are session-bound: they go in launch prompts and
   question/handback frontmatter, never in the resume cache. State
   that escalation has ONE channel (the mailbox) — observed 2026-08-17:
   a worker rang the orchestrator AND asked the operator the same
   question interactively; two channels can return two rulings.
6. **Boundaries.** Escalation rules, reserved decisions, shared-resource
   rules (who holds the heavy-compute slot), and mechanical disciplines
   (staging, scanning, pre-commit checks). State them even though they're
   "known" — the successor only knows what's written.

## Skeleton

```
You are the <ROLE> session (<successor of X / worker owning thread Y>).
You own <thread/workspace>. You report to <orchestrator/user>; these
decisions are already made and NOT yours to reopen: <list>.
You are a WORKER, not the orchestrator: you do not emit packets, author
kickoffs, launch Codex or agent sessions, or schedule other work. The
plan is already written in <plan file> — execute it; do not re-plan or
re-emit it.

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

Mailbox: questions/handbacks are FILES first (the record), then ring
the orchestrator — SendMessage to `<orchestrator session name from
ListAgents>` with only `OPEN_QUESTION|HANDBACK <repo-relative path>`,
then end the turn; the answer's ring resumes you. Never put content in
a message.
If YOU answer a question (a coordinator answering a sub-worker), use
`~/.claude/skills/threads/scripts/answer_question.py` — never hand-flip
`status:`. Escalation has ONE channel: the mailbox to the orchestrator. Do NOT
also put the same question to the operator with AskUserQuestion — two
channels can return two different rulings; the orchestrator brings
user-level decisions to the operator and rings you ESCALATED meanwhile.

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
