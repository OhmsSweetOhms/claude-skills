#!/usr/bin/env python3
"""Refuse a skills cutover while any Codex worker is alive.

A cutover replaces the scripts a running worker and its launcher execute in
place, and changes the worker-state schema under them. Run this first; it exits
0 only when nobody is home.

  cutover_preflight.py --repo <checkout> [--repo ...] [--worktree <dir> ...]

`--repo` names a git checkout; every worktree `git worktree list` reports for it
is searched for `codex-handoff/*/worker-state.json`. `--worktree` names one
directory to search directly.

Liveness is the PROCESS, never the lifecycle. A record's `worker_identity` and
`launcher_identity` (pid + boot_id + start_ticks) are compared with /proc. The
lifecycle field and `process.state` are printed and never consulted: a
`completed` worker's TUI is still there and still rung (the hop-12 trial,
2026-09-18), and nothing reaps `process.state`, so it reads `running` for a dead
pid forever — one tracked record does exactly that in every socks worktree.

A second, independent walk reads /proc for a running
`launch_codex_worker.py launch`: the launcher waits on its child, so it is alive
for as long as the worker is, whatever became of the record.

Output is one line per finding, then exactly one of
`CUTOVER_CLEAR records=<n> gone=<n> no_identity=<n>` (exit 0) or
`CUTOVER_REFUSED live=<n> unreadable=<n>` (exit 1). Departed workers are counted,
not listed, unless `--verbose`.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from launch_codex_worker import identity_is_live  # noqa: E402

LAUNCHER = "launch_codex_worker.py"
IDENTITIES = ("worker_identity", "launcher_identity")


def worktrees_of(repo: Path) -> list[Path]:
    out = subprocess.run(["git", "-C", str(repo), "worktree", "list", "--porcelain"],
                         capture_output=True, text=True)
    if out.returncode != 0:
        raise LookupError(f"{repo} is not a git checkout: {out.stderr.strip()}")
    return [Path(line[len("worktree "):]) for line in out.stdout.splitlines()
            if line.startswith("worktree ")]


def records_in(worktree: Path) -> list[Path]:
    return sorted(worktree.glob("codex-handoff/*/worker-state.json"))


def judge(record: Path) -> tuple[str, str]:
    """(`LIVE` | `GONE` | `NO_IDENTITY` | `UNREADABLE`, detail) for one record."""
    try:
        state = json.loads(record.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return "UNREADABLE", str(exc)
    process = state.get("process") if isinstance(state, dict) else None
    if not isinstance(process, dict):
        return "NO_IDENTITY", "no process block"
    # Printed for the reader, never consulted.
    said = f"lifecycle={state.get('state')} process.state={process.get('state')}"
    recorded = [process.get(key) for key in IDENTITIES if process.get(key)]
    if not recorded:
        return "NO_IDENTITY", said
    for identity in recorded:
        if identity_is_live(identity):
            return "LIVE", f"pid={identity['pid']} {said}"
    return "GONE", said


def live_launchers(proc: Path = Path("/proc")) -> list[tuple[int, str]]:
    """Every running `launch_codex_worker.py launch`, from /proc alone."""
    found = []
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            argv = (entry / "cmdline").read_bytes().split(b"\0")
        except OSError:
            continue        # it exited under us
        words = [word.decode("utf-8", "replace") for word in argv if word]
        for i, word in enumerate(words[:-1]):
            if Path(word).name == LAUNCHER and words[i + 1] == "launch":
                found.append((int(entry.name), " ".join(words[i:])))
                break
    return sorted(found)


def main(argv: list[str] | None = None, proc: Path = Path("/proc")) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo", action="append", default=[], type=Path)
    parser.add_argument("--worktree", action="append", default=[], type=Path)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)
    if not args.repo and not args.worktree:
        parser.error("name at least one --repo or --worktree; "
                     "a preflight that searched nowhere proves nothing")

    for worktree in args.worktree:
        if not worktree.is_dir():
            parser.error(f"{worktree} is not a directory")
    worktrees = list(args.worktree)
    for repo in args.repo:
        try:
            worktrees.extend(worktrees_of(repo))
        except LookupError as exc:
            parser.error(str(exc))

    counts = {"LIVE": 0, "GONE": 0, "NO_IDENTITY": 0, "UNREADABLE": 0}
    seen = set()
    for worktree in worktrees:
        for record in records_in(worktree):
            if record.resolve() in seen:
                continue
            seen.add(record.resolve())
            verdict, detail = judge(record)
            counts[verdict] += 1
            if verdict != "GONE" or args.verbose:
                print(f"{verdict} {record} {detail}")

    launchers = live_launchers(proc)
    for pid, command in launchers:
        print(f"LIVE_LAUNCHER {pid} {command}")

    print(f"SEARCHED worktrees={len(worktrees)} records={len(seen)}")
    live = max(counts["LIVE"], len(launchers))
    if live or counts["UNREADABLE"]:
        print(f"CUTOVER_REFUSED live={live} unreadable={counts['UNREADABLE']}")
        return 1
    print(f"CUTOVER_CLEAR records={len(seen)} gone={counts['GONE']} "
          f"no_identity={counts['NO_IDENTITY']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
