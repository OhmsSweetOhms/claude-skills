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
#       [--thread-id <subsystem/YYYYMMDD-slug>] [--plan-id plan-NN]
#       [--render-prompt-out <path>] [--adjacent-threads <path>]
#
# Defaults:
#   --repo:   git rev-parse --show-toplevel (current main checkout)
#   --branch: <thread-slug> (matches the worktree dir suffix)
#
# Outputs to stdout:
#   - worktree path, branch, base commit, venv link target
#   - JSON snippet to paste into the thread's thread.json (first creation only)
#   - optional codex handoff scaffold path when --render-prompt-out is used

set -euo pipefail

SLUG=""
REPO=""
BRANCH=""
THREAD_ID=""
PLAN_ID=""
RENDER_PROMPT_OUT=""
ADJACENT_THREADS=""
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

while [ $# -gt 0 ]; do
    case "$1" in
        --repo)              REPO="$2"; shift 2 ;;
        --branch)            BRANCH="$2"; shift 2 ;;
        --thread-id)         THREAD_ID="$2"; shift 2 ;;
        --plan-id)           PLAN_ID="$2"; shift 2 ;;
        --render-prompt-out) RENDER_PROMPT_OUT="$2"; shift 2 ;;
        --adjacent-threads)  ADJACENT_THREADS="$2"; shift 2 ;;
        --help|-h)           sed -n '2,23p' "$0"; exit 0 ;;
        -*)                  echo "unknown flag: $1" >&2; exit 2 ;;
        *)                   SLUG="$1"; shift ;;
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

infer_thread_id() {
    local slug="$1"
    local -a matches=()
    local path rel

    if [ ! -d "$REPO/.threads" ]; then
        return 1
    fi

    while IFS= read -r path; do
        rel="${path#"$REPO/.threads/"}"
        matches+=("$rel")
    done < <(
        find "$REPO/.threads" -mindepth 2 -maxdepth 2 -type d \
            \( -name "$slug" -o -name "*-$slug" \) 2>/dev/null | sort
    )

    if [ "${#matches[@]}" -eq 1 ]; then
        printf '%s\n' "${matches[0]}"
        return 0
    fi
    if [ "${#matches[@]}" -gt 1 ]; then
        echo "ambiguous thread slug '$slug'; pass --thread-id explicitly." >&2
        printf '  matches:\n' >&2
        printf '    %s\n' "${matches[@]}" >&2
        return 1
    fi
    return 1
}

infer_plan_id() {
    local thread_id="$1"
    local thread_json="$REPO/.threads/$thread_id/thread.json"
    if [ ! -f "$thread_json" ]; then
        return 1
    fi
    python3 -c '
import json, re, sys
with open(sys.argv[1]) as f:
    data = json.load(f)
current = data.get("current_plan") or ""
if not current:
    hops = data.get("plan_hops") or []
    active = [h for h in hops if h.get("status") == "active" and h.get("file")]
    if active:
        current = sorted(active, key=lambda h: h.get("num", 0))[-1]["file"]
match = re.search(r"(plan-\d+)", current)
if match:
    print(match.group(1))
' "$thread_json"
}

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

if [ -n "$RENDER_PROMPT_OUT" ]; then
    if [ -z "$THREAD_ID" ]; then
        THREAD_ID="$(infer_thread_id "$SLUG" || true)"
    fi
    if [ -z "$THREAD_ID" ]; then
        echo "cannot render prompt scaffold: pass --thread-id <subsystem/YYYYMMDD-slug>" >&2
        exit 2
    fi
    if [ -z "$PLAN_ID" ]; then
        PLAN_ID="$(infer_plan_id "$THREAD_ID" || true)"
    fi
    if [ -z "$PLAN_ID" ]; then
        echo "cannot render prompt scaffold: pass --plan-id plan-NN" >&2
        exit 2
    fi

    RENDER_CMD=(
        python3 "$SCRIPT_DIR/render_codex_handoff.py"
        --main-repo "$REPO"
        --worktree-path "$WORKTREE"
        --thread-id "$THREAD_ID"
        --plan-id "$PLAN_ID"
        --out "$RENDER_PROMPT_OUT"
    )
    if [ -n "$ADJACENT_THREADS" ]; then
        RENDER_CMD+=(--adjacent-threads "$ADJACENT_THREADS")
    fi
    "${RENDER_CMD[@]}"
fi

cat <<EOF

Done. Worktree ready.

  worktree:    $WORKTREE
  branch:      $BRANCH
  base commit: ${BASE_COMMIT} ($(git -C "$WORKTREE" log -1 --format=%s 2>/dev/null || echo '?'))
  venv:        $VENV_LINK -> $VENV_TARGET

Hand the codex agent this env:

  cd "$WORKTREE"
  source .envrc

Then paste the hand-curated codex-handoff prompt. Generate the
scaffold with --render-prompt-out, or run:

  python3 ~/.claude/skills/threads/scripts/render_codex_handoff.py \\
      --main-repo "$REPO" \\
      --worktree-path "$WORKTREE" \\
      --thread-id "<subsystem/YYYYMMDD-slug>" \\
      --plan-id "<plan-NN>" \\
      --out ".threads/<subsystem>/<YYYYMMDD-slug>/codex-handoff-<plan-NN>.md"

Replace every HAND-CURATE marker before pasting it to codex.

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
