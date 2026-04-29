# Thread proposals — session-{{SESSION_ID}}

This file partitions the synthesis recommendations into three
buckets after dedup'ing against the project's existing thread tree
(snapshot in `thread-tree-snapshot.json`).

**Only the NEW bucket is eligible for `/code-survey propose-thread`
spawning.** SUBSUMED items already have a thread covering them;
TENSION items need user coordination first.

---

## New thread proposals ({{NEW_COUNT}} items)

{{#NEW}}
### Proposal {{N}} — {{slug}}

- **Recommendation:** {{ref to synthesis.md section / item id}}
- **Subsystem:** {{inferred from file paths touched}}
- **Files touched:**
  {{file list}}
- **Risk:** {{low|medium|high}}
- **Suggested verification gate:** {{e2e gate from config}}
- **Suggested plan-hop count:** {{1 | 1 phase + N hops}}
- **Hypothesis seed:** {{one-line hypothesis the thread will test}}
- **Stop criteria seed:** {{when does the thread close?}}

{{/NEW}}

---

## Subsumed by active threads ({{ACTIVE_SUBSUMED_COUNT}} items)

These recommendations are already in flight. Do **not** spawn new
threads for them.

{{#ACTIVE_SUBSUMED}}
- Item {{id}} → covered by `{{thread_dir}}` (active, plan-{{plan_num}} {{hop}})
  - Match basis: {{file-overlap | title-keyword | both}}
  - Suggested action: {{wait | extend that thread | nothing}}
{{/ACTIVE_SUBSUMED}}

---

## Subsumed by closed threads ({{CLOSED_SUBSUMED_COUNT}} items)

These recommendations have already landed. Useful for the
filtered-out appendix but not actionable.

{{#CLOSED_SUBSUMED}}
- Item {{id}} → already landed in `{{thread_dir}}` (closed, commit `{{sha}}`)
{{/CLOSED_SUBSUMED}}

---

## Tension with active threads ({{TENSION_COUNT}} items)

These recommendations touch the same files as an active thread
*for a different reason*. Coordinate before proceeding — folding
the recommendation into the existing thread or scheduling around
it both work; just don't open a parallel thread on the same files.

{{#TENSION}}
- Item {{id}} → conflicts with `{{thread_dir}}`
  - Shared files: {{file list}}
  - Their reason: {{their hypothesis / aim}}
  - Our reason: {{this recommendation}}
  - Suggested coordination: {{fold-in | wait | scope-split}}
{{/TENSION}}

---

## Summary

| Bucket | Count | Action |
|---|---|---|
| NEW | {{NEW_COUNT}} | Eligible for `/code-survey propose-thread` |
| Active-SUBSUMED | {{ACTIVE_SUBSUMED_COUNT}} | Point user at thread; do not spawn |
| Closed-SUBSUMED | {{CLOSED_SUBSUMED_COUNT}} | Already landed; demote to appendix |
| TENSION | {{TENSION_COUNT}} | Coordinate before action |

Inputs:
- Synthesis: `synthesis.md`
- Thread inventory: `thread-tree-snapshot.json`
- Generated: {{TIMESTAMP}}
