#!/usr/bin/env bash
# init_tracker.sh — create a session-skill tracker file for capturing
# gaps and improvement ideas while using one or more skills.
#
# Pattern borrowed from /socks-tracker. Idempotent: safe to run
# multiple times; existing tracker is left alone.
#
# Usage:
#   bash init_tracker.sh                     # default path + date
#   bash init_tracker.sh <skill1> [<skill2>] # list skills being tracked
#   bash init_tracker.sh --path <file>       # custom tracker location
#
# Default tracker path: .claude/workspace/skill-tracker-<YYYYMMDD>.md
# (resolved relative to the current working directory).

set -euo pipefail

DATE="$(date +%Y%m%d)"
TRACKER=".claude/workspace/skill-tracker-${DATE}.md"
SKILLS=()

while [ $# -gt 0 ]; do
    case "$1" in
        --path)
            TRACKER="$2"; shift 2 ;;
        --help|-h)
            sed -n '2,15p' "$0"; exit 0 ;;
        *)
            SKILLS+=("$1"); shift ;;
    esac
done

if [ ${#SKILLS[@]} -eq 0 ]; then
    SKILLS=("<skill-name>")
fi

TRACKER_DIR="$(dirname "$TRACKER")"
mkdir -p "$TRACKER_DIR"

if [ -s "$TRACKER" ]; then
    echo "tracker already exists: $TRACKER"
    echo "(non-empty; left alone — add new entries by editing directly)"
    exit 0
fi

SKILL_LIST="$(IFS=', '; echo "${SKILLS[*]}")"

cat > "$TRACKER" <<EOF
# Skill usage tracker — ${DATE}

Skills in use this session: **${SKILL_LIST}**

Log gaps, friction points, and improvement ideas here as you hit
them. Don't batch at session end — specifics get fuzzy fast.

## Entry shape

Use this template. One entry per observation.

\`\`\`
## <short entry title>
- **skill:** ${SKILL_LIST}
- **where:** path/to/file or workflow step
- **symptom:** what was confusing, missing, or wrong
- **evidence:** quote or file:line that shows it
- **suggested fix:** template tweak / doc addition / new reference
- **priority:** blocking | high | medium | low
\`\`\`

## Good triggers to log

- You had to re-read a reference twice before it made sense.
- A template placeholder didn't match what the operation needed.
- The skill's description nearly didn't trigger for a task it should.
- A workflow step was ambiguous and you had to guess.
- Domain terminology in the skill was missing or wrong for what
  you hit in the code.

Don't log every minor stylistic preference — only things worth
fixing in the skill itself.

## End-of-session pass

Before handing off:

1. Read this tracker.
2. Group entries by skill (one group per skill named above).
3. Within each group, sort by priority (blocking → high → medium → low).
4. For each entry, propose the concrete edit (file + before/after
   snippet) — do NOT apply yet; surface the list so the user decides
   which to land.
5. If this tracker is empty at session end, say so plainly. Empty
   means nothing blocked the work; that's a real result, not an
   oversight.

## Entries

<!-- Append new entries below this line. Keep the newest at the top. -->

*(no entries yet)*
EOF

echo "initialized tracker: $TRACKER"
echo "tracking skills: $SKILL_LIST"
