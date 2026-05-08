#!/usr/bin/env python3
"""Emit a minimal Codex launch packet for a plan hop (Path B).

When the plan file is self-contained (every step authored, deliverables
enumerated, hard constraints listed, focused tests + regression baseline
written), the plan file itself IS the launch prompt. Codex just needs an
absolute path to it plus a handful of run-specific operational facts.

This script emits those six mechanical facts plus two generic operational
rules in a human-readable format ready to paste into Codex's turn 1.

Mechanical facts emitted:

    - Plan file absolute path   (resolved from .threads/<thread-id>/)
    - Worktree absolute path    (read from thread.json::codex_worktrees[])
    - Branch                    (read from worktree git or thread.json)
    - Base SHA (short)          (git -C <worktree> rev-parse --short HEAD)
    - Handback inbox path       (<worktree>/codex-handoff/<plan-id>/)
    - Thread + plan IDs

Generic operational rules emitted:

    - Don't push the branch (long-lived; merge-back at thread close).
    - Write structured handback per references/codex-handback.md.

Plan-specific operational rules (cross-repo edits, regression-baseline
specifics, no-simulation constraints, etc.) live in the plan file's
"Hard constraints" section. Path B does NOT extract those; the user
reads them off the plan file and states them inline at Codex turn 1,
or trusts Codex to read them when it opens the plan file.

Usage:
    python3 emit_codex_launch_packet.py \\
        --main-repo . \\
        --thread-id receiver/20260427-chi-square-raim-design \\
        --plan-id plan-03b

Compare with `render_codex_handoff.py` (Path A): that script produces a
hand-curate-required scaffold file. This script produces a copy-paste
launch packet inline.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path


def warn(msg: str) -> None:
    print(f"warning: {msg}", file=sys.stderr)


def die(msg: str, code: int = 1) -> None:
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(code)


def git_short_sha(repo: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return None
    return result.stdout.strip() or None


def git_branch(repo: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), "branch", "--show-current"],
            capture_output=True,
            text=True,
            check=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return None
    branch = result.stdout.strip()
    return branch or None


def find_plan_file(thread_dir: Path, plan_id: str) -> Path:
    candidates = sorted(thread_dir.glob(f"{plan_id}-*.md"))
    if not candidates:
        candidates = sorted(thread_dir.glob(f"{plan_id}.md"))
    if not candidates:
        existing = sorted(p.name for p in thread_dir.glob("plan-*.md"))
        die(
            f"no plan file matching '{plan_id}-*.md' in {thread_dir}.\n"
            f"  existing plan files: {existing or 'none'}"
        )
    if len(candidates) > 1:
        warn(
            f"multiple plan files matched {plan_id}; using "
            f"{candidates[0].name}"
        )
    return candidates[0]


def discover_worktree(thread_json: Path, main_repo: Path) -> tuple[Path, str | None]:
    """Return (worktree_path, branch_from_json) from thread.json.

    Picks the first non-merged codex_worktrees[] entry whose path
    resolves to an existing directory. Returns (None, None) if no
    suitable entry is found — caller dies with a helpful message.
    """
    if not thread_json.exists():
        die(f"thread.json not found: {thread_json}")
    try:
        data = json.loads(thread_json.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        die(f"failed to parse {thread_json}: {exc}")

    worktrees = data.get("codex_worktrees", [])
    ranked = sorted(
        worktrees,
        key=lambda w: 0 if w.get("status") not in ("merged",) else 1,
    )
    for entry in ranked:
        raw_path = entry.get("path")
        if not raw_path:
            continue
        # thread.json paths can use the <workspace-root>/... scrub
        # convention. The token expands to the directory containing the
        # project workspace, which is typically one or two parents above
        # the main checkout. Try several plausible roots and pick the
        # first that resolves to an existing directory.
        if raw_path.startswith("<workspace-root>/"):
            tail = raw_path[len("<workspace-root>/"):]
            candidates = [
                main_repo.parent / tail,
                main_repo.parent.parent / tail,
                main_repo.parent.parent.parent / tail,
            ]
            for candidate in candidates:
                resolved = candidate.resolve()
                if resolved.exists():
                    return resolved, entry.get("branch")
            # Fall through to the next entry if no candidate exists
            continue
        path = Path(raw_path)
        if not path.is_absolute():
            path = (main_repo / raw_path).resolve()
        if path.exists():
            return path, entry.get("branch")

    die(
        f"no usable codex_worktrees[] entry in {thread_json}.\n"
        f"  expected at least one entry with a 'path' that resolves "
        f"to an existing directory."
    )


def emit_packet(
    *,
    plan_file: Path,
    worktree: Path,
    branch: str,
    base_sha: str,
    handback_inbox: Path,
    thread_id: str,
    plan_id: str,
) -> str:
    return f"""\
