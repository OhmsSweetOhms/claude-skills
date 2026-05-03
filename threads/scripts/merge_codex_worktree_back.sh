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
#
# If main carries only closure-session bookkeeping in .threads/ and/or
# .research/INDEX.json, the script offers to stash that overlay, merge the
# worktree branch, then pop the overlay back for the follow-up bookkeeping
# commit. Non-bookkeeping dirty state is still refused.

set -euo pipefail

WORKTREE=""
REPO=""
REWRITE=0
BOOKKEEPING_STASHED=0
BOOKKEEPING_STASH_REF=""
BOOKKEEPING_STASH_MSG=""
BOOKKEEPING_POP_CONFLICT=0

while [ $# -gt 0 ]; do
    case "$1" in
        --repo)          REPO="$2"; shift 2 ;;
        --rewrite-paths) REWRITE=1; shift ;;
        --help|-h)       sed -n '2,21p' "$0"; exit 0 ;;
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

dirty_main_paths() {
    git -C "$REPO" status --porcelain --untracked-files=all |
        while IFS= read -r line; do
            path="${line:3}"
            case "$path" in
                *" -> "*)
                    printf '%s\n' "${path%% -> *}"
                    printf '%s\n' "${path#* -> }"
                    ;;
                *)
                    printf '%s\n' "$path"
                    ;;
            esac
        done |
        sort -u
}

