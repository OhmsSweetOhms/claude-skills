#!/usr/bin/env bash
# Codex side of the ambiguity mailbox (references/codex-handoff.md
# §"Ambiguity mailbox"). Blocks until the main session resolves a
# question file, polling its frontmatter status.
#
# Usage:
#   await_codex_answer.sh <question-file> [timeout-s] [interval-s]
#
#   timeout-s   default 3600 (1 h cap, matching the main-session
#               watcher; a blocked shell costs Codex zero tokens)
#   interval-s  default 30
#
# Status semantics:
#   answered   -> exit 0; read the "## Resolution" section and proceed.
#   escalated  -> keep waiting (a user-level decision is in flight);
#                 the transition is printed once.
#   open       -> keep waiting.
#
# Exit codes:
#   0  answered.
#   3  timeout. Set 'status: timeout' in the question file, record the
#      question as a blocker AND an investigations[] entry, and write
#      the handback (status: gate-incomplete or blocked).
set -u

QFILE="${1:?usage: await_codex_answer.sh <question-file> [timeout-s] [interval-s]}"
TIMEOUT="${2:-3600}"
INTERVAL="${3:-30}"

deadline=$(( $(date +%s) + TIMEOUT ))
last=""
while [ "$(date +%s)" -lt "$deadline" ]; do
  st=$(grep -m1 '^status:' "$QFILE" 2>/dev/null | awk '{print $2}')
  if [ "${st:-}" != "$last" ]; then
    echo "status: ${st:-missing}"
    last="${st:-}"
  fi
  if [ "${st:-}" = "answered" ]; then
    exit 0
  fi
  sleep "$INTERVAL"
done
echo "TIMEOUT after ${TIMEOUT}s (last status: ${last:-none})"
exit 3
