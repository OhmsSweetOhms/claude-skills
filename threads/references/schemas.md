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
      "current_plan": "plan-NN-<slug>.md",
      "codex_worktrees": []
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
- `threads[].codex_worktrees[]` — copy of the per-thread
  `thread.json.codex_worktrees[]` array, preserved in full so
  registry consumers can surface active, merged, or abandoned
  worktree state without re-reading every `thread.json`.
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
  ],
  "codex_worktrees": [
    {
      "path": "/abs/path/to/<repo>-<slug>",
      "branch": "<slug>",
      "base_commit": "<short-sha-from-origin-main>",
      "started": "2026-04-29",
      "status": "active",
      "merged_into": null,
      "merged_at": null,
      "notes": "Optional one-line"
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
- `data[]` — expected tracked data captures or fixtures. Use for
  files that cannot be regenerated and for gate-dependent exact
  snapshots that must be present in a clean checkout. Keep refresh
  commands in `data/README.md` rather than `regenerate_with`.
- `linked_research[]` — see `research-integration.md` for
  bidirectional-link semantics.
- `promotions[]` — historical record of diagnostics promoted to
  permanent tests. Never rewritten.
- `external_reviews[]` — one entry per external-comments file.
  Mirror of the frontmatter in the referenced file.
- `codex_worktrees[]` — one entry per long-lived codex worktree
  the thread spawned (typically one; rarely more, e.g., if the
  first worktree was abandoned and a fresh one cut). See
  `references/codex-handoff.md` for the workflow. Field-by-field:
  - `path` — absolute path to the worktree dir on the user's
    machine. Convention: `<parent-of-repo>/<repo-name>-<slug>`.
  - `branch` — git branch name of the worktree, matches the
    thread's slug component.
  - `base_commit` — short SHA the worktree was cut from at
    bootstrap. Always `origin/main` HEAD at the time, not a stale
    local branch.
  - `started` — ISO date of bootstrap.
  - `status` — `active`, `merged`, or `abandoned`. Worktree-specific
    enum, intentionally distinct from the thread / plan-hop status
    enum (`active | blocked | superseded | closed`) since the
    terminal states differ.
  - `merged_into` — commit hash on `main` once the worktree is
    merged back. `null` while `status == "active"`. Required
    non-null when `status == "merged"` — same audit-trail rule as
    `external_reviews[].merged_into`.
  - `merged_at` — ISO date of merge. `null` while `status == "active"`.
  - `notes` — optional one-line. Use sparingly (e.g., to flag
    "abandoned: codex sandbox blocked all writes; restarted at
    base_commit X").

### `codex_worktrees[]` merged_into contract

When `status == "merged"`, `merged_into` is required and must be a
real commit hash on `main` (`git rev-parse <hash>` succeeds — short
or full form). The merge commit's body should describe what the
thread accomplished; this is the user's commit, not auto-generated.

When `status ∈ {"active", "abandoned"}`, `merged_into` is `null`.
An `abandoned` worktree was cleaned up without landing — record
why in `notes`.

This is a strict rule for the same reason as the `external_reviews[]`
contract: a worktree marked `merged` with no commit hash is
indistinguishable from one that was deleted and forgotten. The
hash forces merge to be a real git operation.

## Codex handback JSON

Each Codex worktree hop emits a JSON handback next to its Markdown
summary:

```text
<worktree>/.threads/<thread-id>/codex-handback-<plan-id>.json
```

The authoritative schema is
`assets/schemas/codex-handback.schema.json`. See
`references/codex-handback.md` for the lifecycle and consumer
workflow. Field summary:

- `schema_version` — currently `"2"`.
- `plan_id` — plan id such as `plan-03`.
- `thread_id` — canonical thread id.
- `session_date` — ISO date.
- `status` — Codex progress/outcome:
  `complete | gate-incomplete | blocked | scope-cut`.
- `closure_status` — optional thread/plan lifecycle state:
  `active | blocked | superseded | closed`; omit for normal forward
  handbacks until the main session closes the hop.
- `worktree` — branch, hop-start base, handback head, optional
  terminal SHA/commit range, and `diff_stat`.
- `commits[]` — `{sha, subject, role, has_verification_trailer?}`.
- `tests` — optional aggregate test count/log metadata.
- `regression_baseline` — optional command/result/log metadata.
- `gates[]` — acceptance-gate verdicts with optional
  `caveats[]` for portability, reproducibility, validity, coverage,
  or scope caveats.
- `engineering_deliverables[]` — optional path-level landed/partial/
  deferred/removed summary.
- `discoveries[]` — Codex-spontaneous observations.
- `investigations[]` — user-prompted mid-session inquiries.
- `blockers[]` — blockers with command evidence and proposed owner.
- `follow_ons[]` — proposed next-hop, new-thread, backlog, or
  out-of-scope follow-ups.
- `plan_hindsight` — short retrospective string.

Schema compatibility: the v2 JSON schema remains tolerant of
unknown fields so historical handbacks validate. Current templates
define stricter emission discipline: new gates should emit
`caveats: []` when no caveat exists, and lifecycle closure should
use `closure_status` rather than overloading `status`.

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
