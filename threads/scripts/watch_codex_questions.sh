#!/usr/bin/env bash
# Main-session side of the ambiguity mailbox (references/codex-handoff.md
# §"Ambiguity mailbox"). Watches a codex-handoff inbox for an open
# question from a running Codex session.
#
# Usage:
#   watch_codex_questions.sh <inbox-dir> [timeout-s] [interval-s]
#
#   inbox-dir   <worktree>/codex-handoff/<plan-id>
#   timeout-s   default 3600 (1 h — sleep cap so idle sessions don't
#               burn tokens; Codex's own wait cap is the same)
#   interval-s  default 20
#
# Intended invocation: Bash run_in_background at Codex launch. The
# process costs zero tokens while idle and re-invokes the main session
# only when a question lands or the cap expires.
#
# Exit codes:
#   0  printed "OPEN_QUESTION <path>" — read it, resolve per the
#      answered/escalated protocol, then relaunch this watcher to
#      catch the next question.
#   2  timeout, no open question. Do not relaunch automatically.
set -u

INBOX="${1:?usage: watch_codex_questions.sh <inbox-dir> [timeout-s] [interval-s]}"
TIMEOUT="${2:-3600}"
INTERVAL="${3:-20}"
QDIR="$INBOX/questions"

deadline=$(( $(date +%s) + TIMEOUT ))
while [ "$(date +%s)" -lt "$deadline" ]; do
  if [ -d "$QDIR" ]; then
    for f in "$QDIR"/q-*.md; do
      [ -e "$f" ] || continue
      if grep -q '^status: open' "$f"; then
        echo "OPEN_QUESTION $f"
        exit 0
      fi
    done
  fi
  sleep "$INTERVAL"
done
echo "TIMEOUT no open question within ${TIMEOUT}s"
exit 2
