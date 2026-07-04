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
#   answered   -> exit 0, but ONLY once the "## Resolution" section also
#                 carries a real (non-comment, non-blank) line. The main
#                 session writes the body and flips `status:` in two
#                 separate, non-atomic edits; keying on status alone lets
#                 this poller read a status-first edit as an answered
#                 question with an empty body, which has twice caused a
#                 wasted duplicate q-NN round trip (see the mailbox
#                 section of references/codex-handoff.md). Requiring a
#                 non-empty body degrades an out-of-order edit to
#                 continued waiting instead.
#   escalated  -> keep waiting (a user-level decision is in flight);
#                 the transition is printed once.
#   open       -> keep waiting.
#
# Exit codes:
#   0  answered (status: answered AND a non-empty Resolution body).
#   3  timeout. Set 'status: timeout' in the question file, record the
#      question as a blocker AND an investigations[] entry, and write
#      the handback (status: gate-incomplete or blocked).
set -u

QFILE="${1:?usage: await_codex_answer.sh <question-file> [timeout-s] [interval-s]}"
TIMEOUT="${2:-3600}"
INTERVAL="${3:-30}"

# Print "1" iff the "## Resolution" section holds a non-comment,
# non-blank line. Handles multi-line <!-- --> comment blocks (the
# template ships one) and stops at the next "## " heading or EOF.
resolution_has_body() {
  awk '
    /^##[ \t]+Resolution/ { inres=1; next }
    /^##[ \t]/            { if (inres) inres=0 }
    inres {
      s=$0
      while (length(s) > 0) {
        if (incomment) {
          p=index(s,"-->"); if (p==0) { s="" } else { s=substr(s,p+3); incomment=0 }
        } else {
          p=index(s,"<!--")
          if (p==0) { chunk=s; s="" } else { chunk=substr(s,1,p-1); s=substr(s,p+4); incomment=1 }
          gsub(/[ \t\r]/,"",chunk); if (length(chunk) > 0) { print "1"; exit }
        }
      }
    }
  ' "$1"
}

deadline=$(( $(date +%s) + TIMEOUT ))
last=""
warned_empty=0
while [ "$(date +%s)" -lt "$deadline" ]; do
  st=$(grep -m1 '^status:' "$QFILE" 2>/dev/null | awk '{print $2}')
  if [ "${st:-}" != "$last" ]; then
    echo "status: ${st:-missing}"
    last="${st:-}"
  fi
  if [ "${st:-}" = "answered" ]; then
    if [ -n "$(resolution_has_body "$QFILE")" ]; then
      exit 0
    elif [ "$warned_empty" -eq 0 ]; then
      echo "status: answered but '## Resolution' body is still empty — waiting" \
           "(main session: write the body, THEN flip status)"
      warned_empty=1
    fi
  fi
  sleep "$INTERVAL"
done
echo "TIMEOUT after ${TIMEOUT}s (last status: ${last:-none})"
exit 3
