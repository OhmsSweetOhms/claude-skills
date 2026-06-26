#!/usr/bin/env python3
"""Pre-commit guard for threads/ record discipline (the three edit-classes).

Enforces, over the STAGED diff, the immutability rules from CONVENTIONS
"Record discipline":

  IMMUTABLE  findings-*.md bodies        -> only a leading '> SUPERSEDED ...'
                                            blockquote banner may be ADDED; no
                                            line may be removed or otherwise added.
                                            A correction is a NEW findings file.
  APPEND-ONLY handoff.md "Session log"   -> existing dated entries may not be
                                            edited or deleted (no removed lines
                                            at/below the "## Session log" header
                                            in the OLD file). Prepending a new
                                            entry is fine. The Current-truth block
                                            (above the header) may change freely.

Scope: paths under a `.threads/` tree. New files (status A) are always allowed.
Out of scope (documented, not yet guarded): thread.json closed-hop `outcome`
and external-review verbatim sections.

Exit 0 = clean. Exit 1 = violation (commit aborted). A deliberate override is
`git commit --no-verify` — the committer's explicit choice.

Usage: run from the repo root (a git pre-commit hook does this automatically):
    python3 ~/.claude/skills/threads/scripts/check_record_discipline.py
"""
import os
import re
import subprocess
import sys

BANNER_RE = re.compile(r"^\s*>")                       # blockquote line = allowed banner
FINDINGS_RE = re.compile(r"findings-\d{4}-\d{2}-\d{2}.*\.md$")
HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
SESSION_LOG_HEADER = "## Session log"


def _run(args):
    return subprocess.run(args, capture_output=True, text=True).stdout


def staged_files():
    out = _run(["git", "diff", "--cached", "--name-status"])
    files = []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) >= 2:
            files.append((parts[0], parts[-1]))
    return files


def staged_diff(path):
    return _run(["git", "diff", "--cached", "--unified=0", "--", path])


def in_threads_tree(path):
    return path.startswith(".threads/") or "/.threads/" in path or \
        path.startswith("threads/") or "/threads/" in path


def header_line_no(blob_ref):
    """1-based line number of the Session-log header in a git blob, or None."""
    content = _run(["git", "show", blob_ref])
    for i, line in enumerate(content.splitlines(), start=1):
        if line.strip().startswith(SESSION_LOG_HEADER):
            return i
    return None


def check_findings(path, violations):
    diff = staged_diff(path)
    added, removed = [], []
    for l in diff.splitlines():
        if l.startswith("+++") or l.startswith("---"):
            continue
        if l.startswith("+"):
            added.append(l[1:])
        elif l.startswith("-"):
            removed.append(l[1:])
    if removed:
        violations.append(
            f"{path}: findings body is IMMUTABLE — {len(removed)} line(s) removed. "
            f"A correction is a NEW findings-*.md that supersedes this one, not an edit."
        )
    bad = [a for a in added if a.strip() and not BANNER_RE.match(a)]
    if bad:
        violations.append(
            f"{path}: findings body is IMMUTABLE — only a leading "
            f"'> SUPERSEDED by findings-… ' blockquote banner may be added "
            f"({len(bad)} non-banner line(s) added). Make a NEW findings-*.md instead."
        )


def check_handoff(path, violations):
    old_hdr = header_line_no("HEAD:" + path)   # OLD file header position
    if old_hdr is None:
        return  # no session log yet (e.g. brand-new layout) — nothing to protect
    diff = staged_diff(path)
    for line in diff.splitlines():
        m = HUNK_RE.match(line)
        if not m:
            continue
        old_start = int(m.group(1))
        old_count = int(m.group(2) or "1")
        # a hunk that REMOVES/CHANGES old lines (old_count>0) starting at/below
        # the Session-log header edits an existing journal entry.
        if old_count > 0 and (old_start + old_count - 1) >= old_hdr:
            violations.append(
                f"{path}: Session log is APPEND-ONLY — a past entry at/after the "
                f"'## Session log' header (old line {old_hdr}) was edited or deleted. "
                f"Record the new understanding in a NEW entry at the top instead; "
                f"the Current-truth block above the header may change freely."
            )
            return


def main():
    violations = []
    for status, path in staged_files():
        if not in_threads_tree(path):
            continue
        if status.startswith("A"):           # new file — always allowed
            continue
        if not status.startswith("M"):       # rename/copy/delete — out of scope here
            continue
        base = os.path.basename(path)
        if FINDINGS_RE.search(base) or base.startswith("findings-"):
            check_findings(path, violations)
        elif base == "handoff.md":
            check_handoff(path, violations)

    if violations:
        sys.stderr.write("\nthreads record-discipline guard — commit BLOCKED:\n\n")
        for v in violations:
            sys.stderr.write("  • " + v + "\n")
        sys.stderr.write(
            "\nSee CONVENTIONS § 'Record discipline — three edit-classes'.\n"
            "Override (committer's explicit choice): git commit --no-verify\n\n"
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
