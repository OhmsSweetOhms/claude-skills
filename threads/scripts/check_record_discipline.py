#!/usr/bin/env python3
"""Pre-commit guard for threads/ record discipline.

Enforces, over the STAGED diff, the edit-classes from CONVENTIONS
"Record discipline":

  IMMUTABLE   findings-*.md bodies         -> only a leading '> SUPERSEDED ...'
                                              blockquote banner may be ADDED; no
                                              line may be removed or otherwise added.
                                              A correction is a NEW findings file.
  APPEND-ONLY handoff.md "Session log"     -> existing dated entries may not be
                                              edited or deleted (no removed lines
                                              at/below the "## Session log" header
                                              in the OLD file). Prepending a new
                                              entry is fine.
  BOUNDED     handoff.md "## Current truth" -> overwritten freely, but while the
                                              block is larger than CURRENT_TRUTH_BOUND
                                              bytes, EVERY commit to that handoff must
                                              make it smaller than HEAD's — even one
                                              that only adds a Session-log entry. A
                                              new handoff over the bound is refused.
                                              Only that section is measured (heading
                                              to the next '## '), the same quantity
                                              diagnose_boot_surface_sizes.py reports.
  NO PREPEND  ORCHESTRATOR-CACHE*.md       -> may not ADD a 'START HERE',
                                              'READ THIS FIRST' or 'supersedes … below'
                                              line: that is a second whiteboard on
                                              top of the first.
  SIZE-BOUND  files the PROJECT lists in   -> may not grow past max_bytes; while over,
              .threads/record-discipline.json every commit to one must shrink it. One
                                              '## ' section may be left out of the measure
                                              and held to one numbered line per entry
                                              instead (a cache's decisions in force).
  IMMUTABLE   SESSION-HANDOFF-*.md         -> never edited after creation, except
                                              adding the pinned banner line. A NEW
                                              one must carry the pinned banner in its
                                              first ten lines.
  TOP-LEVEL   directly under .threads/     -> a file or directory may be ADDED there
                                              (status A, or the new path of a rename
                                              or copy) only if its name is on the
                                              project's `top_level_allow` list in
                                              .threads/record-discipline.json. No list,
                                              no check. Existing strays are untouched
                                              until they move.

Merges: a clean `git merge` never runs pre-commit. A conflicted merge concluded
with `git commit` does, with MERGE_HEAD present: on that one commit the BOUNDED,
SIZE-BOUND, NO PREPEND, narrative and TOP-LEVEL checks are switched off (operator ruling 2026-09-20,
"Keep them ON"); the findings and Session-log checks run as they always have. `git merge --squash` sets no MERGE_HEAD, so every check runs
and an over-bound result is refused — squash-merging .threads/ is not supported.

Scope: paths under a `.threads/` tree, plus any path the project's size-bound list names. Renames and copies
reach only the TOP-LEVEL check, by their NEW path; deletions reach nothing. Out of scope (documented, not yet guarded): thread.json closed-hop
`outcome` and external-review verbatim sections.

Exit 0 = clean. Exit 1 = violation (commit aborted). A deliberate override is
`git commit --no-verify` — the committer's explicit choice.

Usage: run from the repo root (a git pre-commit hook does this automatically):
    python3 ~/.claude/skills/threads/scripts/check_record_discipline.py
Dry run over history (reports, never refuses; exits 0):
    python3 ~/.claude/skills/threads/scripts/check_record_discipline.py --range HEAD~30..HEAD
"""
import argparse
import fnmatch
import json
import os
import re
import subprocess
import sys

BANNER_RE = re.compile(r"^\s*>")                       # blockquote line = allowed findings banner
FINDINGS_RE = re.compile(r"findings-\d{4}-\d{2}-\d{2}.*\.md$")
HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
SESSION_LOG_HEADER = "## Session log"

