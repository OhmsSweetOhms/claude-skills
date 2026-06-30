#!/usr/bin/env bash
#
# reconcile-pull.sh — safely pull + reconcile a threads-bearing repo across
# machines (see references/cross-machine-reconciliation.md).
#
# Generic: no project specifics. The contract it enforces:
#   - Fast-forward when the local branch is merely behind (no merge commit).
#   - On true divergence, merge — but AUTO-RESOLVE ONLY the derived registry
#     (.threads/threads.json, .research/INDEX.json) by rebuilding it with the
#     indexer, after union-seeding the two non-derived blocks (closure_log,
#     current_metrics) from both sides so no clone's closures are dropped.
#   - REFUSE to auto-merge any *content* conflict (e.g. a thread.json both
#     clones edited). Those are aborted and handed back to the operator.
#   - Never pushes. Printing the push command is as far as it goes.
#
# Usage:   reconcile-pull.sh [remote] [branch]
#   remote  default: origin
#   branch  default: current branch
#
# Env:     THREADS_INDEXER  override path to index_threads_research.py
#
# Exit:    0 up-to-date / fast-forwarded / cleanly reconciled
#          1 needs operator attention (content conflicts, dirty tree, etc.)
#          2 not a usable git/threads context

set -uo pipefail

REMOTE="${1:-origin}"
SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INDEXER="${THREADS_INDEXER:-$SELF_DIR/index_threads_research.py}"

say()  { printf '%s\n' "$*"; }
hr()   { printf -- '----------------------------------------------------------\n'; }
die()  { printf 'ERROR: %s\n' "$*" >&2; exit "${2:-1}"; }

# --- preconditions --------------------------------------------------------
git rev-parse --is-inside-work-tree >/dev/null 2>&1 \
  || die "not inside a git work tree (run from the repo)" 2
ROOT="$(git rev-parse --show-toplevel)"
cd "$ROOT"
BRANCH="${2:-$(git rev-parse --abbrev-ref HEAD)}"
UPSTREAM="$REMOTE/$BRANCH"

command -v python3 >/dev/null 2>&1 || die "python3 not found" 2
HAS_THREADS=0
[ -f "$ROOT/.threads/threads.json" ] && HAS_THREADS=1
if [ "$HAS_THREADS" = 1 ] && [ ! -f "$INDEXER" ]; then
  die "threads repo but indexer not found at: $INDEXER (set THREADS_INDEXER)" 2
fi

say "repo:     $ROOT"
say "branch:   $BRANCH    upstream: $UPSTREAM"
say "threads:  $([ "$HAS_THREADS" = 1 ] && echo yes || echo 'no (plain pull mode)')"
hr

# --- fetch + assess divergence -------------------------------------------
say "fetching $REMOTE ..."
git fetch --quiet "$REMOTE" || die "git fetch $REMOTE failed"
git rev-parse --verify --quiet "$UPSTREAM" >/dev/null \
  || die "upstream $UPSTREAM does not exist on $REMOTE" 2

read -r AHEAD BEHIND < <(git rev-list --left-right --count "HEAD...$UPSTREAM" \
                          | awk '{print $1, $2}')
say "divergence: ahead $AHEAD / behind $BEHIND (local vs $UPSTREAM)"
hr

PRE_HEAD="$(git rev-parse HEAD)"

# --- trivial cases --------------------------------------------------------
if [ "$BEHIND" -eq 0 ] && [ "$AHEAD" -eq 0 ]; then
  say "Already up to date. Nothing to do."; exit 0
fi
if [ "$BEHIND" -eq 0 ] && [ "$AHEAD" -gt 0 ]; then
  say "Nothing to pull — you have $AHEAD local commit(s) not on $UPSTREAM."
  say "When ready (after a fingerprint scan): git push $REMOTE $BRANCH"
  exit 0
fi
if [ "$AHEAD" -eq 0 ] && [ "$BEHIND" -gt 0 ]; then
  say "Behind only by $BEHIND — fast-forward (no merge commit)."
  git merge --ff-only "$UPSTREAM" || die "fast-forward failed unexpectedly"
  if [ "$HAS_THREADS" = 1 ]; then
    say "rebuilding registry from fast-forwarded state ..."
    python3 "$INDEXER" --check >/dev/null 2>&1 || python3 "$INDEXER" >/dev/null
  fi
  say "Done. Fast-forwarded $PRE_HEAD -> $(git rev-parse --short HEAD)."
  exit 0
fi

# --- diverged: must reconcile --------------------------------------------
say "Diverged (ahead $AHEAD AND behind $BEHIND) — reconciling."

# require a clean tracked tree so a failed merge is trivially recoverable
if ! git diff --quiet || ! git diff --cached --quiet; then
  say ""
  say "Working tree has uncommitted tracked changes. Commit or stash them"
  say "first, then re-run — a reconcile must start from a clean tree so an"
  say "abort restores you exactly. (Untracked files are fine.)"
  git status --short | sed 's/^/    /'
  exit 1
fi

