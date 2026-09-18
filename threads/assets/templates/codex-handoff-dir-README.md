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
  and this packet carries the mechanical facts that point Codex at it

The fire launcher writes here BEFORE the Codex first turn:

- `worker-state.json` — schema-validated foreground worker lifecycle receipt,
  written atomically by `launch_codex_worker.py`; do not hand-edit it

Codex writes here DURING/AFTER the run:

- `progress.json` — optional plan checkpoint and next-action state; never a
  substitute for the launcher-owned worker lifecycle receipt
- `handback.json` — machine-readable session handback
- `handback.md` — human-readable companion report
- `scripts/` — throwaway probes, debug tests, and helper scripts
- `temp/` — bulky or disposable generated working files
- `artifacts/` — curated evidence cited by the handback
- `mailbox.md` — the ONE shared, append-only message file between the
  orchestrator and a Codex worker (questions, answers, the handback
  announcement). Written only through `~/.claude/skills/mailbox/scripts/mb.py
  send`, never by hand; the worker is woken by `codex queue` and the
  orchestrator by its `Stop` hook, so nothing waits, nothing is typed and
  nothing times out (see `~/.claude/skills/mailbox/SKILL.md`, and
  `~/.claude/skills/threads/references/codex-handoff.md` §"Codex worker
  mailbox"). `fire-failed.log` sits beside it.

The main session reads this inbox after Codex exits and promotes only
durable material into `.threads/`, permanent tests, or tracked data.
