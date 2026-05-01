#!/usr/bin/env python3
"""
fingerprint_scan.py -- CLI audit tool for scanning repos and directory trees
for PII, secrets, and digital fingerprint material.

Modes:
  --scan-dir DIR       : scan all tracked files in a single repo (working tree)
  --scan-tree DIR      : find all git repos under DIR, scan each, output JSON
  --scan-commits DIR   : scan every reachable commit message (git log --all)
  --scan-unpushed DIR  : scan commit messages not yet on the upstream branch

Uses the shared fingerprint_engine for all pattern matching.

Engine: ~/.claude/hooks/fingerprint_engine.py
"""

import json
import os
import subprocess
import sys
from pathlib import Path

# Import shared engine
sys.path.insert(0, str(Path.home() / ".claude" / "hooks"))
from fingerprint_engine import (
    Scanner,
    filter_gitignored,
    find_git_repos,
    find_loose_files,
    git_ls_files,
    git_repo_status,
    is_binary,
    is_git_repo,
    scan_single_repo,
)


# ---------------------------------------------------------------------------
# Mode: --scan-dir (single repo)
# ---------------------------------------------------------------------------
def mode_scan_dir(scan_dir: str) -> int:
    scan_dir = os.path.abspath(scan_dir)
    if not os.path.isdir(scan_dir):
        print(f"ERROR: Directory not found: {scan_dir}", file=sys.stderr)
        return 1

    print(f"Fingerprint scan: {scan_dir}", file=sys.stderr)
    print("=" * 48, file=sys.stderr)

    scanner = Scanner("scan", scan_dir)

    if is_git_repo(scan_dir):
        files = git_ls_files(scan_dir)
    else:
        files = []
        workspace_dir = os.path.join(scan_dir, "workspace")
        for root, dirs, fnames in os.walk(scan_dir):
            if ".git" in root.split(os.sep):
                continue
            # Skip ephemeral workspace/ at the top level of the scan root
            if root == workspace_dir or root.startswith(workspace_dir + os.sep):
                dirs[:] = []
                continue
            for fname in fnames:
                rel = os.path.relpath(os.path.join(root, fname), scan_dir)
                files.append(rel)

    files = filter_gitignored(files, scan_dir)

    file_count = 0
    for f in files:
        full = os.path.join(scan_dir, f)
        if not os.path.isfile(full) or is_binary(full):
            continue
        file_count += 1
        scanner.scan_file(f, Path(full).read_text(errors="replace"))

    print(f"Scanned {file_count} files", file=sys.stderr)
    print("=" * 48, file=sys.stderr)

    if scanner.found_count:
        scanner.report()
        return 2
    else:
        print("CLEAN: No PII, secrets, or fingerprint material found",
              file=sys.stderr)
        return 0


# ---------------------------------------------------------------------------
# Mode: --scan-tree (multi-repo discovery + scan)
# ---------------------------------------------------------------------------
def mode_scan_tree(root_dir: str) -> int:
    root_dir = os.path.abspath(root_dir)
    repos = find_git_repos(root_dir)
    loose = find_loose_files(root_dir, repos)

    results = {
        "root": root_dir,
        "repos": {},
        "loose_files": loose,
    }

    for repo in repos:
        rel = os.path.relpath(repo, root_dir)
        file_count = len(git_ls_files(repo))
        status = git_repo_status(repo)
        findings = scan_single_repo(repo)
        results["repos"][rel] = {
            "path": repo,
            "tracked_files": file_count,
            "git_status": status,
            "findings": findings,
            "scanned": True,
        }

    json.dump(results, sys.stdout, indent=2)
    return 2 if any(r["findings"] for r in results["repos"].values()) else 0


