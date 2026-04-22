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

## Current state

- **Active plan:** {{CURRENT_PLAN}} — {{CURRENT_PLAN_STEP}}
- **Confirmed-green baseline:** {{GREEN_BASELINE}} *(suite + pass/fail + wall time + timestamp)*
- **Blockers / in flight:** {{IN_FLIGHT}}
- **What the next session should do first:** {{NEXT_ACTION}}

## Reading order for a cold start

1. `thread.json` — machine-readable status, plan hops, findings
   index.
2. This file's "Current state" block above.
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
