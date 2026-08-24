#!/usr/bin/env python3
"""Atomic answer/escalate for ambiguity-mailbox question files.

The ONLY sanctioned way to move a `q-*.md` to `answered` or `escalated`.
It writes the `## Resolution` body and flips the status in ONE atomic
replace (temp file + os.replace), so a reader can never observe
"status: answered" with an empty body — the write-order scar the
mailbox contract has re-documented for weeks.

Usage:
  answer_question.py <q-file> --status answered  --body-file <path> [--by <name>]
  answer_question.py <q-file> --status answered  --body     "<text>" [--by <name>]
  answer_question.py <q-file> --status escalated [--note "<why>"]   [--by <name>]
  cat body.md | answer_question.py <q-file> --status answered --body -

Rules enforced (exit 2 on violation, file untouched):
  * `answered` REQUIRES a non-empty Resolution body (comments/blank lines
    do not count — same test as await_codex_answer.sh).
  * `escalated` never writes a Resolution body; it appends an attributed
    escalation note under `## Resolution` only if --note is given.
  * Refuses to downgrade `answered` -> anything else.
  * Handles both frontmatter styles in use: YAML `status: open` inside a
    leading `---` block, and the bold `**status:** open` line.
  * Frontmatter `answered:` (if present) is stamped ISO-8601 UTC on answer;
    `answered_by:` / `escalated_by:` recorded from --by.

Companion PreToolUse guard: `hooks/mailbox-flip-guard.py` blocks Edit/Write
calls that would flip a question to answered without a body — this script
is the escape hatch it points at.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import os
import re
import sys
import tempfile
from pathlib import Path

RES_HDR = re.compile(r"^##[ \t]+Resolution\b.*$", re.M)
YAML_STATUS = re.compile(r"^(status:[ \t]*)(\S+)(.*)$", re.M)
# Workers sometimes write the bold header as a list item (`- **status:** open`);
# accept an optional bullet prefix so the flip still lands atomically.
BOLD_STATUS = re.compile(r"^((?:[-*][ \t]+)?\*\*status:\*\*[ \t]*)(\S+)(.*)$", re.M)


def resolution_body_nonempty(text: str) -> bool:
    m = RES_HDR.search(text)
    if not m:
        return False
    body = text[m.end():]
    nxt = re.search(r"^##[ \t]", body, re.M)
    if nxt:
        body = body[: nxt.start()]
    body = re.sub(r"<!--.*?-->", "", body, flags=re.S)
    # escalation notes are history, not a resolution
    return any(line.strip() and not line.lstrip().startswith("> **ESCALATED**")
               for line in body.splitlines())


def find_status(text: str):
    """Return (regex_match, style) for the status line, preferring YAML
    frontmatter when the file starts with '---'."""
    if text.startswith("---"):
        end = text.find("\n---", 3)
        head = text[: end + 4] if end != -1 else text
        m = YAML_STATUS.search(head)
        if m:
            return m, "yaml"
    m = BOLD_STATUS.search(text)
    if m:
        return m, "bold"
    m = YAML_STATUS.search(text)
    if m:
        return m, "yaml"
    return None, None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("qfile")
    ap.add_argument("--status", required=True, choices=["answered", "escalated"])
    ap.add_argument("--body", help="Resolution body text, or '-' for stdin")
    ap.add_argument("--body-file", help="file holding the Resolution body")
    ap.add_argument("--note", help="escalation note (escalated only)")
    ap.add_argument("--by", default=os.environ.get("MAILBOX_ANSWERED_BY", ""),
                    help="session name to record as answered_by/escalated_by")
    a = ap.parse_args()

    qp = Path(a.qfile)
    if not qp.is_file():
        print(f"error: {qp} not found", file=sys.stderr)
        return 2
    text = qp.read_text()
    m, style = find_status(text)
    if not m:
        print("error: no `status:` / `**status:**` line found", file=sys.stderr)
        return 2
    cur = m.group(2).strip().lower()
    if cur == "answered":
        print("error: already answered — refusing to rewrite (edit by hand if you must)",
              file=sys.stderr)
        return 2

    now = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    who = a.by.strip()

    if a.status == "answered":
        body = None
        if a.body_file:
            body = Path(a.body_file).read_text()
        elif a.body == "-":
            body = sys.stdin.read()
        elif a.body is not None:
            body = a.body
        if body is None or not any(l.strip() for l in
                                   re.sub(r"<!--.*?-->", "", body, flags=re.S).splitlines()):
            print("error: `answered` requires a non-empty Resolution body "
                  "(--body / --body-file / --body -)", file=sys.stderr)
            return 2
        body = body.rstrip("\n") + "\n"
        if who:
            body += f"\nAnswered by: `{who}`, {now}.\n"
        # Append the body at the END of the existing Resolution section
        # (escalation notes and template comments are history — keep them);
        # create the section if the file has none.
        hm = RES_HDR.search(text)
        if hm:
            after = text[hm.end():]
            nxt = re.search(r"^##[ \t]", after, re.M)
            existing = after[: nxt.start()] if nxt else after
            tail = after[nxt.start():] if nxt else ""
            new_text = (text[: hm.end()] + existing.rstrip("\n") + "\n\n" + body
                        + ("\n" + tail if tail else ""))
        else:
            new_text = text.rstrip("\n") + "\n\n## Resolution\n\n" + body
        # Flip status (same buffer, same write).
        m2, _ = find_status(new_text)
        s, e = m2.span(2)
        new_text = new_text[:s] + "answered" + new_text[e:]
        new_text = re.sub(r"^answered:[ \t]*null[ \t]*$", f"answered: {now}", new_text,
                          count=1, flags=re.M)
        if who and style == "yaml" and "answered_by:" not in new_text:
            new_text = re.sub(r"^(status:.*)$", r"\1\nanswered_by: " + who, new_text,
                              count=1, flags=re.M)
        if not resolution_body_nonempty(new_text):
            print("internal error: body test failed after compose; aborting", file=sys.stderr)
            return 2
    else:  # escalated
        new_text = text
        if a.note:
            note = f"\n> **ESCALATED** {now}" + (f" by `{who}`" if who else "") + f": {a.note.strip()}\n"
            hm = RES_HDR.search(new_text)
            if hm:
                new_text = new_text[: hm.end()] + "\n" + note + new_text[hm.end():]
            else:
                new_text = new_text.rstrip("\n") + "\n\n## Resolution\n" + note
        m2, _ = find_status(new_text)
        s, e = m2.span(2)
        new_text = new_text[:s] + "escalated" + new_text[e:]
        if who and style == "yaml" and "escalated_by:" not in new_text:
            new_text = re.sub(r"^(status:.*)$", r"\1\nescalated_by: " + who, new_text,
                              count=1, flags=re.M)

    # Atomic replace.
    fd, tmp = tempfile.mkstemp(dir=str(qp.parent), prefix=".q-tmp-", suffix=".md")
    with os.fdopen(fd, "w") as f:
        f.write(new_text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, qp)
    print(f"{a.status.upper()} {qp}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