# The Current-truth bound. Derived on the block alone: of the handoffs that have one,
# the largest under 8,192 B is 7,909 B and the smallest over is 9,912 B (plan-01 Step 1).
CURRENT_TRUTH_BOUND = 8192
CURRENT_TRUTH_HEADING = re.compile(r"^## .*[Cc]urrent.truth.*$", re.M)   # level 2 ONLY
SESSION_LOG_HEADING = re.compile(r"^## Session log", re.M)
NEXT_H2 = re.compile(r"^## ", re.M)

CACHE_RE = re.compile(r"^ORCHESTRATOR-CACHE.*\.md$")
PREPEND_MARKERS = (
    re.compile(r"START HERE"),
    re.compile(r"READ THIS FIRST"),
    re.compile(r"supersedes .* below", re.I),
)

NARRATIVE_RE = re.compile(r"^SESSION-HANDOFF-.*\.md$")
NARRATIVE_BANNER = "> Immutable session narrative — history, not a boot surface."
NARRATIVE_BANNER_WINDOW = 10                           # the banner must sit in the first N lines
SIZE_BOUNDS_FILE = ".threads/record-discipline.json"   # the project's explicit list
ENTRY_LINE_RE = re.compile(r"^\d+\. \*\*")             # one numbered, bold-led line per entry
TOP_LEVEL_KEY = "top_level_allow"                       # in the same file; the tree it guards
THREADS_ROOT = SIZE_BOUNDS_FILE.rsplit("/", 1)[0]       # is the one the file sits in


def _git(args):
    """Run git; return (returncode, stdout decoded as UTF-8)."""
    r = subprocess.run(["git", *args], capture_output=True)
    return r.returncode, r.stdout.decode("utf-8", "surrogateescape")


def _out(args):
    return _git(args)[1]


def _blob(ref):
    """Content of a git blob (`<rev>:<path>` or `:<path>` for the index), or None."""
    rc, out = _git(["show", ref])
    return out if rc == 0 else None


def nbytes(text):
    return len(text.encode("utf-8", "surrogateescape"))


# --- where the two sides of a change come from ---------------------------------------

class Staged:
    """The index against HEAD — what a pre-commit hook sees."""

    def name_status(self):
        return _out(["diff", "--cached", "--name-status"])

    def diff(self, path):
        return _out(["diff", "--cached", "--unified=0", "--", path])

    def new_blob(self, path):
        return _blob(":" + path)

    def old_blob(self, path):
        return _blob("HEAD:" + path)


class Commit:
    """A committed change against its first parent — for the --range dry run."""

    def __init__(self, sha):
        self.sha = sha

    def name_status(self):
        return _out(["diff", "--name-status", self.sha + "^", self.sha])

    def diff(self, path):
        return _out(["diff", "--unified=0", self.sha + "^", self.sha, "--", path])

    def new_blob(self, path):
        return _blob(self.sha + ":" + path)

    def old_blob(self, path):
        return _blob(self.sha + "^:" + path)


# --- helpers ---------------------------------------------------------------------------

def parse_name_status(out):
    files = []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) >= 2:
            files.append((parts[0], parts[-1]))
    return files


def in_threads_tree(path):
    return path.startswith(".threads/") or "/.threads/" in path or \
        path.startswith("threads/") or "/threads/" in path


def added_removed(diff):
    """Content lines added and removed by a one-path unified diff.

    Only lines after the first hunk header count. Skipping every line that merely
    starts with '---'/'+++' (the old approach) also skipped CONTENT lines such as a
    removed markdown '---' rule, which diff prints as '----'.
    """
    added, removed, in_hunk = [], [], False
    for line in diff.splitlines():
        if line.startswith("@@"):
            in_hunk = True
            continue
        if not in_hunk:
            continue
        if line.startswith("+"):
            added.append(line[1:])
        elif line.startswith("-"):
            removed.append(line[1:])
    return added, removed


def header_line_no(content):
    """1-based line number of the Session-log header in a file's content, or None."""
    if content is None:
        return None
    for i, line in enumerate(content.splitlines(), start=1):
        if line.strip().startswith(SESSION_LOG_HEADER):
            return i
    return None


