# threads/ conventions

Reference manual for the `threads/` layout: schemas, status
vocabularies, file templates, and naming rules. See `README.md`
for the task-indexed how-to-use guide.

---

## Naming

### Thread slug
`<YYYYMMDD>-<slug>` where `YYYYMMDD` is the date of the **earliest**
plan hop. Example: `20260414-cache-coherency-bug`.

### Plan numbering
`plan-NN-<slug>.md` where `NN` starts at 01 and increments per hop.
The slug describes this hop's focus, not its parent. Numbering
makes the order obvious at `ls`; the slug says what the hop was
about.

### Findings snapshots
`findings-<YYYY-MM-DD>.md` — one per snapshot, not per hop. Written
when a plan hop closes or a decision point is reached. Never
overwritten; a new snapshot is a new file.

### External-comment files
`<YYYYMMDD>-<source>-<subject>.md`.
Sources: `codex`, `claude-ai`, `colleague-<name>`, `other`.

---

## Status vocabulary

One enum, applied to both **threads** (`thread.json.status`,
`threads.json.threads[].status`) and **plan hops**
(`thread.json.plan_hops[].status`):

| Status | Meaning |
|---|---|
| `active` | In progress. |
| `blocked` | Awaiting external input (research, decision, hardware). |
| `superseded` | Replaced by a successor (thread: successor thread; plan hop: next plan hop in the same thread). |
| `closed` | Done. The `outcome` field carries the substance (resolution, inconclusive completion, or refutation). |

Don't invent new status values. If you need to distinguish
"resolved with a clear answer" from "ran to completion but
inconclusive," that distinction lives in `outcome` prose, not in
the enum.

### Disposition (external reviews only)

| Disposition | Meaning |
|---|---|
| `pending` | Captured verbatim, triage not started or incomplete. |
| `merged` | Every accepted triage-table point has a commit hash. `merged_into` must list those hashes. |
| `rejected` | Review contained no accepted points; rationale in "Merge notes". |
| `deferred` | Accepted in principle but held for later; "Merge notes" records the conditions. |

### Review kind

| Kind | Meaning |
|---|---|
| `comment` | Prose feedback, no rewrites. |
| `edit` | Reviewer rewrote parts of the plan or code. |
| `mixed` | Both comments and edits. |

---

## JSON schemas

### `threads/threads.json`

```json
{
  "version": 1,
  "threads": [
    {
      "id": "<subsystem>/<YYYYMMDD>-<slug>",
      "title": "Short one-line description",
      "status": "active",
      "started": "2026-04-14",
      "updated": "2026-04-18",
      "current_plan": "plan-NN-<slug>.md",
      "codex_worktrees": []
    }
  ],
  "promotion_log": [
    {
      "date": "2026-04-18",
      "from_thread": "<subsystem>/<YYYYMMDD>-<slug>",
      "from": "diagnostics/diagnose_X.py",
      "to": "<tests-dir>/test_X_regression.py",
      "reason": "One-line rationale"
    }
  ]
}
```

### `<thread>/thread.json`

```json
{
  "version": 1,
  "id": "<subsystem>/<YYYYMMDD>-<slug>",
  "title": "Short description",
  "status": "active",
  "started": "2026-04-14",
  "updated": "2026-04-18",
  "parent_plans": [],
  "plan_hops": [
    {"num": 1, "file": "plan-01-<slug>.md", "status": "active", "outcome": null}
  ],
  "findings": [],
  "diagnostics": [],
  "temp": [],
  "data": [],
  "linked_research": [],
  "promotions": [],
  "external_reviews": []
}
```

Each `plan_hops[]` entry shape:
```json
{"num": 1, "file": "plan-NN-<slug>.md", "status": "<status>", "outcome": "<prose or null>"}
```

Each `findings[]` entry shape:
```json
{"file": "findings-YYYY-MM-DD.md", "date": "YYYY-MM-DD", "plan_hop": N}
```

Each `diagnostics[]` entry shape:
```json
{"script": "diagnostics/diagnose_X.py", "plan_hop": N, "purpose": "<one-line>"}
```

Each `temp[]` entry shape:
```json
{"file": "temp/<output>", "plan_hop": N, "regenerate_with": "<exact shell cmd>"}
```

Each `linked_research[]` entry shape:
```json
{
  "path": ".research/session-<timestamp>",
  "title": "<session title>",
  "spawned_by_this_thread": true,
  "consumed_artifacts": ["file1.md", "file2.md"]
}
```

