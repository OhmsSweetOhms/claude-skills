#!/usr/bin/env python3
"""Mailbox scan hook — the mechanical fallback for the ambiguity mailbox.

Runs as a UserPromptSubmit / SessionStart hook (zero tokens when quiet).
Finds every `codex-handoff/<packet>/questions/q-*.md` whose frontmatter
`status:` is `open` or `escalated` across the inbox roots reachable from
the current working directory, and injects one line per hit into the
turn as additionalContext:

    OPEN_QUESTION <path>        (status: open — answer it)
    ESCALATED_QUESTION <path>   (status: escalated — user decision in flight)

Inbox roots, in order (deduplicated):
  1. the git top-level of cwd (a worker sitting in its own worktree);
  2. every `codex_worktrees[].path` of every `.threads/**/thread.json`
     under that top-level (the orchestrator sitting in the main checkout;
     `$WORKBASE/` and `~` placeholders resolved the same way
     emit_codex_launch_packet.py resolves them);
  3. every worktree of the cwd repo (`git worktree list`).

A packet that already has a handback (`handback.json`/`.md`) is closed:
its questions are record, not work, and are skipped. The same tracked
question file appearing in several worktrees is reported once.

Prints nothing and exits 0 when there is nothing to report. Never fails
the turn: any error is swallowed to stderr. Contract:
references/codex-handoff.md §"Ambiguity mailbox".
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

STATUS_RE = re.compile(r"^status:\s*(\S+)", re.M)
MAX_ROOTS = 64


def git_toplevel(cwd: Path) -> Path | None:
    try:
        out = subprocess.run(
            ["git", "-C", str(cwd), "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=5,
        )
    except Exception:
        return None
    if out.returncode != 0:
        return None
    return Path(out.stdout.strip())


def git_worktrees(repo: Path) -> list[Path]:
    try:
        out = subprocess.run(
            ["git", "-C", str(repo), "worktree", "list", "--porcelain"],
            capture_output=True, text=True, timeout=5,
        )
    except Exception:
        return []
    return [Path(l[len("worktree "):]) for l in out.stdout.splitlines()
            if l.startswith("worktree ")]


def resolve_wt_path(raw: str, main_repo: Path) -> Path | None:
    if raw.startswith("$WORKBASE/"):
        tail = raw[len("$WORKBASE/"):]
        cands = []
        wb = os.environ.get("WORKBASE")
        if wb:
            cands.append(Path(wb) / tail)
        cands += [main_repo.parent / tail, main_repo.parent.parent / tail]
        for c in cands:
            if c.exists():
                return c.resolve()
        return None
    p = Path(os.path.expandvars(raw)).expanduser()
    if not p.is_absolute():
        p = (main_repo / raw).resolve()
    return p if p.exists() else None


def thread_worktrees(top: Path) -> list[Path]:
    roots: list[Path] = []
    tdir = top / ".threads"
    if not tdir.is_dir():
        return roots
    for tj in tdir.rglob("thread.json"):
        try:
            data = json.loads(tj.read_text())
        except Exception:
            continue
        for e in data.get("codex_worktrees", []) or []:
            raw = e.get("path") if isinstance(e, dict) else None
            if not raw:
                continue
            p = resolve_wt_path(raw, top)
            if p:
                roots.append(p)
    return roots


def scan_root(root: Path) -> list[tuple[str, Path]]:
    hits: list[tuple[str, Path]] = []
    ch = root / "codex-handoff"
    if not ch.is_dir():
        return hits
    for q in ch.glob("*/questions/q-*.md"):
        # A packet with a handback is CLOSED — its questions are record,
        # not work (timeouts and never-flipped files are normal there).
        pkt = q.parent.parent
        if (pkt / "handback.json").exists() or (pkt / "handback.md").exists():
            continue
        try:
            head = q.read_text(errors="replace")[:2048]
        except Exception:
            continue
        m = STATUS_RE.search(head)
        if not m:
            continue
        st = m.group(1).strip().lower()
        if st == "open":
            hits.append(("OPEN_QUESTION", q))
        elif st == "escalated":
            hits.append(("ESCALATED_QUESTION", q))
    return hits


def main() -> int:
    try:
        payload = json.load(sys.stdin) if not sys.stdin.isatty() else {}
    except Exception:
        payload = {}
    cwd = Path(payload.get("cwd") or os.getcwd())
    top = git_toplevel(cwd)
    roots: list[Path] = []
    if top:
        roots.append(top)
        roots += thread_worktrees(top)
        roots += git_worktrees(top)
    seen: set[Path] = set()
    seen_q: set[tuple[str, str, int]] = set()  # (packet, file, size) — the same
    hits: list[tuple[str, Path]] = []          # tracked file shows up in every worktree
    for r in roots[:MAX_ROOTS]:
        rr = r.resolve()
        if rr in seen:
            continue
        seen.add(rr)
        for verb, q in scan_root(rr):
            key = (q.parent.parent.name, q.name, q.stat().st_size)
            if key in seen_q:
                continue
            seen_q.add(key)
            hits.append((verb, q))
    if not hits:
        return 0
    lines = []
    for verb, q in hits:
        try:
            shown = str(q.relative_to(top)) if top and q.is_relative_to(top) else str(q)
        except Exception:
            shown = str(q)
        lines.append(f"{verb} {shown}")
    body = (
        "Mailbox scan (threads skill, ambiguity mailbox fallback): "
        f"{len(hits)} unresolved question(s) found. Resolve per "
        "references/codex-handoff.md §Ambiguity mailbox (Resolution body FIRST, "
        "status flip LAST, then ring the asker if it is a Claude session):\n"
        + "\n".join(lines)
    )
    event = payload.get("hook_event_name") or "UserPromptSubmit"
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": event,
            "additionalContext": body,
        }
    }))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # never fail the turn
        print(f"scan_open_questions: {exc}", file=sys.stderr)
        sys.exit(0)
