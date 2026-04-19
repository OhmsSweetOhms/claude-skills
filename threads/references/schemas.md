# JSON schemas

Exact shapes for the three JSON/frontmatter contracts in the
`threads/` pattern. Treat these as definitive — tooling (`jq`,
future CLI, future skills) will parse against them.

## `threads/threads.json`

Top-level machine-readable index. Listed once per repo.

```json
{
  "version": 1,
  "threads": [
    {
      "id": "<subsystem>/<YYYYMMDD>-<slug>",
      "title": "Short one-line description (<= 80 chars)",
      "status": "active",
      "started": "2026-04-14",
      "updated": "2026-04-18",
      "current_plan": "plan-NN-<slug>.md"
    }
  ],
  "promotion_log": [
    {
      "date": "2026-04-18",
      "from_thread": "<subsystem>/<YYYYMMDD>-<slug>",
      "from": "diagnostics/diagnose_<X>.py",
      "to": "<path>/test_<X>_regression.py",
      "reason": "One-line why this was worth promoting"
    }
  ]
}
```

### Field notes

- `version` — integer, currently `1`. Bumps only with
  backwards-incompatible schema changes.
- `threads[].id` — the canonical identifier used by
  `/research`'s `spawning_thread` field. Must match the thread
  directory's position:
  `threads/<subsystem>/<YYYYMMDD>-<slug>/`.
- `threads[].status` — from the unified enum: `active`, `blocked`,
  `superseded`, `closed`. Mirror of `thread.json.status`.
- `threads[].current_plan` — bare filename (no path prefix). When
  `status` is `closed` or `superseded`, this still points at the
  last active plan hop.
- `promotion_log[]` — append-only. Every promotion adds one entry.
  Never rewrite historical entries.
- `promotion_log[]` field naming: `from`/`to` (paths) match the
  per-thread `promotions[]` shape exactly; `from_thread` is the
  one extra field at the top-level slot since the thread context
  isn't implicit there. Same conceptual entity has the same field
  names at both scopes — easier to remember, easier to grep.

## `<thread>/thread.json`

Per-thread manifest. Authoritative for what's in this thread.

```json
{
  "version": 1,
  "id": "<subsystem>/<YYYYMMDD>-<slug>",
  "title": "Short one-line description",
  "status": "active",
  "started": "2026-04-14",
  "updated": "2026-04-18",
  "parent_plans": [
    "<path-to-Type-A-plan>/some-integration-PLAN.md"
  ],
  "plan_hops": [
    {
      "num": 1,
      "file": "plan-01-<slug>.md",
      "status": "closed",
      "outcome": "Short prose: what this hop resolved or refuted"
    }
  ],
  "findings": [
    {
      "file": "findings-YYYY-MM-DD.md",
      "date": "2026-04-17",
      "plan_hop": 3
    }
  ],
  "diagnostics": [
    {
      "script": "diagnostics/diagnose_<name>.py",
      "plan_hop": 1,
      "purpose": "One-line description of what it measures"
    }
  ],
  "temp": [
    {
      "file": "temp/<output>.csv",
      "plan_hop": 3,
      "regenerate_with": "python diagnostics/diagnose_X.py --flag"
    }
  ],
  "data": [],
  "linked_research": [
    {
      "path": ".research/session-<timestamp>",
      "title": "Research session title",
      "spawned_by_this_thread": true,
      "consumed_artifacts": [
        "report.md",
        "some-specific-artefact.md"
      ]
    }
  ],
  "promotions": [
    {
      "date": "2026-04-18",
      "from": "diagnostics/diagnose_<X>.py",
      "to": "<path>/test_<X>_regression.py",
      "reason": "Short one-line rationale",
      "plan_hop": 4
    }
  ],
  "external_reviews": [
    {
      "date": "2026-04-20",
      "source": "codex",
      "subject": "plan-05-<slug>.md",
      "kind": "comment",
      "disposition": "merged",
      "file": "external-comments/20260420-codex-plan-05-review.md",
      "merged_into": ["<commit-hash-1>", "<commit-hash-2>"]
    }
  ]
}
```

