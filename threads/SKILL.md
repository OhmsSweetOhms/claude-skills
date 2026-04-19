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
| "Link this research session to the thread" / "wire up the research back-pointer" | **Link research** |

If the user's ask doesn't match cleanly, ask which operation they
want before acting. Don't invent new operations.

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
  handoff journal. Created at thread birth; updated on user
  request (not automatically by workflows) to capture what the
  next session needs to know right now.
- `plan-01-template.md` — starter plan.
- `findings-template.md` — snapshot skeleton.
- `external-comment-template.md` — verbatim + triage scaffold.
- `temp-README.md` — regeneration-commands scaffold.

## Handoff journal vs README vs findings

Three prose artefacts, three different cadences and purposes:

| File | Cadence | Role |
|------|---------|------|
| `README.md` | Changes rarely; structural | Status header, plan-lineage table, findings table, research linkage, next-step pointer. The stable overview. |
| `findings-<YYYY-MM-DD>.md` | Once per plan hop closure / decision point | Point-in-time snapshot. What was measured, what was refuted, what the current best understanding is. Never overwritten. |
| `handoff.md` | On user request (typically at session end / start) | Reverse-chronological running journal. "I'm about to X", "I tried Y, it failed because Z", "confirmed-green baseline as of <time>", "next session should start here". The bridge between formal findings and ephemeral conversation. |

`handoff.md` is **never auto-updated** by the other workflows
(New plan hop, Findings snapshot, Close thread, Import external
review). Those workflows update the structural README and
machine-readable `thread.json`. `handoff.md` stays under explicit
user control — it's a journal, not a derived artefact.