# ---------------------------------------------------------------------------
# Mode: --scan-commits / --scan-unpushed (commit message audit)
# ---------------------------------------------------------------------------
def _iter_commits(repo_dir: str, rev_range: str = "--all"):
    """Yield (sha, subject, body) tuples for the given rev range.

    Uses NUL-byte field separator and ASCII RS (\\x1e) record separator so
    multi-line commit bodies survive intact.
    """
    fmt = "%H%x00%s%x00%B%x1e"
    args = ["git", "log", rev_range, f"--format={fmt}"]
    try:
        result = subprocess.run(
            args, capture_output=True, text=True, cwd=repo_dir, timeout=60
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        print(f"ERROR: git log failed: {e}", file=sys.stderr)
        return

    if result.returncode != 0:
        print(f"ERROR: git log exit {result.returncode}: "
              f"{result.stderr.strip()}", file=sys.stderr)
        return

    for record in result.stdout.split("\x1e"):
        record = record.lstrip("\n")
        if not record:
            continue
        parts = record.split("\x00")
        if len(parts) < 3:
            continue
        sha, subject, body = parts[0], parts[1], parts[2]
        yield sha, subject, body


def mode_scan_commits(repo_dir: str, rev_range: str = "--all",
                      label_prefix: str = "all") -> int:
    """Scan commit messages in the given rev range.

    rev_range: argument passed to git log. "--all" walks every reachable ref;
    "@{upstream}..HEAD" walks only commits not yet on upstream.
    """
    repo_dir = os.path.abspath(repo_dir)
    if not is_git_repo(repo_dir):
        print(f"ERROR: Not a git repo: {repo_dir}", file=sys.stderr)
        return 1

    print(f"Fingerprint commit-message scan ({label_prefix}): {repo_dir}",
          file=sys.stderr)
    print("=" * 56, file=sys.stderr)

    scanner = Scanner("scan-commits", repo_dir)
    commit_count = 0

    for sha, subject, body in _iter_commits(repo_dir, rev_range):
        commit_count += 1
        # Use short SHA + subject as the label for human-readable findings
        label = f"commit:{sha[:7]} {subject[:60]}"
        for lineno, line in enumerate(body.splitlines(), 1):
            scanner.scan_line(label, lineno, line)

    print(f"Scanned {commit_count} commit message(s)", file=sys.stderr)
    print("=" * 56, file=sys.stderr)

    if scanner.found_count:
        scanner.report()
        return 2
    print("CLEAN: No PII, secrets, or fingerprint material found in messages",
          file=sys.stderr)
    return 0


def mode_scan_unpushed(repo_dir: str) -> int:
    """Scan commit messages not yet pushed to upstream.

    Falls back gracefully if no upstream is configured.
    """
    repo_dir = os.path.abspath(repo_dir)
    if not is_git_repo(repo_dir):
        print(f"ERROR: Not a git repo: {repo_dir}", file=sys.stderr)
        return 1

    upstream_r = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "@{upstream}"],
        capture_output=True, text=True, cwd=repo_dir, timeout=10
    )
    if upstream_r.returncode != 0:
        print("ERROR: No upstream configured for current branch. "
              "Use --scan-commits to scan all reachable commits, "
              "or set an upstream with `git branch --set-upstream-to`.",
              file=sys.stderr)
        return 1

    upstream = upstream_r.stdout.strip()
    return mode_scan_commits(repo_dir, f"{upstream}..HEAD",
                             label_prefix=f"unpushed vs {upstream}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
USAGE = (
    "Usage: fingerprint_scan.py MODE [DIR]\n"
    "  --scan-dir DIR       scan working-tree files\n"
    "  --scan-tree DIR      multi-repo discovery + scan (JSON output)\n"
    "  --scan-commits DIR   scan all reachable commit messages\n"
    "  --scan-unpushed DIR  scan unpushed commit messages (vs @{upstream})\n"
)


def main():
    if len(sys.argv) < 2:
        print(USAGE, file=sys.stderr)
        sys.exit(1)

    cmd = sys.argv[1]
    target = sys.argv[2] if len(sys.argv) > 2 else "."

    if cmd == "--scan-dir":
        sys.exit(mode_scan_dir(target))
    elif cmd == "--scan-tree":
        sys.exit(mode_scan_tree(target))
    elif cmd == "--scan-commits":
        sys.exit(mode_scan_commits(target))
    elif cmd == "--scan-unpushed":
        sys.exit(mode_scan_unpushed(target))
    else:
        print(f"Unknown mode: {cmd}\n\n{USAGE}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