is_bookkeeping_path() {
    case "$1" in
        .threads|.threads/*|.research/INDEX.json) return 0 ;;
        *)                                        return 1 ;;
    esac
}

print_path_list() {
    local title="$1"
    shift
    echo "$title"
    for f in "$@"; do
        echo "  $f"
    done
}

BRANCH="$(git -C "$WORKTREE" rev-parse --abbrev-ref HEAD)"
if [ "$BRANCH" = "main" ] || [ "$BRANCH" = "HEAD" ]; then
    echo "worktree is on '$BRANCH'; refusing (need a feature branch)" >&2
    exit 2
fi

# Pre-flight: main must be on main; closure bookkeeping can be stashed.
MAIN_BRANCH="$(git -C "$REPO" rev-parse --abbrev-ref HEAD)"
if [ "$MAIN_BRANCH" != "main" ]; then
    echo "main checkout is on '$MAIN_BRANCH', not 'main'; aborting" >&2
    echo "  (cd $REPO && git checkout main) first" >&2
    exit 2
fi

MAIN_DIRTY_PATHS=()
mapfile -t MAIN_DIRTY_PATHS < <(dirty_main_paths)
if [ "${#MAIN_DIRTY_PATHS[@]}" -gt 0 ]; then
    BOOKKEEPING_DIRTY_PATHS=()
    OTHER_DIRTY_PATHS=()
    for f in "${MAIN_DIRTY_PATHS[@]}"; do
        if is_bookkeeping_path "$f"; then
            BOOKKEEPING_DIRTY_PATHS+=("$f")
        else
            OTHER_DIRTY_PATHS+=("$f")
        fi
    done

    echo "main checkout has uncommitted state:"
    for f in "${MAIN_DIRTY_PATHS[@]}"; do
        echo "  $f"
    done
    echo

    if [ "${#OTHER_DIRTY_PATHS[@]}" -gt 0 ]; then
        print_path_list "Non-bookkeeping dirty paths:" "${OTHER_DIRTY_PATHS[@]}" >&2
        echo >&2
        echo "Refusing to merge with non-bookkeeping dirty state on main." >&2
        echo "Park those changes separately, then re-run merge-back." >&2
        exit 2
    fi

    print_path_list "Closure-session bookkeeping candidates:" "${BOOKKEEPING_DIRTY_PATHS[@]}"
    echo
    echo "This is the common closure-session case: main already has"
    echo ".threads/.research bookkeeping that should become a follow-up"
    echo "commit after the worktree merge."
    echo
    echo "Options:"
    echo "  (a) abort so you can commit or stash manually."
    echo "  (s) stash this bookkeeping now, merge the worktree branch,"
    echo "      then pop the bookkeeping overlay back afterwards."
    echo
    read -r -p "Choice [a/s]: " MAIN_DIRTY_CHOICE
    case "$MAIN_DIRTY_CHOICE" in
        s|S)
            BOOKKEEPING_STASH_MSG="threads merge-back bookkeeping overlay for $BRANCH $(date +%Y-%m-%dT%H%M%S)"
            git -C "$REPO" stash push -u -m "$BOOKKEEPING_STASH_MSG" -- "${BOOKKEEPING_DIRTY_PATHS[@]}"
            BOOKKEEPING_STASHED=1
            BOOKKEEPING_STASH_REF="stash@{0}"
            MAIN_DIRTY_AFTER_STASH=()
            mapfile -t MAIN_DIRTY_AFTER_STASH < <(dirty_main_paths)
            if [ "${#MAIN_DIRTY_AFTER_STASH[@]}" -gt 0 ]; then
                print_path_list "main still has dirty paths after bookkeeping stash:" "${MAIN_DIRTY_AFTER_STASH[@]}" >&2
                echo "Aborting before merge. Restore the stash with:" >&2
                echo "  git -C \"$REPO\" stash pop $BOOKKEEPING_STASH_REF" >&2
                exit 2
            fi
            ;;
        a|A|"")
            echo "aborting. Commit or stash bookkeeping, then re-run."
            exit 0
            ;;
        *)
            echo "unrecognised choice; aborting" >&2
            exit 2
            ;;
    esac
fi

# Inspect worktree state: any modified or untracked files?
# Filter out worktree-local infrastructure that bootstrap created
# (.envrc, .venv symlink, .codex-hooks). git's per-worktree
# exclude is shared across all worktrees in the common gitdir, so
# we filter here in the merge-back instead of in bootstrap.
UNCOMMITTED=$({ git -C "$WORKTREE" diff --name-only HEAD; \
                git -C "$WORKTREE" ls-files --others --exclude-standard; } \
              | grep -Ev '^(\.envrc|\.venv|\.venv/.*|\.codex-hooks|\.codex-hooks/.*)$' \
              | sort -u || true)

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
    if [ "$BOOKKEEPING_STASHED" -eq 1 ]; then
        echo >&2
        echo "Bookkeeping overlay is still stashed as $BOOKKEEPING_STASH_REF." >&2
        echo "After the merge is continued or aborted, restore it with:" >&2
        echo "  git -C \"$REPO\" stash pop $BOOKKEEPING_STASH_REF" >&2
    fi
    exit 1
fi

MERGE_COMMIT="$(git rev-parse --short HEAD)"
TODAY="$(date +%Y-%m-%d)"

if [ "$BOOKKEEPING_STASHED" -eq 1 ]; then
    echo
    echo "Restoring main-side bookkeeping overlay from $BOOKKEEPING_STASH_REF..."
    if git stash pop "$BOOKKEEPING_STASH_REF"; then
        echo "Bookkeeping overlay restored. It is intentionally uncommitted."
    else
        BOOKKEEPING_POP_CONFLICT=1
        echo >&2
        echo "Bookkeeping overlay pop reported conflicts or failed." >&2
        echo "Resolve the bookkeeping overlay, then commit it as the" >&2
        echo "post-merge closure/bookkeeping commit. Git usually keeps" >&2
        echo "the stash entry when pop conflicts; do not drop it until" >&2
        echo "the overlay is recovered." >&2
    fi
fi

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

EOF

if [ "$BOOKKEEPING_STASHED" -eq 1 ]; then
    cat <<EOF

Main-side bookkeeping overlay:

  The script stashed .threads/.research bookkeeping before the merge
  and popped it back afterwards.

  Stash message:
    $BOOKKEEPING_STASH_MSG

  Review the restored overlay before committing:
    git status
    git diff

  If stash pop produced conflicts, resolve them now. The follow-up
  bookkeeping commit should carry the closure-session state, plus the
  actual merge metadata from this run:
    codex_worktrees[<i>].merged_into = "$MERGE_COMMIT"
    codex_worktrees[<i>].merged_at   = "$TODAY"

EOF
fi

cat <<EOF

Next steps (run by hand, in this order):

  1. .venv/bin/python -m unittest <focused tests>
        — confirm the merged tree passes. Use the venv Python.

  2. Update the thread's thread.json to mark the worktree merged:
        codex_worktrees[<i>].status        = "merged"
        codex_worktrees[<i>].merged_into   = "$MERGE_COMMIT"
        codex_worktrees[<i>].merged_at     = "$TODAY"

  3. Update the thread's handoff.md with a session-log entry
     describing the close + merge transition.

  4. Run or verify the **Close thread** workflow
     (sets thread.status = closed).

  5. Regenerate the registry:
        python3 ~/.claude/skills/threads/scripts/index_threads_research.py

  6. Commit the thread bookkeeping + threads.json + INDEX.json
     together (single post-merge bookkeeping commit).

  7. Clean up the worktree:
        git worktree remove "$WORKTREE"
        git branch -d "$BRANCH"

EOF

if [ "$BOOKKEEPING_POP_CONFLICT" -eq 1 ]; then
    exit 1
fi