def current_truth_block(text):
    """The '## Current truth' section — its heading to the next '## ' — or None.

    Must precede '## Session log'. The same quantity diagnose_boot_surface_sizes.py
    measures as block_bytes; the bound was derived on it, so the two must agree.
    """
    if text is None:
        return None
    m = CURRENT_TRUTH_HEADING.search(text)
    if not m:
        return None
    log = SESSION_LOG_HEADING.search(text)
    if log and m.start() > log.start():
        return None
    nxt = NEXT_H2.search(text, m.end())
    return text[m.start(): nxt.start() if nxt else len(text)]


def _clip(line, n=90):
    line = line.strip()
    return line if len(line) <= n else line[: n - 1] + "…"


# --- the checks ------------------------------------------------------------------------

def check_findings(src, path, violations):
    added, removed = added_removed(src.diff(path))
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


def survives_in_order(old_lines, new_lines):
    """True when every line of old_lines is still in new_lines, in the same order —
    that is, nothing was removed or changed; lines may have been inserted anywhere."""
    it = iter(new_lines)
    return all(any(line == candidate for candidate in it) for line in old_lines)


def check_session_log(src, path, violations):
    """No line at or below the OLD '## Session log' header may be removed or changed.

    Decided on the two files' CONTENT, not on the diff's hunk positions. A commit that
    shrinks a Current truth holding '### ' sub-headings AND prepends a Session-log entry
    makes git align the hunks so the header line itself prints as removed and re-added
    ('@@ -289 +25,5 @@' / '-## Session log'); read by position, that is an edit to a past
    entry, and the guard refused the ordinary hop close (2026-09-21, the appliance-ops
    and PL handoffs). The rule is unchanged: the old header line and everything under it
    must survive, in order, from the new header line down.
    """
    old = src.old_blob(path)
    old_hdr = header_line_no(old)                  # OLD file header position
    if old_hdr is None:
        return  # no session log yet (e.g. brand-new layout) — nothing to protect
    new = src.new_blob(path) or ""
    new_hdr = header_line_no(new)
    old_log = old.splitlines()[old_hdr - 1:]
    new_log = new.splitlines()[new_hdr - 1:] if new_hdr else []
    if not new_log or new_log[0] != old_log[0] or not survives_in_order(old_log, new_log):
        violations.append(
            f"{path}: Session log is APPEND-ONLY — a past entry at/after the "
            f"'## Session log' header (old line {old_hdr}) was edited or deleted. "
            f"Record the new understanding in a NEW entry at the top instead; "
            f"the Current-truth block above the header is the place to overwrite."
        )


def check_current_truth(src, path, status, violations):
    new_block = current_truth_block(src.new_blob(path))
    if new_block is None:
        return
    new_bytes = nbytes(new_block)
    if new_bytes <= CURRENT_TRUTH_BOUND:
        return
    old_block = None if status == "A" else current_truth_block(src.old_blob(path))
    advice = (
        "Move what is still true to a findings file or a decision entry and rewrite "
        "the block as present state only. Only the '## Current truth' section is "
        "measured — other sections above the Session log are yours."
    )
    if old_block is None:
        where = "a new handoff" if status == "A" else "a handoff that had no such block"
        violations.append(
            f"{path}: Current truth is BOUNDED at {CURRENT_TRUTH_BOUND} B — the block "
            f"is {new_bytes} B in {where}, so the bound applies outright. {advice}"
        )
        return
    old_bytes = nbytes(old_block)
    if new_bytes < old_bytes:
        return  # over the bound but shrinking
    # Operator ruling 2026-09-20 ("literal reading"): ANY commit to a handoff whose block
    # is over the bound must shrink it — including one that leaves the block byte-identical
    # (a Session-log entry, another session's ORCHESTRATOR NOTE).
    if new_block == old_block:
        what = "and this commit leaves it unchanged"
    else:
        what = f"and not smaller than HEAD's {old_bytes} B"
    violations.append(
        f"{path}: Current truth is BOUNDED at {CURRENT_TRUTH_BOUND} B — the block is "
        f"{new_bytes} B {what}. Every commit to a handoff whose block is over the bound "
        f"must shrink that block. {advice}"
    )