# Codex launch packet — {thread_id} / {plan_id}

## Six mechanical facts

**Plan file** (paste this path into Codex turn 1; tell it
"execute this plan from start to finish"):

```
{plan_file}
```

**Worktree** (where Codex does the source-code work):

```
{worktree}
```

**Branch:** `{branch}`
**Base SHA:** `{base_sha}` (current worktree HEAD)

**Handback inbox** (where Codex writes its session output —
handback.json + handback.md + scripts/ + temp/ + artifacts/ per
`~/.claude/skills/threads/references/codex-handback.md`):

```
{handback_inbox}/
```

**Thread / Plan IDs:** `{thread_id}` / `{plan_id}`

## Two generic operational rules to state at Codex turn 1

1. **Don't push the branch.** `{branch}` is long-lived across the
   thread's plan hops; merge-back to `main` is a single terminal
   event at thread close on user request, not at plan close.

2. **Write structured handback** to:

   ```
   {handback_inbox}/handback.{{json,md}}
   ```

   with the required gates / discoveries / follow_ons / blockers /
   investigations / handoff_artifacts shape. See
   `~/.claude/skills/threads/references/codex-handback.md` for the
   schema and recording discipline.

## Plan-specific operational rules

Read the plan file's "Hard constraints" section. The plan file
already contains run-specific rules (cross-repo edits, regression
baselines, no-simulation constraints, etc.) — Codex consumes those
when it opens the plan as turn 1.

## Sidecar terminal

```bash
cd {worktree}
source .envrc
codex
```

Then paste the plan file path + the two operational rules above as
turn 1.
"""


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Emit a minimal Codex launch packet (Path B) for a plan hop. "
            "Use when the plan file is self-contained and no scaffold "
            "wrapper is needed."
        ),
    )
    parser.add_argument(
        "--thread-id",
        required=True,
        help="Thread ID, e.g. receiver/20260427-chi-square-raim-design",
    )
    parser.add_argument(
        "--plan-id",
        required=True,
        help="Plan hop ID, e.g. plan-03b",
    )
    parser.add_argument(
        "--main-repo",
        default=".",
        help="Path to main checkout (where .threads/ lives). Default: cwd",
    )
    parser.add_argument(
        "--worktree-path",
        default=None,
        help=(
            "Override worktree path. Default: discovered from "
            "thread.json::codex_worktrees[]."
        ),
    )
    parser.add_argument(
        "--out",
        default=None,
        help="Output path for the launch packet. Default: stdout",
    )
    args = parser.parse_args()

    main_repo = Path(args.main_repo).resolve()
    if not main_repo.exists():
        die(f"main repo {main_repo} does not exist")

    thread_dir = main_repo / ".threads" / args.thread_id
    if not thread_dir.exists():
        die(f"thread directory not found: {thread_dir}")

    thread_json = thread_dir / "thread.json"
    plan_file = find_plan_file(thread_dir, args.plan_id)

    worktree: Path
    branch_json: str | None = None
    if args.worktree_path:
        worktree = Path(args.worktree_path).resolve()
        if not worktree.exists():
            die(f"worktree {worktree} does not exist")
    else:
        worktree, branch_json = discover_worktree(thread_json, main_repo)

    branch = git_branch(worktree) or branch_json
    if not branch:
        die(
            f"could not determine branch for worktree {worktree}.\n"
            f"  git branch --show-current returned empty (detached HEAD?) "
            f"and thread.json had no codex_worktrees[].branch field."
        )

    base_sha = git_short_sha(worktree)
    if not base_sha:
        die(
            f"could not read short HEAD SHA from worktree {worktree}.\n"
            f"  is git installed and the worktree initialized?"
        )

    handback_inbox = worktree / "codex-handoff" / args.plan_id

    packet = emit_packet(
        plan_file=plan_file,
        worktree=worktree,
        branch=branch,
        base_sha=base_sha,
        handback_inbox=handback_inbox,
        thread_id=args.thread_id,
        plan_id=args.plan_id,
    )

    if args.out:
        out_path = Path(args.out).resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(packet)
        print(f"Codex launch packet written to:")
        print(f"  {out_path}")
    else:
        sys.stdout.write(packet)


if __name__ == "__main__":
    main()
