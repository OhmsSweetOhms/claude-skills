# Codex Handoff Inbox — {{PLAN_ID}}

Thread: `{{THREAD_ID}}`
Worktree: `{{WORKTREE_PATH}}`

This directory is the Codex session inbox. Everything Codex reads or
writes for this plan hop lives here, so the handoff infrastructure
stays self-contained in the worktree branch.

Main session writes here BEFORE Codex launch:

- `README.md` — this file (inbox description, who writes what)
- `prompt.md` — launch packet from `~/.claude/skills/threads/scripts/emit_codex_launch_packet.py`;
  the plan file at `.threads/{{THREAD_ID}}/<plan-NN>-*.md` IS the launch prompt,
  and this packet carries the six mechanical facts that point Codex at it

Codex writes here DURING/AFTER the run:

- `handback.json` — machine-readable session handback
- `handback.md` — human-readable companion report
- `scripts/` — throwaway probes, debug tests, and helper scripts
- `temp/` — bulky or disposable generated working files
- `artifacts/` — curated evidence cited by the handback
- `questions/` — ambiguity mailbox (`q-NN.md`): when the plan/ADRs/
  vectors don't pin a decision Codex needs, it writes a
  `status: open` question here and blocks; the main session's
  background watcher answers in the same file (1 h cap both sides;
  see `~/.claude/skills/threads/references/codex-handoff.md`
  §"Ambiguity mailbox")

The main session reads this inbox after Codex exits and promotes only
durable material into `.threads/`, permanent tests, or tracked data.