### Field notes

- `id` — identical to the `threads.json` entry. One canonical
  form.
- `parent_plans[]` — Type A plans this thread investigates under.
  Usually one entry; sometimes zero (exploratory); occasionally
  two (cross-cutting investigation).
- `plan_hops[]` — ordered by `num`. Each entry:
  - `num` — integer, starts at 1, increments per hop.
  - `file` — bare filename (no path).
  - `status` — unified enum: `active`, `blocked`, `superseded`,
    `closed`.
  - `outcome` — prose, nullable while `status == "active"`. Brief:
    what resolved, what got refuted, or what moved to a successor.
- `findings[]` — each snapshot. `plan_hop` is the integer hop
  number the snapshot wraps up.
- `diagnostics[]` — tracked scripts. `plan_hop` says which hop
  spawned the diagnostic. `purpose` is a one-line description
  (helps future readers decide which diagnostic to re-run).
- `temp[]` — register of expected `temp/` outputs. Gitignored on
  disk. `regenerate_with` is the exact shell command to recreate
  the file.
- `data[]` — same shape as `temp[]` minus `regenerate_with`
  (because data can't be regenerated). Often empty; populate only
  for hardware traces or one-off captures.
- `linked_research[]` — see `research-integration.md` for
  bidirectional-link semantics.
- `promotions[]` — historical record of diagnostics promoted to
  permanent tests. Never rewritten.
- `external_reviews[]` — one entry per external-comments file.
  Mirror of the frontmatter in the referenced file.

### `external_reviews[]` merged_into contract

When `disposition == "merged"`, `merged_into[]` is required and
must be a non-empty array of commit hashes (short or full form) —
one entry per accepted triage-table point that produced a commit.

When `disposition ∈ {"pending", "rejected", "deferred"}`,
`merged_into[]` is empty `[]` or may be omitted.

This is a strict rule: a review marked `merged` with no commit
hash is indistinguishable from one that was discussed and
forgotten. The commit-hash requirement forces merge to be a real
git operation, not a paperwork flip.

## External-comment frontmatter

Each file under `<thread>/external-comments/` starts with YAML
frontmatter:

```markdown
---
source: codex                                 # codex | claude-ai | colleague-<name> | other
date: 2026-04-20
subject: plan-05-<slug>.md
kind: comment                                 # comment | edit | mixed
disposition: pending                          # pending | merged | rejected | deferred
merged_into: []                               # array of commit hashes (required if merged)
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
| 1 | <quote or paraphrase of each actionable point> | accepted | <hash> |
| 2 | ... | rejected | — |

## Merge notes

(One paragraph per accepted point: what changed, which commit
carries the change, why. For rejected points: brief rationale.
For deferred: conditions under which the point becomes active.)
```

### Frontmatter field notes

- `source` — see `layout.md` for the allowed values. If a reviewer
  doesn't fit those categories, use `other` and identify them in
  `Merge notes`.
- `kind` — `comment` means prose feedback only; `edit` means the
  reviewer rewrote parts of the plan or code; `mixed` means both.
  Pick the one closest to the bulk of the content; if truly mixed,
  use `mixed`.
- `disposition` — see the disposition table in `layout.md`.
- `merged_into` — see the contract above.

### Raw content section — the cardinal rule

The raw content section is **never edited after initial capture**.
It is the attribution record. If the external reviewer sent
Markdown, paste it as-is. If they sent a Python file, wrap it in a
```python fenced block. The delimiter comments
(`<!-- BEGIN RAW -->` / `<!-- END RAW -->`) mark the boundaries
for future sessions that need to diff against the original.

Correcting typos, paraphrasing, or reorganizing the raw section
breaks the audit trail. If the reviewer's content contained an
obvious error, note the correction in `Merge notes`, not in the
raw section.
