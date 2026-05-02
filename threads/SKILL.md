---
name: threads
description: Manage debug-thread directories for hypothesis-driven investigations that span multiple sessions. Use this skill whenever the user wants to start a new debug thread, add a plan hop to an existing thread, capture a findings snapshot, register a diagnostic script, import external review feedback (from Codex, claude.ai, or a colleague), promote a diagnostic into a permanent regression test, close a thread, or link a thread to a `/research` session. Also triggers on any mention of `threads/`, `thread.json`, "debug thread", "plan hop", "findings snapshot", "promote this diagnostic", "external comment", or when the user is working inside a `threads/<subsystem>/<slug>/` directory. This skill plays nice with `/research`: it maintains the bidirectional link (`thread.json.linked_research[]` ↔ `session-manifest.json.spawning_thread`). Do NOT use for sprint boards, feature planning, or one-off debug commands — this is specifically for multi-hop investigations that accrete plans, data, and diagnostics over time.
---

# threads — Debug Investigation Container Pattern

A "thread" is a **hypothesis-driven, time-bounded investigation**
that accretes state across sessions. Instead of N scattered
`*-PLAN.md` files at the repo root and a pile of orphaned
`diagnose_*.py` scripts, everything for one investigation lives in
`threads/<subsystem>/<YYYYMMDD>-<slug>/` with a machine-readable
`thread.json` manifest.

The goal is **session handoff**: a future collaborator — your next
session, another Claude, or a human — can pick up the investigation
cold by reading one directory, not by grepping `Status:` across a
dozen markdown files.

## When to use this skill

Trigger on any of:

- User says `/threads ...` or types a command like "new thread",
  "promote this diagnostic", "close the auth-latency thread".
- User asks to start a debug investigation that will clearly span
  multiple plan revisions or produce diagnostic scripts.
- User mentions `threads/`, `thread.json`, `threads.json`, or refers
  to plan hops, findings snapshots, or external reviews.
- User is editing a file under `threads/<subsystem>/<slug>/` and
  needs to update manifests, add plan hops, or capture findings.
- User wants to link a `/research` session to a thread, or
  vice-versa.
- User wants a survey of the threads tree's overall state, asks
  "what's blocked," "thread status," "thread review," or wants to
  triage stale/blocked threads as a batch. → **Status review**
  workflow produces a frozen `review-<YYYY-MM-DD>.md` snapshot.

Do NOT trigger for:

- Sprint planning, feature roadmaps, TODO lists — those are not
  hypothesis-driven.
- One-off debugging that resolves in a single session with no
  artefacts worth preserving.
- Architectural or block-level design plans — those live at
  `<package>/*-PLAN.md` and are NOT debug threads. See the "Type A
  vs Type B" distinction in `references/layout.md`.

## Core concepts (read first)

Before you act on any operation, skim `references/layout.md`. It
covers:

- The directory tree and file roles.
- **Type A vs Type B**: architectural plans stay in place; only
  debug threads migrate into `threads/`. This distinction is
  load-bearing — misclassifying will churn the wrong files.
- Naming conventions (thread slug, plan numbering, findings,
  external-comment filenames).
- The unified status vocabulary (`active`, `blocked`, `superseded`,
  `closed`) and why nuance lives in `outcome` prose, not in the
  enum.

For exact JSON shapes, `references/schemas.md` has the
`threads.json`, `thread.json`, and external-comment frontmatter
contracts with examples.

For Codex worktree sessions, `references/codex-handback.md` defines
the handback artifact pair (`codex-handback-<plan-id>.json` and
`.md`), the recording discipline, lifecycle visibility rules, and
the consumer triage step before merge-back or next-hop activation.

## Operations — dispatch table

When the user's request matches one of these, follow the named
section in `references/workflows.md`:

| User's ask | Workflow section |
|-----------|------------------|
| "Adopt threads/ in this repo" / "initialize threads/" | **Bootstrap** |
| "Start a new thread for X" / "new thread under Y subsystem" | **New thread** |
| "Add a plan hop" / "write plan-NN for <existing thread>" | **New plan hop** |
| "Capture findings" / "write a findings snapshot" | **Findings snapshot** |
| "Register this diagnostic" / "track diagnose_*.py in thread.json" | **Register diagnostic** |
| "Import Codex/claude.ai/colleague feedback" / "add external comment" | **Import external review** |
| "Promote this diagnostic to a test" / "it's a regression gate now" | **Promote diagnostic** |
| "Close the thread" / "mark thread as done" | **Close thread** |
| "Retire closed threads" / "audit and delete closed thread directories" / "clean up the working tree" / "garbage-collect closed threads" | **Retire thread** |
| "Thread status" / "thread review" / "what's blocked" / "/threads --review" / triage stale threads as a batch | **Status review** |
| "Link this research session to the thread" / "wire up the research back-pointer" | **Link research** |
| "Hand thread X off to codex" / "spawn a codex worktree on X" / "spawn codex on X" / "run codex on X" | **Codex worktree handoff** (`references/codex-handoff.md`) |
| "Recover a missing codex handback" / "retroactive handback" / "closed plan has no handback" | **Retroactive handback** (`references/codex-handoff.md`) |
| "Triage codex handback findings" / "process codex handback" / "classify handback follow-ons" | **Process codex handback** (`references/codex-handoff.md`) |
| "Merge the codex worktree work back" / "the codex agent finished, pull the work in" | **Codex worktree merge-back** (`references/codex-handoff.md`) |

