#!/usr/bin/env bash
# merge_codex_worktree_back.sh — land a long-lived Codex worktree branch
# back onto main at thread close.
#
# This is a TERMINAL operation per thread. It runs once, only when the
# user explicitly requests it, and only after all plan hops are
# resolved. The script never auto-merges: it inspects the worktree
# state, prints the incoming diff/log, and asks for explicit
# confirmation before running `git merge`.
#
# Usage:
#   bash merge_codex_worktree_back.sh <worktree-path> [--repo <main-path>] [--rewrite-paths]
#
# --rewrite-paths runs sed substitutions on tracked files in main after
# the merge for known stale-layout symbols (e.g. gps_receiver/threads ->
# .threads). Skip unless the worktree branched off a stale-layout commit.

set -euo pipefail

WORKTREE=""
REPO=""
REWRITE=0

while [ $# -gt 0 ]; do
    case "$1" in
        --repo)          REPO="$2"; shift 2 ;;
        --rewrite-paths) REWRITE=1; shift ;;
        --help|-h)       sed -n '2,18p' "$0"; exit 0 ;;
        -*)              echo "unknown flag: $1" >&2; exit 2 ;;
        *)               WORKTREE="$1"; shift ;;
    esac
done

if [ -z "$WORKTREE" ]; then
    echo "usage: merge_codex_worktree_back.sh <worktree-path>" >&2
    exit 2
fi

WORKTREE="$(realpath "$WORKTREE")"
if [ ! -d "$WORKTREE/.git" ] && [ ! -f "$WORKTREE/.git" ]; then
    echo "not a worktree: $WORKTREE" >&2
    exit 2
fi

if [ -z "$REPO" ]; then
    REPO="$(git rev-parse --show-toplevel)"
fi
REPO="$(realpath "$REPO")"

if [ "$WORKTREE" = "$REPO" ]; then
    echo "worktree path equals main repo path; refusing" >&2
    exit 2
fi

BRANCH="$(git -C "$WORKTREE" rev-parse --abbrev-ref HEAD)"
if [ "$BRANCH" = "main" ] || [ "$BRANCH" = "HEAD" ]; then
    echo "worktree is on '$BRANCH'; refusing (need a feature branch)" >&2
    exit 2
fi

# Pre-flight: main must be on main and clean.
MAIN_BRANCH="$(git -C "$REPO" rev-parse --abbrev-ref HEAD)"
if [ "$MAIN_BRANCH" != "main" ]; then
    echo "main checkout is on '$MAIN_BRANCH', not 'main'; aborting" >&2
    echo "  (cd $REPO && git checkout main) first" >&2
    exit 2
fi
if ! git -C "$REPO" diff --quiet || ! git -C "$REPO" diff --cached --quiet; then
    echo "main checkout has uncommitted changes; aborting" >&2
    echo "  commit, stash, or discard them first" >&2
    exit 2
fi

# Inspect worktree state: any modified or untracked files?
# Filter out worktree-local infrastructure that bootstrap created
# (.envrc, .venv symlink, pycache prefix dir). git's per-worktree
# exclude is shared across all worktrees in the common gitdir, so
# we filter here in the merge-back instead of in bootstrap.
UNCOMMITTED=$({ git -C "$WORKTREE" diff --name-only HEAD; \
                git -C "$WORKTREE" ls-files --others --exclude-standard; } \
              | grep -Ev '^(\.envrc|\.venv|\.venv/.*)$' \
              | sort -u)

if [ -n "$UNCOMMITTED" ]; then
    echo "Worktree has UNCOMMITTED state on branch '$BRANCH':"
    echo "$UNCOMMITTED" | sed 's/^/  /'
    echo
    echo "Per the codex-handoff convention, codex output should have been"
    echo "committed onto the worktree branch before merge-back. You have"
    echo "three options:"
    echo "  (a) abort, commit on the worktree branch yourself, then re-run."
    echo "  (b) copy the uncommitted state into main as-is (skip the"
    echo "      worktree commit and merge only the existing branch history;"
    echo "      uncommitted files land as a working-tree edit on main)."
    echo "  (c) merge only the committed history (uncommitted state stays"
    echo "      in the worktree)."
    echo
    read -r -p "Choice [a/b/c]: " CHOICE
    case "$CHOICE" in
        a|A) echo "aborting. cd $WORKTREE && git add ... && git commit ..."; exit 0 ;;
        b|B) COPY_UNCOMMITTED=1 ;;
        c|C) COPY_UNCOMMITTED=0 ;;
        *)   echo "unrecognised choice; aborting"; exit 2 ;;
    esac
