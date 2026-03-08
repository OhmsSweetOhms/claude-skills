#!/usr/bin/env python3
"""
fingerprint_scan.py -- CLI audit tool for scanning repos and directory trees
for PII, secrets, and digital fingerprint material.

Modes:
  --scan-dir DIR  : scan all tracked files in a single repo
  --scan-tree DIR : find all git repos under DIR, scan each, output JSON

Uses the shared fingerprint_engine for all pattern matching.

Engine: ~/.claude/hooks/fingerprint_engine.py
"""

import json
import os
import sys
from pathlib import Path

# Import shared engine
sys.path.insert(0, str(Path.home() / ".claude" / "hooks"))
from fingerprint_engine import (
    Scanner,
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
        for root, _dirs, fnames in os.walk(scan_dir):
            if ".git" in root.split(os.sep):
                continue
            for fname in fnames:
                rel = os.path.relpath(os.path.join(root, fname), scan_dir)
                files.append(rel)

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
# Main
# ---------------------------------------------------------------------------
def main():
    if len(sys.argv) < 2:
        print("Usage: fingerprint_scan.py --scan-dir DIR | --scan-tree DIR",
              file=sys.stderr)
        sys.exit(1)

    cmd = sys.argv[1]
    target = sys.argv[2] if len(sys.argv) > 2 else "."

    if cmd == "--scan-dir":
        sys.exit(mode_scan_dir(target))
    elif cmd == "--scan-tree":
        sys.exit(mode_scan_tree(target))
    else:
        print(f"Unknown mode: {cmd}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