If the user's ask doesn't match cleanly, ask which operation they
want before acting. Don't invent new operations.

The codex-handoff pair is a delivery mechanism for thread work, not
a separate domain — a thread already has a plan; codex executes
source-code work in an isolated worktree while bookkeeping stays on
`main`. The worktree + branch are **long-lived across the thread's
full lifetime** (all plans, all hops): bootstrap once at thread
start, re-invoke codex against the same worktree N times as the
plan progresses, and merge back to `main` exactly once at thread
close — and only when the user explicitly requests it. The
merge-back script never auto-merges; it shows the incoming diff and
prompts for confirmation. See `references/codex-handoff.md` for the
full workflow, the pre-built bootstrap / scaffold-render / merge-back
scripts under `scripts/`, and the agent-prompt scaffold template
under `assets/templates/codex-handoff-prompt.md`. The renderer fills
mechanical boilerplate only; the main agent must replace every
`HAND-CURATE` marker before launch.

## Integration with `/research`

`references/research-integration.md` covers the bidirectional link:

- **Thread side**: `thread.json.linked_research[]` — array of
  `{path, title, spawned_by_this_thread, consumed_artifacts[]}`
  entries. Each entry records a research session the thread either
  **spawned** (thread preceded research) or **consumed** (thread
  used research produced independently).
- **Research side**: `session-manifest.json.spawning_thread` —
  string path like `"threads/receiver/20260414-nav-anchor-precision"`.
  Optional field; the `/research` schema is permissive so adding it
  doesn't break anything.

The two sides should stay in sync when both exist. The **Link
research** workflow handles writing both.

## Auto-generated registry

`<project>/.threads/threads.json` is **auto-generated**, not
hand-edited. The canonical source for every thread is its own
`thread.json` (status, plan hops, findings, promotions, linked
research, etc.). The registry is the aggregate produced by
`~/.claude/skills/threads/scripts/index_threads_research.py`,
which also writes `<project>/.research/INDEX.json` for the
research-side mirror.

**Mutating workflows** — new thread, new plan hop, findings snapshot,
register diagnostic, import external review, promote diagnostic,
close thread, link research, codex worktree merge-back — must
regenerate the registry as their **final step**:

```bash
python3 ~/.claude/skills/threads/scripts/index_threads_research.py
```

**Status-review workflow** also fires the indexer, but as a **first
step**: the review reads `threads.json` to compute status counts and
triage candidates, so a stale registry produces a misleading review.
Same command, run before `status_review.py`.

Run it from the project root (the directory containing `.threads/`
and `.research/`). The script discovers the project root from the
current working directory; pass `--project-root <path>` if invoking
from elsewhere. Add `--check` to validate without writing (exits 1
on any cross-reference findings); add `--print` to also see a
one-line summary plus per-finding-kind breakdown.

The registry must always be regenerated and committed in the same
commit as the per-thread edits. A drifted registry is a bug.

## Invariants (violate these and the pattern falls apart)

- **Unified status enum.** Threads and plan hops both use
  `active | blocked | superseded | closed`. Don't invent new
  values. Put substance in `outcome` prose.
- **External-review raw content is never edited.** The verbatim
  section is the attribution record. Only the triage table and
  merge notes get updated.
- **Merge requires a commit hash.** An external review may only be
  marked `disposition: merged` when every accepted triage-table
  point has a commit hash in `merged_into[]`. This makes every
  accepted external idea traceable to a git operation.
- **Promotion uses `git mv`, not copy.** Preserves
  `git log --follow` lineage from the test back through the
  diagnostic's history.
- **`temp/` is gitignored; `temp/README.md` is tracked.** The
  README documents regeneration commands; the bytes don't get
  committed. If an output can't be regenerated, it goes in
  `data/` instead.
- **Handoff tracks plan state.** Whenever a plan hop is added,
  closed, superseded, or materially edited in place, the
  `handoff.md` forward-looking sections (Current state, Blockers,
  "What the next session should do first", Reading order,
  Cross-references) must be updated in the same commit — and a
  new Session-log entry appended at the top describing the
  transition in prose. The New-plan-hop and Close-thread workflows
  include this step; in-place plan edits are the user's
  responsibility to mirror into the handoff. A handoff that
  references a closed or superseded plan is a cold-start trap.

