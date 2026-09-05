---
name: handback
description: Close out work with a state-complete handback — the PRODUCER side of ending a packet, plan hop, thread, fleet round, or standalone work stage. Use this skill whenever work is being closed, wrapped, banked, or handed back — "write the handback", "close out this packet", "close the plan hop", "close the thread", "wrap this stage", "bank the results", "write the findings close", "the packet is done" — and even when the user just says work is finished and asks what's next, since finishing IS the trigger. Every handback is the same three moves (STATE block, narrative, bank it); this skill is the single encoded format that replaces months of drifted closeout convention. Do NOT use for booting a session from a handoff (orchestrator-handoff owns that), and when a stricter contract owns the artifact — the Codex handback JSON (threads skill), the ordered session-wrap protocol (orchestrator-handoff), a fleet script's return schema — follow that contract and apply this skill's STATE discipline inside it.
---

# Handback

Closeouts drifted for months because no format was ever ruled: gate
tables in one thread, prose suites in another, a retrospective template
invented in a single afternoon. This skill ends the drift. From now on
every handback — packet, plan hop, thread, fleet round, standalone stage
— is the same three moves. Existing closeout files are grandfathered
untouched; this binds everything new.

A handback exists so that a reader who was not in the session can trust
the state without re-deriving it. Everything below serves that reader.

## Move 1 — the STATE block (first, always, fixed fields)

Machine-checkable, before any prose. A handback without this block is
not done, whatever the narrative says.

```
## STATE
- Tree: <git status --porcelain output verbatim — empty, or every line explained>
- Suites: <ran / passed / failed / SKIPPED-and-why, with the exact invocation>
- Gates: <each: PASS / FAIL / DEFERRED-by-whose-ruling, with evidence path>
- Evidence: <each artifact: path + git check-ignore -v result>
- Open: <each open item: one line + the single command that would resolve it>
```

Field rules, each earned:

- **Tree** is pasted, not summarized — "clean" is a claim; porcelain
  output is evidence. Unexplained lines are the finding, not noise.
- **Suites** distinguishes ran from passed from skipped. A suite that
  didn't run is SKIPPED with a why; silence about a suite reads as
  "passed" to the next session and that lie compounds.
- **Gates** names who deferred what. "DEFERRED" without a ruler invites
  re-litigation; "DEFERRED by operator ruling <date>" closes it.
- **Evidence** proves artifacts survived: a log that .gitignore ate is
  evidence that exists only in the dead session. `git check-ignore -v`
  per artifact catches it while it's still recoverable.
- **Open** items each carry their resolving command so the successor
  starts with an action, not an investigation.

## Move 2 — the narrative (second, never instead)

What happened and why it matters, written for the cold reader. Every
claim cites the command output or file:line that proves it; anything
uncited is labeled `UNVERIFIED:` with the command that would confirm it.
Refuted or killed work is a full deliverable — report it with the same
evidence rigor as a success; a cleanly killed hypothesis saves the next
session a board cycle.

What's closing sets the emphasis, not the format:

- **Packet / plan hop** — gate verdicts and what they unblock,
  discoveries, follow-ons each ROUTED (to a thread, plan, or operator
  ruling), worktree/branch disposition (pushed, unpushed-and-why,
  merge-back timing).
- **Whole thread** — the retrospective questions, in order: what the
  thread set out to do; what actually happened; why it closed rather
  than parked; what was ruled out / is now known; what debt leaves with
  it; what would justify a respawn.
- **Standalone stage** (no threads workspace) — flip the handoff doc's
  remaining-work ledger: done items become dated `Done` lines carrying
  the measured numbers; pending items keep their exact commands; the
  banked-artifacts section gains the new evidence homes.

## Move 3 — bank it (or it didn't happen)

- **Append-only.** A new snapshot file, never an edit of a prior one —
  in a threads workspace: `findings-<YYYY-MM-DD>-<scope>-close.md` in
  the thread dir (the ruled naming), plus the session-log entry in the
  thread's handoff and the thread.json update. In a standalone repo:
  the handoff document update IS the banking.
- **Boot surface updated in the same pass.** The classic succession
  failure is an accurate closeout beside a stale current-truth block —
  the cold reader boots from the stale one. Whatever surface the next
  session reads first gets updated in the same commit as the handback.
- **Sanitized.** No absolute paths or usernames in anything committed;
  repo-relative paths or placeholders. Fingerprint scan before
  thread-dir commits where the project requires it.
- **Committed, explicit paths.** Chat-only handbacks don't survive the
  session. Stage the handback files by explicit path (concurrent
  sessions share these trees), commit with the packet/thread id in the
  subject.

## When a stricter contract owns the artifact

Three closeout lanes already have ruled contracts. They win; this
skill's discipline rides inside them, it never competes:

- **Codex worktree handback** → the threads skill's handback JSON schema
  and template, then its triage flow. The STATE fields map onto the
  schema's own (gate verdicts, blockers[], regression baseline).
- **Session wrap / succession** → orchestrator-handoff's ordered wrap
  protocol (boot surface first, narrative second). Its order IS Move 3's
  same-pass rule, applied session-wide.
- **Fleet returns** → the fleet script's return schema; agents return
  data, the orchestrator banks. The orchestrator's banking pass is a
  normal handback under this skill.

If none of those apply, there is no routing question — do the three
moves.
