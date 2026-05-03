#!/usr/bin/env bash
# install_worktree_codex_hooks.sh — install Codex worktree guard hooks.
#
# The hook is deliberately simple: Codex worktrees must not commit
# changes under .threads/. Thread bookkeeping is main-checkout owned.
# Session handoff material lives under codex-handoff/<plan-id>/.

set -euo pipefail

if [ $# -ne 1 ]; then
    echo "usage: install_worktree_codex_hooks.sh <worktree-path>" >&2
    exit 2
fi

WORKTREE="$(realpath "$1")"

if ! git -C "$WORKTREE" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    echo "not a git worktree: $WORKTREE" >&2
    exit 2
fi

HOOK_DIR="$WORKTREE/.codex-hooks"
HOOK="$HOOK_DIR/pre-commit"
mkdir -p "$HOOK_DIR"

cat > "$HOOK" <<'HOOK'
#!/usr/bin/env bash
set -euo pipefail

blocked="$(
    git diff --cached --name-only --diff-filter=ACMRTUXB -- .threads || true
)"

if [ -n "$blocked" ]; then
    cat >&2 <<EOF
Codex worktree guard: refusing to commit .threads/ changes.

.threads/ is main-checkout bookkeeping. Codex session output belongs in:

  codex-handoff/<plan-id>/

Staged .threads/ paths:
$blocked

Unstage those paths and let the main session promote any durable
handoff material into .threads/ after triage.
EOF
    exit 1
fi
HOOK

chmod +x "$HOOK"

# Use worktree-local config so the hook applies only to this linked
# worktree, not to the user's main checkout or sibling worktrees.
git -C "$WORKTREE" config extensions.worktreeConfig true
git -C "$WORKTREE" config --worktree core.hooksPath .codex-hooks

echo "codex worktree hooks installed: $HOOK"