## Sanity checks before acting

When asked to modify a thread, always:

1. Read the thread's `thread.json` first. If the file doesn't
   parse, stop and flag the corruption — don't append to a broken
   manifest.
2. Check `thread.json.status`. If `closed` or `superseded`, confirm
   with the user before adding new hops or diagnostics.
3. Check the top-level `threads/threads.json` exists and references
   this thread. If out of sync, fix it as part of the change.
4. If you're writing a new plan hop, the previous hop should be
   marked `closed` or `superseded` with an `outcome` before the new
   one is added.

These are cheap to check and expensive to recover from if skipped.

## Session-skill tracker (optional)

For multi-skill investigations where you'd like to capture gaps and
improvement ideas about the skills themselves as you use them, run:

```bash
bash scripts/init_tracker.sh threads <other-skill> [<more>]
```

This creates `.claude/workspace/skill-tracker-<YYYYMMDD>.md` (in the
project's workspace dir) with a template, entry schema, and an
end-of-session pass instruction. Idempotent — existing trackers are
left alone. Pattern is borrowed from `/socks-tracker`, but works for
any skills the session is exercising. At session end, the template
prompts you to group entries per skill and propose concrete edits
(file + before/after) without applying them automatically.

When to bother: long sessions, fresh-skill shakedown runs, or
hand-offs where the next session might benefit from the skill being
sharper. Skip for one-shot tasks where there's nothing to learn.

## Templates

`assets/templates/` holds the file skeletons this skill copies
during Bootstrap and New-thread operations. Each template has
placeholder tokens like `{{SLUG}}`, `{{DATE}}`,
`{{SUBSYSTEM}}` — substitute them literally when copying.
Templates:

- `top-level-README.md` — the project-level `threads/README.md`.
- `top-level-CONVENTIONS.md` — the project-level `threads/CONVENTIONS.md`.
- `top-level-threads.json` — empty threads/promotion_log arrays.
- `thread-README.md` — per-thread landing page.
- `thread.json` — per-thread manifest skeleton.
- `thread-handoff.md` — per-thread rolling session-to-session
  handoff journal. Created at thread birth; updated by the
  New-plan-hop and Close-thread workflows whenever the plan
  changes (forward-looking sections get stale otherwise), and on
  user request for in-session notes. See "Handoff journal vs
  README vs findings" below for the cadence contract.
- `plan-01-template.md` — starter plan.
- `findings-template.md` — snapshot skeleton.
- `external-comment-template.md` — verbatim + triage scaffold.
- `temp-README.md` — regeneration-commands scaffold.
- `codex-handoff-prompt.md` — Codex worktree prompt scaffold.
  `render_codex_handoff.py` substitutes mechanical worktree/thread
  values and leaves `HAND-CURATE` markers for the main agent to
  author.
- `codex-handback-retroactive-prompt.md` — recovery prompt for a
  closed or already-executed plan hop that lacks structured
  handback artifacts.
- `review-template.md` — index-scope status-review skeleton with
  AUTO-BEGIN/AUTO-END markers and boilerplate manual sections
  (strategic tiers, cross-tier file overlaps, critical-path
  observations, recommendations). Used by the **Status review**
  workflow; the script `scripts/status_review.py` rewrites the auto
  block, manual sections persist across regen passes.

## Handoff journal vs README vs findings

Three prose artefacts, three different cadences and purposes:

| File | Cadence | Role |
|------|---------|------|
| `README.md` | Changes rarely; structural | Status header, plan-lineage table, findings table, research linkage, next-step pointer. The stable overview. |
| `findings-<YYYY-MM-DD>.md` | Once per plan hop closure / decision point | Point-in-time snapshot. What was measured, what was refuted, what the current best understanding is. Never overwritten. |
| `handoff.md` | **Whenever a plan changes** (new hop, close, supersede, material in-place plan edit) + on user request for in-session notes | Reverse-chronological running journal. "I'm about to X", "I tried Y, it failed because Z", "confirmed-green baseline as of <time>", "next session should start here". The bridge between formal findings and ephemeral conversation. |

`handoff.md` is **updated by the New-plan-hop and Close-thread
workflows**, alongside `README.md` and `thread.json`. It is also
updated on user request for in-session notes. The reason: when the
plan changes, the handoff's forward-looking sections — Current
state, Blockers, "What the next session should do first", Reading
order, Cross-references — go stale immediately, and a cold-start
reader lands on a misleading snapshot. The fix is to re-check
those sections against the new plan state in the **same commit**
that changes the plan. The Session-log entry at the top of
`handoff.md` gets a new block in prose describing the transition.

`handoff.md` is never **derived mechanically** from other files —
that's what `thread.json` is for. Always edit with attention: the
Session-log narrative is the human record, and the forward-looking
sections are re-thought, not auto-populated.