def check_cache_prepend(src, path, violations):
    added, _ = added_removed(src.diff(path))
    hits = [a for a in added if any(p.search(a) for p in PREPEND_MARKERS)]
    if hits:
        shown = "; ".join(repr(_clip(h)) for h in hits[:3])
        violations.append(
            f"{path}: an orchestrator cache may not gain a prepend marker "
            f"('START HERE' / 'READ THIS FIRST' / 'supersedes … below') — "
            f"{len(hits)} added line(s): {shown}. That is a second whiteboard on top of "
            f"the first; rewrite the charter thread's Current truth instead."
        )


def check_narrative_modified(src, path, violations):
    added, removed = added_removed(src.diff(path))
    bad = [a for a in added if a.strip() and a.strip() != NARRATIVE_BANNER]
    if removed or bad:
        violations.append(
            f"{path}: a SESSION-HANDOFF narrative is IMMUTABLE — {len(removed)} line(s) "
            f"removed, {len(bad)} non-banner line(s) added. The only edit allowed is "
            f"adding the pinned banner line. Put a correction in Current truth, or "
            f"write a new dated narrative."
        )


def check_narrative_new(src, path, violations):
    content = src.new_blob(path) or ""
    head = content.splitlines()[:NARRATIVE_BANNER_WINDOW]
    if not any(line.strip() == NARRATIVE_BANNER for line in head):
        violations.append(
            f"{path}: a new SESSION-HANDOFF narrative must carry the pinned banner in "
            f"its first {NARRATIVE_BANNER_WINDOW} lines, exactly:\n"
            f"      {NARRATIVE_BANNER}"
        )


def size_bounds(src, violations):
    """{path: entry} from the project's SIZE_BOUNDS_FILE as this change leaves it, or {}.

    The list is EXPLICIT and lives in the project, never in this script: a blanket bound
    on ORCHESTRATOR-CACHE*.md would refuse every new decision in a lane whose cache has
    not been filtered yet.
    """
    raw = src.new_blob(SIZE_BOUNDS_FILE)
    if raw is None:
        return {}
    try:
        return {e["path"]: e for e in json.loads(raw)["size_bounds"]}
    except (ValueError, KeyError, TypeError) as exc:
        violations.append(f"{SIZE_BOUNDS_FILE}: not readable as {{\"size_bounds\": [{{\"path\", "
                          f"\"max_bytes\", …}}]}} — {exc!r}")
        return {}


def top_level_allow(src, violations):
    """The project's `top_level_allow` list as this change leaves it, or None (no check).

    Each entry is a name or a glob for a FILE directly under the threads root, or, with a
    trailing '/', for a DIRECTORY there. An entry may be an object {"name": …, "until": …}
    so a temporary admission says when it leaves. The list is the project's, like the
    size bounds: plan-03 of the context-diet thread (2026-09-21) put it there so that a
    session that writes a playbook, a brainstorm or a review prompt is told where it goes
    instead of dropping it at the top.
    """
    raw = src.new_blob(SIZE_BOUNDS_FILE)
    if raw is None:
        return None
    try:
        allow = json.loads(raw).get(TOP_LEVEL_KEY)
    except (ValueError, AttributeError):
        return None                                    # size_bounds() reported the file
    if allow is None:
        return None
    try:
        names = [e["name"] if isinstance(e, dict) else e for e in allow]
        if not all(isinstance(n, str) and n for n in names):
            raise TypeError("every entry is a name or {\"name\": …}")
    except (KeyError, TypeError) as exc:
        violations.append(f"{SIZE_BOUNDS_FILE}: `{TOP_LEVEL_KEY}` not readable as a list of "
                          f"names, globs, 'dir/' entries or {{\"name\", \"until\"}} objects — {exc!r}")
        return None
    return names


