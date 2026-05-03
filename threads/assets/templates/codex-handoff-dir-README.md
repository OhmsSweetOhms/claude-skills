# Codex Handoff Inbox — {{PLAN_ID}}

Thread: `{{THREAD_ID}}`
Worktree: `{{WORKTREE_PATH}}`

This directory is the Codex session inbox. The main session created it
before launch so Codex does not need to write under `.threads/`.

Codex may write here:

- `handback.json` — machine-readable session handback
- `handback.md` — human-readable companion report
- `scripts/` — throwaway probes, debug tests, and helper scripts
- `temp/` — bulky or disposable generated working files
- `artifacts/` — curated evidence cited by the handback

The main session reads this inbox after Codex exits and promotes only
durable material into `.threads/`, permanent tests, or tracked data.
