#!/usr/bin/env python3
"""Inventory every thread under a project root.

Output: a JSON document on stdout with one record per thread,
including the thread's status, title, plan-hop file paths, and the
files those plan hops touch (extracted by best-effort regex from
"Files touched:" lines in plan-*.md).

Used by /code-survey Scan to produce thread-tree-snapshot.json,
which the Synthesize step consumes for the three-bucket dedupe
(NEW / SUBSUMED / TENSION).

Includes both active and closed threads -- closed-thread overlap
matters because a recommendation that already landed is signal,
not noise.

Usage:
    inventory_threads.py <project-root> [--pretty]
"""
import argparse
import json
import re
import sys
from pathlib import Path


PLAN_FILES_TOUCHED = re.compile(
    r"^\s*-?\s*\*\*Files touched:?\*\*\s*(.+?)$",
    re.IGNORECASE | re.MULTILINE,
)


def extract_files_touched(plan_md_path: Path) -> list[str]:
    """Best-effort extraction of file paths from a plan-*.md."""
    try:
        text = plan_md_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    paths: list[str] = []
    for match in PLAN_FILES_TOUCHED.finditer(text):
        line = match.group(1)
        for token in re.split(r"[,\s]+", line):
            token = token.strip("`.,;:()[]")
            if token and ("/" in token or token.endswith(".py")):
                paths.append(token)
    return sorted(set(paths))


def inventory(project_root: Path) -> list[dict]:
    threads: list[dict] = []
    for thread_json in project_root.glob("**/threads/*/*/thread.json"):
        try:
            data = json.loads(thread_json.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue

        thread_dir = thread_json.parent
        plan_files = sorted(thread_dir.glob("plan-*.md"))
        plan_data = []
        for plan in plan_files:
            plan_data.append(
                {
                    "file": str(plan.relative_to(project_root)),
                    "files_touched": extract_files_touched(plan),
                }
            )

        threads.append(
            {
                "id": data.get("id", thread_dir.name),
                "title": data.get("title", ""),
                "status": data.get("status", "unknown"),
                "started": data.get("started"),
                "updated": data.get("updated"),
                "thread_dir": str(thread_dir.relative_to(project_root)),
                "plans": plan_data,
                # Aggregate of every file referenced across all plans, for
                # quick file-overlap matching during the dedupe pass.
                "all_files_touched": sorted(
                    {
                        f
                        for plan in plan_data
                        for f in plan["files_touched"]
                    }
                ),
            }
        )

    threads.sort(key=lambda t: (t["status"] != "active", t["id"]))
    return threads


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project_root", type=Path)
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args(argv)

    if not args.project_root.is_dir():
        print(f"error: {args.project_root} is not a directory", file=sys.stderr)
        return 2

    # Emit project_root as "." rather than the resolved absolute path:
    # the snapshot lives inside the project tree, so the absolute path is
    # both redundant and a fingerprint leak (it includes the username on
    # most systems). All `thread_dir` entries below are already relative
    # to the project root, so consumers shouldn't need the absolute form.
    snapshot = {
        "project_root": ".",
        "threads": inventory(args.project_root),
    }
    indent = 2 if args.pretty else None
    json.dump(snapshot, sys.stdout, indent=indent)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