def check_top_level(path, allow, violations):
    """A new name directly under the threads root must be on the allowlist.

    Runs on status A and on the NEW path of a rename or copy, so a stray cannot arrive by
    `git mv` either. A directory entry admits only a directory; a file entry only a file.
    """
    if not path.startswith(THREADS_ROOT + "/"):
        return
    first, sep, _ = path[len(THREADS_ROOT) + 1:].partition("/")
    is_dir = bool(sep)
    for entry in allow:
        if entry.endswith("/"):
            if is_dir and fnmatch.fnmatchcase(first, entry[:-1]):
                return
        elif not is_dir and fnmatch.fnmatchcase(first, entry):
            return
    shown = first + ("/" if is_dir else "")
    violations.append(
        f"{path}: the `{THREADS_ROOT}/` top level admits only the names in {SIZE_BOUNDS_FILE} "
        f"`{TOP_LEVEL_KEY}` — `{shown}` is not one of them. A session narrative goes in "
        f"`narratives/`, a status review in `reviews/`, a dated one-off (playbook, brainstorm, "
        f"proposal, review prompt) in the thread it serves; a new subsystem directory goes on "
        f"the list first, in the same commit."
    )


def split_section(text, heading_prefix):
    """(text without the '## ' section that starts with heading_prefix, that section)."""
    m = re.search(r"^" + re.escape(heading_prefix) + r".*$", text or "", re.M)
    if not m:
        return text or "", ""
    nxt = NEXT_H2.search(text, m.end())
    end = nxt.start() if nxt else len(text)
    return text[:m.start()] + text[end:], text[m.start():end]


def check_size_bound(src, path, kind, entry, violations):
    """A listed boot file may not grow past max_bytes, and while over it must shrink.

    `excluding_section` leaves one '## ' section out of the measure — the tracking cache's
    "Decisions in force", which grows by one line per ruling for as long as rulings are
    made (operator, 2026-09-21: a byte cap there would refuse the 14th new decision). That
    section is held to its FORM instead: a line added to it is one numbered decision line
    of at most `entry_line_max_bytes`.

    `only_sections` is the opposite: the measure is ONLY the listed '## ' sections, summed
    — a lane charter of which the resume protocol reads four sections "not the whole
    file"; the rest of the plan may grow (operator, 2026-09-21).
    """
    new = src.new_blob(path) or ""
    old = src.old_blob(path) if kind == "M" else None
    sec = entry.get("excluding_section")
    new_rest, new_sec = split_section(new, sec) if sec else (new, "")
    old_rest, old_sec = split_section(old, sec) if (sec and old is not None) else (old, "")
    what = f"{path}" + (f" (without '{sec}')" if sec else "")
    only = entry.get("only_sections")
    if only:
        def listed(text):
            return "".join(split_section(text, h)[1] for h in only)
        new_rest, old_rest = listed(new), (listed(old) if old is not None else None)
        what = f"{path} (its {len(only)} boot-read sections: {', '.join(repr(h) for h in only)})"
    bound, n = int(entry["max_bytes"]), nbytes(new_rest)
    if n > bound:
        o = nbytes(old_rest) if old_rest is not None else None
        if o is None or o <= bound:
            violations.append(
                f"{what}: a bounded boot surface — {n} B is over its bound of {bound} B. Move "
                f"facts to findings and a skill chapter, status to a Current truth; keep pointers.")
        elif n >= o:
            violations.append(
                f"{what}: a bounded boot surface — it is over its bound of {bound} B ({o} B) "
                f"and this commit does not shrink it ({n} B). While over, every commit to it must.")
    line_max = entry.get("entry_line_max_bytes")
    if sec and line_max:
        had = set(old_sec.splitlines())
        bad = [ln for ln in new_sec.splitlines()[1:]
               if ln.strip() and ln not in had
               and not (ENTRY_LINE_RE.match(ln) and nbytes(ln) <= int(line_max))]
        if bad:
            violations.append(
                f"{path}: '{sec}' holds ONE LINE per entry — {len(bad)} added line(s) are not a "
                f"numbered '<NNN>. **ruling sentence** (who, date)' line of at most {line_max} B: "
                f"{'; '.join(repr(_clip(b)) for b in bad[:3])}. The full entry goes in the "
                f"decisions registry.")


