#!/usr/bin/env bash
# bootstrap_codex_worktree.sh — set up an isolated worktree for handing
# focused source-code work off to a Codex agent.
#
# Idempotent: if the worktree already exists at the target path, the
# script verifies its branch and refreshes the venv symlink + .envrc
# but leaves any in-progress edits alone. Re-running is safe.
#
# Always cuts from current origin/main HEAD (never a stale local branch).
#
# Usage:
#   bash bootstrap_codex_worktree.sh <thread-slug> [--repo <path>] [--branch <name>]
#
# Defaults:
#   --repo:   git rev-parse --show-toplevel (current main checkout)
#   --branch: <thread-slug> (matches the worktree dir suffix)
#
# Outputs to stdout:
#   - worktree path, branch, base commit, venv link target
#   - JSON snippet to paste into the thread's thread.json (first creation only)

set -euo pipefail

SLUG=""
REPO=""
BRANCH=""

while [ $# -gt 0 ]; do
    case "$1" in
        --repo)    REPO="$2"; shift 2 ;;
        --branch)  BRANCH="$2"; shift 2 ;;
        --help|-h) sed -n '2,21p' "$0"; exit 0 ;;
        -*)        echo "unknown flag: $1" >&2; exit 2 ;;
        *)         SLUG="$1"; shift ;;
    esac
done

if [ -z "$SLUG" ]; then
    echo "usage: bootstrap_codex_worktree.sh <thread-slug>" >&2
    exit 2
fi

if [ -z "$REPO" ]; then
    REPO="$(git rev-parse --show-toplevel)"
fi
REPO="$(realpath "$REPO")"

if [ ! -d "$REPO/.git" ] && [ ! -f "$REPO/.git" ]; then
    echo "not a git repo: $REPO" >&2
    exit 2
fi

[ -z "$BRANCH" ] && BRANCH="$SLUG"

REPO_NAME="$(basename "$REPO")"
PARENT="$(dirname "$REPO")"
WORKTREE="$PARENT/${REPO_NAME}-${SLUG}"

cd "$REPO"

# Detect existing worktree at target path.
EXISTED=0
if git worktree list --porcelain | awk '/^worktree /{print $2}' | grep -qx "$WORKTREE"; then
    EXISTED=1
    echo "worktree already exists at: $WORKTREE (idempotent refresh mode)"
    EXISTING_BRANCH="$(git -C "$WORKTREE" rev-parse --abbrev-ref HEAD 2>/dev/null || echo '?')"
    if [ "$EXISTING_BRANCH" != "$BRANCH" ]; then
        echo "  WARNING: existing branch is '$EXISTING_BRANCH', requested '$BRANCH'." >&2
        echo "  Leaving as-is. Reconcile manually if needed." >&2
    fi
else
    echo "Fetching origin..."
    git fetch origin main
    echo "Creating worktree at $WORKTREE on branch '$BRANCH' from origin/main..."
    git worktree add -b "$BRANCH" "$WORKTREE" origin/main
fi

BASE_COMMIT="$(git -C "$WORKTREE" rev-parse --short HEAD)"

# Refresh venv symlink (relative path so the link survives if the parent
# of the worktree moves alongside the parent of the main repo).
VENV_TARGET="../${REPO_NAME}/.venv"
VENV_LINK="$WORKTREE/.venv"
if [ ! -e "$REPO/.venv" ]; then
    echo "WARNING: $REPO/.venv does not exist; skipping venv symlink." >&2
    echo "         Codex will fall back to the system Python — expect ABI traps." >&2
elif [ -L "$VENV_LINK" ] && [ "$(readlink "$VENV_LINK")" = "$VENV_TARGET" ]; then
    echo "venv symlink already correct: $VENV_LINK -> $VENV_TARGET"
else
    rm -f "$VENV_LINK"
    ln -s "$VENV_TARGET" "$VENV_LINK"
    echo "venv symlinked: $VENV_LINK -> $VENV_TARGET"
fi

# Refresh .envrc with PYTHON pinned to the venv-symlink path.
ENVRC="$WORKTREE/.envrc"
cat > "$ENVRC" <<EOF
# Source this before launching codex in this worktree, or paste into
# the agent's env. Pins PYTHON to the venv to dodge system-ABI mismatch
# (SciPy/NumPy _ARRAY_API failures on host python3).
export PYTHON="${WORKTREE}/.venv/bin/python"
EOF
echo ".envrc written: $ENVRC"

cat <<EOF

Done. Worktree ready.

  worktree:    $WORKTREE
  branch:      $BRANCH
  base commit: ${BASE_COMMIT} ($(git -C "$WORKTREE" log -1 --format=%s 2>/dev/null || echo '?'))
  venv:        $VENV_LINK -> $VENV_TARGET

Hand the codex agent this env:

  cd "$WORKTREE"
  source .envrc

Then paste the codex-handoff prompt
(see ~/.claude/skills/threads/assets/templates/codex-handoff-prompt.md)
with substitutions filled in from the thread's handoff.md.

EOF

if [ "$EXISTED" -eq 0 ]; then
    TODAY="$(date +%Y-%m-%d)"
    cat <<EOF
First-time bootstrap — paste this into the thread's thread.json as
codex_worktrees[<i>] (create the array if it doesn't exist yet).
Then re-run the indexer: python3 ~/.claude/skills/threads/scripts/index_threads_research.py

  {
    "path": "${WORKTREE}",
    "branch": "${BRANCH}",
    "base_commit": "${BASE_COMMIT}",
    "started": "${TODAY}",
    "status": "active",
    "merged_into": null,
    "merged_at": null,
    "notes": ""
  }

EOF
fi
