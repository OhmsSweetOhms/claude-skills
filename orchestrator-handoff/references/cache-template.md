# Resume cache template (annotated)

The cache is the single file a cold orchestrator reads first and INSTEAD
of bulk-reading the program. Two invariants make it work: it contains
**pointers and decisions only** (anything that describes a moment in time
belongs in worker handoffs/registries and gets polled), and it is updated
**in the same commit** as whatever change it reflects.

Copy the skeleton, delete the annotations.

```markdown
# Orchestrator cache — <program name> (durable resume surface)

**What this is:** the top-level orchestrator's knowledge index.
POINTERS + DECISIONS ONLY — live status is NEVER cached here (it
rots). Updated IN PLACE when a decision changes or an artifact lands,
same commit; history in git. Dated SESSION-HANDOFF files are immutable
narratives; this file is what a cold orchestrator reads FIRST and
INSTEAD of bulk reading.

## Resume protocol (cold session)

<!-- Numbered steps: read this file; the exact poll command(s) for
     live status; which handoff blocks to read; the delegate-deep-reads
     rule. Make step 1 "do NOT inline-read the artifacts below yet" —
     cold sessions over-read by default. -->

## Live workers (poll, don't cache)

| Handoff (read current-truth only) | Role |
|---|---|
<!-- One row per live worker. The Role column is DURABLE framing
     ("scenario sweep worker", "PL coordinator — successor boots from
     rewritten current-truth + banked launch prompt"), never progress
     ("pass 1 running"). When a worker finishes but its close isn't
     verified, the row carries the verify flag rather than vanishing:
     "close authorized <date> — VERIFY it executed, then drop row". -->

## Knowledge index (read via subagent, on demand)

<!-- Pointers to charters, kickoffs, research reports, key findings —
     each with a one-line "what's in it" so a successor knows what to
     ask a subagent for. Include a "never re-derive" list: findings
     whose re-derivation would waste a session. -->

**Session narratives (immutable, read only if the cache confuses):**
<!-- List the dated SESSION-HANDOFF files, newest last, each with a
     three-word scope note. -->

## Decisions in force (stable until explicitly changed)

<!-- Numbered list. Numbers are append-only addresses — never renumber
     or reuse (other docs cite them). Each entry: the ruling AS WORDS
     (never as an option-letter — labels collide across documents),
     who decided (user vs orchestrator) and when, one line of why, and
     provenance pointers. When a decision is superseded or completed,
     amend it in place with the update rather than deleting.

     Include one entry that IS the decision ladder: which decision
     classes are reserved for the user, which are orchestrator remit.
     Successors inherit the boundary instead of guessing it. -->

## Update rule

Add/adjust a pointer or decision the moment it lands, in the same
commit as the change it reflects. Never add status lines ("X is
running/paused") — that class of fact lives in handoffs and the
registry only.
```

## Maintenance rules

- **Same-commit discipline.** The cache edit rides the commit of the
  decision/artifact it records. If you post a ruling note into a worker
  handoff, the cache amendment is in that commit.
- **Amend, don't churn.** When a decision evolves (a hold becomes a
  declaration, a directive becomes mechanically enforced), amend the
  existing numbered entry with the dated update. The number is the
  stable address; the content carries the history's endpoint.
- **The table is the only quasi-live part.** It names WHAT to poll, and
  it may carry verify flags for unconfirmed transitions — that is the
  full extent of allowed liveness.
- **Self-audit trigger.** Any time you're asked (or wonder) "is this
  durable?", diff your session's rulings against the decisions list,
  and the table against the actual registry. The two gaps you'll find:
  a ruling that lives only in chat, and a table row describing last
  week.