def check(src, merging=False):
    """Every violation in one change. `merging` = a conflicted merge is being concluded."""
    violations = []
    bounds = {} if merging else size_bounds(src, violations)
    allow = None if merging else top_level_allow(src, violations)
    for status, path in parse_name_status(src.name_status()):
        if path in bounds and status[:1] in ("A", "M"):   # listed by the project; any tree
            check_size_bound(src, path, status[:1], bounds[path], violations)
        if not in_threads_tree(path):
            continue
        kind = status[:1]
        # TOP-LEVEL runs BEFORE the rename skip: a rename arrives as 'R100' with its new
        # path, and that path is exactly what the allowlist is about.
        if allow is not None and kind in ("A", "R", "C"):
            check_top_level(path, allow, violations)
        if kind not in ("A", "M"):                 # rename/copy/delete — out of scope below
            continue
        base = os.path.basename(path)

        # Overwrite-and-bounded class, caches, narratives. Dispatched BEFORE the
        # new-file skip below, because two of these checks exist for status A.
        # They stand down while a conflicted merge is concluded.
        if not merging:
            if base == "handoff.md":
                check_current_truth(src, path, kind, violations)
            elif NARRATIVE_RE.match(base):
                if kind == "A":
                    check_narrative_new(src, path, violations)
                else:
                    check_narrative_modified(src, path, violations)
            elif CACHE_RE.match(base):
                check_cache_prepend(src, path, violations)

        # Immutable + append-only classes: new files are always allowed, as before.
        if kind != "M":
            continue
        if FINDINGS_RE.search(base) or base.startswith("findings-"):
            check_findings(src, path, violations)
        elif base == "handoff.md":
            check_session_log(src, path, violations)
    return violations


def merge_in_progress():
    return _git(["rev-parse", "-q", "--verify", "MERGE_HEAD"])[0] == 0


def dry_run(revs):
    shas = _out(["rev-list", "--reverse", revs]).split()
    refused = merges = 0
    for sha in shas:
        parents = _out(["rev-list", "--parents", "-n", "1", sha]).split()[1:]
        subject = _out(["log", "-1", "--format=%s", sha]).strip()
        if len(parents) != 1:
            merges += 1
            print(f"{sha[:8]}  (merge or root commit — not simulated)  {subject}")
            continue
        violations = check(Commit(sha))
        if violations:
            refused += 1
            print(f"{sha[:8]}  WOULD BE REFUSED  {subject}")
            for v in violations:
                print("    • " + v)
    print(f"\n{refused} of {len(shas) - merges} non-merge commit(s) in {revs} would have been "
          f"refused; {merges} merge/root commit(s) not simulated.")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--range", metavar="REVS",
                    help="dry run: report what each non-merge commit in REVS "
                         "(e.g. HEAD~30..HEAD) would have had refused; always exits 0")
    args = ap.parse_args(argv)
    if args.range:
        return dry_run(args.range)

    violations = check(Staged(), merging=merge_in_progress())
    if violations:
        sys.stderr.write("\nthreads record-discipline guard — commit BLOCKED:\n\n")
        for v in violations:
            sys.stderr.write("  • " + v + "\n")
        sys.stderr.write(
            "\nSee CONVENTIONS § 'Record discipline — three edit-classes' and its "
            "Enforcement paragraph.\n"
            "Override (committer's explicit choice): git commit --no-verify\n\n"
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
