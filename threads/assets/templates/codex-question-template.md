---
id: q-{{NN}}
plan_id: {{PLAN_ID}}
thread_id: {{THREAD_ID}}
status: open
asked: {{ISO_TIMESTAMP}}
answered: null
---

## Question (Codex)

<!-- State the ambiguity precisely:
     - What decision does the plan / ADRs / golden vectors fail to pin?
     - Each candidate reading, with the evidence for it (file:line,
       vector values, ADR phrase).
     - What you will do with the answer (which file/gate it unblocks).
     Do NOT proceed on an assumed reading while waiting. -->

## Resolution (main session)

<!-- Written by the main session. status transitions:
       open -> answered     resolvable from pinned authority (ADR text,
                            golden source, committed vectors) — cite it.
       open -> escalated    requires a NEW decision; the user is deciding.
                            Stays escalated until decided, then -> answered.
     USE THE ATOMIC WRITER — do not hand-edit the flip:
       python3 ~/.claude/skills/threads/scripts/answer_question.py <this file> \
           --status answered --body-file <resolution.md> --by <session-name>
     (a PreToolUse guard denies a status flip without a body). Two hand
     edits are not atomic and the asker keys on `status`; a status-first
     edit is a question that reads "answered" with an empty body.
     If the contract doc was corrected as part of the answer, link the
     commit. Keep paths repo-relative (fingerprint discipline). -->