else
    COPY_UNCOMMITTED=0
fi

# Show the user what's about to be merged.
cd "$REPO"
git fetch --quiet 2>/dev/null || true

echo
echo "============================================================"
echo "Incoming on branch '$BRANCH' vs main:"
echo "============================================================"
echo "Commits:"
git log --oneline "main..$BRANCH" 2>/dev/null | sed 's/^/  /' || echo "  (no new commits)"
echo
echo "Diff stat:"
git diff --stat "main...$BRANCH" 2>/dev/null | sed 's/^/  /' || echo "  (no diff)"
echo

if [ "${COPY_UNCOMMITTED:-0}" -eq 1 ]; then
    echo "Plus uncommitted state to be copied (option b):"
    echo "$UNCOMMITTED" | sed 's/^/  /'
    echo
fi

read -r -p "Proceed with merge of '$BRANCH' into main? [y/N]: " CONFIRM
case "$CONFIRM" in
    y|Y|yes|YES) ;;
    *)           echo "aborted by user."; exit 0 ;;
esac

# Step 1: copy uncommitted state into main if option (b) was chosen.
if [ "${COPY_UNCOMMITTED:-0}" -eq 1 ]; then
    while IFS= read -r f; do
        [ -z "$f" ] && continue
        mkdir -p "$REPO/$(dirname "$f")"
        cp -p "$WORKTREE/$f" "$REPO/$f"
    done <<< "$UNCOMMITTED"
    echo "Uncommitted files copied to main working tree (not yet staged)."
fi

# Step 2: merge the branch with --no-ff to preserve lineage.
echo
echo "Running: git merge --no-ff $BRANCH"
echo "  (you'll be dropped into \$EDITOR for the merge commit body —"
echo "   describe the thread's outcome, plan hops landed, key measurements)"
echo
if ! git merge --no-ff "$BRANCH"; then
    echo
    echo "merge FAILED. The repo is in a half-merged state — resolve" >&2
    echo "conflicts, then 'git merge --continue' or 'git merge --abort'." >&2
    exit 1
fi

MERGE_COMMIT="$(git rev-parse --short HEAD)"
TODAY="$(date +%Y-%m-%d)"

# Step 3: optional path-substitution for stale-layout fixups.
if [ "$REWRITE" -eq 1 ]; then
    echo
    echo "Rewriting stale-layout paths in tracked files..."
    # Add new mappings here as future layout moves accrue.
    git ls-files | while IFS= read -r f; do
        if [ -f "$f" ] && file --mime-type "$f" | grep -q text; then
            sed -i 's|gps_receiver/threads|.threads|g' "$f"
        fi
    done
    if ! git diff --quiet; then
        echo "Path-substitution produced edits. Inspect 'git diff' and commit" >&2
        echo "as a follow-up to the merge." >&2
    else
        echo "No stale-layout symbols found."
    fi
fi

cat <<EOF

Merge committed: $MERGE_COMMIT

Next steps (run by hand, in this order):

  1. .venv/bin/python -m unittest <focused tests>
        — confirm the merged tree passes. Use the venv Python.

  2. Update the thread's thread.json to mark the worktree merged:
        codex_worktrees[<i>].status        = "merged"
        codex_worktrees[<i>].merged_into   = "$MERGE_COMMIT"
        codex_worktrees[<i>].merged_at     = "$TODAY"

  3. Update the thread's handoff.md with a session-log entry
     describing the close + merge transition.

  4. Run the **Close thread** workflow (sets thread.status = closed).

  5. Regenerate the registry:
        python3 ~/.claude/skills/threads/scripts/index_threads_research.py

  6. Commit thread.json + handoff.md + threads.json + INDEX.json
     together (single bookkeeping commit).

  7. Clean up the worktree:
        git worktree remove "$WORKTREE"
        git branch -d "$BRANCH"

EOF