Each `promotions[]` entry shape:
```json
{
  "date": "YYYY-MM-DD",
  "from": "diagnostics/diagnose_X.py",
  "to": "<tests-dir>/test_X_regression.py",
  "reason": "<one-line>",
  "plan_hop": N
}
```

Each `external_reviews[]` entry shape:
```json
{
  "date": "YYYY-MM-DD",
  "source": "<source>",
  "subject": "<subject>",
  "kind": "comment",
  "disposition": "pending",
  "file": "external-comments/YYYYMMDD-<source>-<subject>.md",
  "merged_into": []
}
```

`merged_into` is required when `disposition == "merged"`, empty
otherwise.

### External-comment frontmatter

```markdown
---
source: codex                        # codex | claude-ai | colleague-<name> | other
date: 2026-04-20
subject: plan-NN-<slug>.md
kind: comment                        # comment | edit | mixed
disposition: pending                 # pending | merged | rejected | deferred
merged_into: []                      # commit hashes required once merged
---
```

---

## Scripts and data

- `diagnostics/` — any `diagnose_*.py` or one-off investigation
  script. Tracked in git.
- `temp/` — **required, gitignored**. Regeneratable outputs from
  running diagnostics. `temp/README.md` (tracked) documents
  regeneration commands.
- `data/` — optional. For committed captures that CANNOT be
  regenerated (hardware traces, one-off recordings), plus
  gate-dependent fixtures whose exact bytes are consumed by
  committed tests or CI gates.

**Rule:** if running a script from `diagnostics/` recreates the
file, it belongs in `temp/` unless a committed gate depends on that
exact snapshot. Commit bytes only when they can't be reproduced or
when they are gate-dependent fixtures.

---

## External review workflow

### Per-file template

```markdown
---
source: codex
date: YYYY-MM-DD
subject: <subject>
kind: comment
disposition: pending
merged_into: []
---

# External review: <source> on <subject>

## Raw content (verbatim — do not edit)

<!-- BEGIN RAW -->
<paste external output unchanged>
<!-- END RAW -->

---

## Triage

| # | Point | Disposition | Commit |
|---|-------|-------------|--------|
| 1 | "<point>" | accepted | <hash> |
| 2 | "<point>" | rejected | — |

## Merge notes

(One paragraph per accepted point.)
```

### Workflow

1. Paste raw external output verbatim into
   `external-comments/<YYYYMMDD>-<source>-<subject>.md`. Fill
   frontmatter with `disposition: pending`.
2. Triage every point in the table.
3. For each accepted point:
   - Edit the relevant plan or code file.
   - Commit. Capture the hash.
   - Fill the Commit column in the triage table.
4. Once every accepted point has a commit hash, flip frontmatter
   to `disposition: merged` and populate `merged_into`.
5. For rejected / deferred: rationale in "Merge notes".
6. Append an entry to `thread.json.external_reviews[]`.
7. The raw content section is never edited after step 1.

### Why require commit hashes

A review marked "merged" with no commit link is indistinguishable
from one that was discussed and forgotten. Requiring commit hashes
forces the merge to be a real git operation and makes
`git log <plan-file>` a ledger of which ideas came from where.

---

## Promotion path

When a diagnostic earns promotion to a permanent gate:

1. `git mv <thread>/diagnostics/diagnose_X.py <tests-dir>/test_X_regression.py`
   (or `<tests-dir>/test_X.py` for unit/integration-grade).
2. Refactor: remove the CLI, wrap assertions in the project's test
   framework idiom, keep helpers importable from other thread
   diagnostics.
3. Append to `<thread>/thread.json.promotions[]`.
4. Append to `threads/threads.json.promotion_log[]`.
5. Add a row in `<thread>/README.md` under "Promoted artifacts".

### Why `git mv` (not copy)
- `git log --follow <tests-dir>/test_X_regression.py` chains back
  through the diagnostic's history.
- Single source of truth — the script doesn't drift in two
  locations.

### When a diagnostic should NOT be promoted
- It depends on scratch data in `temp/` that won't be regenerated
  per run.
- It was a one-shot refutation and will never regress (e.g.,
  conventions cross-check). Record the outcome in the thread's
  `findings-*.md` and let the diagnostic rest as the historical
  record.

---

## Research linkage

Bidirectional, low-coupling. Research content never moves from
`.research/`.

**Thread side:** `thread.json.linked_research[]` records the
research sessions the thread consumed or spawned.

**Research side:** the session's `session-manifest.json` gains an
optional `spawning_thread: "<subsystem>/<YYYYMMDD>-<slug>"` field.

Both fields are optional. When both exist, they should stay in
sync.
