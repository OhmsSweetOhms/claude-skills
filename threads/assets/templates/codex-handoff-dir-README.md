# Codex Handoff Inbox — {{PLAN_ID}}

Thread: `{{THREAD_ID}}`
Worktree: `{{WORKTREE_PATH}}`

This directory is the Codex session inbox. Everything Codex reads or
writes for this plan hop lives here, so the handoff infrastructure
stays self-contained in the worktree branch.

Main session writes here BEFORE Codex launch:

- `README.md` — this file (inbox description, who writes what)
- `prompt.md` — curated launch prompt; pasted into Codex's first message
  (rendered via `~/.claude/skills/threads/scripts/render_codex_handoff.py`,
  which defaults its output to this inbox)

Codex writes here DURING/AFTER the run:

- `handback.json` — machine-readable session handback
- `handback.md` — human-readable companion report
- `scripts/` — throwaway probes, debug tests, and helper scripts
- `temp/` — bulky or disposable generated working files
- `artifacts/` — curated evidence cited by the handback

The main session reads this inbox after Codex exits and promotes only
durable material into `.threads/`, permanent tests, or tracked data.
