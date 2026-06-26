# Hand-off — {{TITLE}}

**Thread:** {{ID}}
**Last updated:** {{LAST_UPDATED}}

This is a rolling session-to-session journal. Entries are
reverse-chronological (newest at top). Updated **whenever the
plan changes** (New-plan-hop and Close-thread workflows re-check
the forward-looking sections below and append a new session-log
entry) AND on user request for in-session notes. For the stable
structural overview see [`README.md`](README.md); for point-in-time
snapshots at hop closures see `findings-*.md`.

## Current truth  (overwrite each hop — do NOT append; history → Session log + git)

This block is the **only** forward-facing state. It is **bounded** (keep it short)
and **overwritten** every hop — never grown into a changelog. When a claim dies it
**leaves** the block; its death is recorded once in the Session log, not carried
forward here. Git holds every prior version, so overwriting loses nothing.

- **Focus:** {{CURRENT_PLAN}} — {{CURRENT_PLAN_STEP}}
- **PROVED:** {{LIVE_PROVED_CLAIMS}} *(each with an evidence pointer: findings doc / commit / capture)*
- **OPEN:** {{LIVE_OPEN_CLAIMS}} *(competing live hypotheses + the experiment that decides between them)*
- **NEXT:** {{NEXT_ACTION}}
- **Baseline (green):** {{GREEN_BASELINE}} *(suite + pass/fail + wall time + timestamp)*
- **RULED OUT (do not re-run):** {{REFUTED_DEAD_ENDS}} *(one-liners — the cheap graveyard that stops re-litigation)*

## Reading order for a cold start

1. `thread.json` — machine-readable status, plan hops, findings
   index.
2. This file's "Current truth" block above (bounded; the only live state). The
   Session log below is dated history — read newest-first, stop when oriented.
3. `README.md` — plan lineage, findings table, research linkage.
4. The current active plan file (`{{CURRENT_PLAN}}`).
5. Most recent `findings-*.md` if one exists.
6. `git show <ref>` for the most recent commit touching this
   thread, if immediate history matters.

## Session log (newest first)

### {{TODAY}} — thread initialized

{{INITIAL_CONTEXT}}

<!--
Append new entries above this comment. Each entry:

### YYYY-MM-DD [optional HH:MM] — <short topic>

<what happened, what decisions got made, what the next session
needs to know. Prose, not bullet-formatted unless a list is
genuinely clearer. Include measurement numbers with timestamps
when relevant.>
-->