say "merging $UPSTREAM (no auto-commit) ..."
git merge --no-commit --no-ff "$UPSTREAM" >/dev/null 2>&1
MERGE_RC=$?   # nonzero is expected when there are conflicts

CONFLICTS="$(git diff --name-only --diff-filter=U || true)"

# partition conflicts: derived registry (auto) vs everything else (handback)
REGISTRY_RE='^(\.threads/threads\.json|\.research/INDEX\.json)$'
CONTENT_CONFLICTS="$(printf '%s\n' "$CONFLICTS" | grep -vE "$REGISTRY_RE" | grep -v '^$' || true)"

if [ -n "$CONTENT_CONFLICTS" ]; then
  hr
  say "STOP — content conflicts that must be resolved by a human:"
  printf '%s\n' "$CONTENT_CONFLICTS" | sed 's/^/    /'
  say ""
  say "These are real (both clones edited the same file — typically a"
  say "thread.json / handoff / findings). This script only auto-resolves the"
  say "derived registry, never content. Aborting the merge to leave you clean."
  git merge --abort || true
  say "Merge aborted; HEAD back at $(git rev-parse --short HEAD). Resolve by hand:"
  say "  git merge $UPSTREAM   # then union the conflicted files on content,"
  say "  # rebuild the registry per references/cross-machine-reconciliation.md"
  exit 1
fi

# Only the registry (or nothing) conflicted — safe to auto-resolve.
if [ "$HAS_THREADS" = 1 ]; then
  say "auto-resolving registry by rebuild (union closure_log + preserve metrics) ..."
  OURS_JSON="$(git show "HEAD:.threads/threads.json" 2>/dev/null || echo '{}')"
  THEIRS_JSON="$(git show "MERGE_HEAD:.threads/threads.json" 2>/dev/null || echo '{}')"

  # seed on-disk threads.json with the unioned non-derived blocks, then let
  # the indexer rebuild everything derived from the merged on-disk dirs.
  OURS_JSON="$OURS_JSON" THEIRS_JSON="$THEIRS_JSON" \
  python3 - "$ROOT/.threads/threads.json" <<'PY'
import json, os, sys
out_path = sys.argv[1]
ours   = json.loads(os.environ.get("OURS_JSON")   or "{}")
theirs = json.loads(os.environ.get("THEIRS_JSON") or "{}")

seed = dict(theirs) if theirs else dict(ours)

# closure_log: append-only, de-dup by the indexer's idempotence key
merged, seen = [], set()
for e in (theirs.get("closure_log") or []) + (ours.get("closure_log") or []):
    if not isinstance(e, dict):
        continue
    k = (e.get("thread_id"), e.get("transition_date"))
    if k in seen:
        continue
    seen.add(k); merged.append(e)
seed["closure_log"] = merged

# current_metrics: asserted, not derived. Prefer the side that has it; warn if
# both have it and they differ (the operator should confirm which is current).
o_cm, t_cm = ours.get("current_metrics"), theirs.get("current_metrics")
if o_cm is not None and t_cm is not None and o_cm != t_cm:
    sys.stderr.write("  WARN: current_metrics differs between the two sides; "
                     "kept the local (HEAD) copy — verify it is the current one.\n")
chosen = o_cm if o_cm is not None else t_cm
if chosen is not None:
    seed["current_metrics"] = chosen

with open(out_path, "w") as fh:
    json.dump(seed, fh, indent=2, ensure_ascii=False)
    fh.write("\n")
print(f"  seeded closure_log union = {len(merged)} entries; "
      f"current_metrics = {'present' if chosen is not None else 'none'}")
PY
  [ $? -eq 0 ] || { git merge --abort || true; die "registry seed failed; merge aborted"; }

  python3 "$INDEXER" >/dev/null || { git merge --abort || true; die "indexer rebuild failed; merge aborted"; }
  git add .threads/threads.json .research/INDEX.json 2>/dev/null || true
fi

# stage any remaining auto-merged paths and complete the merge commit
git add -A
say "committing merge ..."
git commit --no-edit >/dev/null || die "merge commit failed (check the commit-msg hook output)"

# --- verify ---------------------------------------------------------------
hr
if [ "$HAS_THREADS" = 1 ]; then
  say "registry self-check:"
  python3 "$INDEXER" --check 2>&1 | sed 's/^/    /'
  if [ "${PIPESTATUS[0]}" -ne 0 ]; then
    say "  WARN: --check reports drift; inspect before pushing."
  fi
fi
say ""
say "Reconciled: $PRE_HEAD -> $(git rev-parse --short HEAD) (merge commit)."
read -r AHEAD2 BEHIND2 < <(git rev-list --left-right --count "HEAD...$UPSTREAM" | awk '{print $1, $2}')
say "Now ahead $AHEAD2 / behind $BEHIND2 vs $UPSTREAM."
say ""
say "NOT pushed. To publish (fingerprint-scan first if this repo requires it):"
say "  python3 ~/.claude/skills/fingerprint/fingerprint_scan.py --scan-unpushed ."
say "  git push $REMOTE $BRANCH"
exit 0
