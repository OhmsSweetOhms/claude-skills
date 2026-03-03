#!/usr/bin/env python3
"""
Clean build artifacts from a SOCKS project.

Removes Vivado/Xsim build outputs, logs, journals, simulation waveforms,
synthesis reports, and other generated files while preserving source code,
testbenches, TCL scripts, and committed project files.

Usage:
    python scripts/clean.py --project-dir .
    python scripts/clean.py --project-dir . --dry-run
    python scripts/clean.py --project-dir . --all      # also remove synthesis reports
"""

import argparse
import glob
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from socks_lib import print_header, print_separator, print_result

# Directories to remove entirely
ARTIFACT_DIRS = [
    "xsim.dir",
    ".Xil",
    "webtalk",
    "__pycache__",
]

# File glob patterns to remove (relative to project root)
ARTIFACT_GLOBS = [
    # Vivado/Xsim logs and journals
    "**/*.log",
    "**/*.jou",
    "**/*.backup.log",
    "**/*.backup.jou",
    # Xsim protocol buffers
    "**/*.pb",
    # Waveform databases
    "**/*.wdb",
    # VCD waveforms
    "**/*.vcd",
    # Simulation CSVs
    "sim/*.csv",
    # Auto-generated Tcl run scripts
    "sim/_run*.tcl",
    # Python bytecode
    "**/*.pyc",
    # Testbench plots
    "tb/*.png",
]

# Synthesis report patterns (only removed with --all)
REPORT_GLOBS = [
    "sim/*_utilization.txt",
    "sim/*_timing.txt",
    "sim/*_timing_constrained.txt",
    "sim/*_timing_paths.txt",
    "sim/*_drc.txt",
]

# Directories to never descend into or remove
KEEP_DIRS = {".git", "logs", "docs"}

# Files to never remove
KEEP_FILES = {
    "synth_check.tcl",
    "synth_timing.tcl",
}


def find_artifacts(project_dir, include_reports=False):
    """Find all artifact files and directories to remove."""
    dirs_to_remove = []
    files_to_remove = []

    # Find artifact directories
    for root, dirs, _ in os.walk(project_dir):
        for d in dirs:
            if d in ARTIFACT_DIRS:
                dirs_to_remove.append(os.path.join(root, d))
        # Don't descend into artifact dirs or protected dirs
        dirs[:] = [d for d in dirs if d not in ARTIFACT_DIRS
                   and d not in KEEP_DIRS]

    # Find artifact files
    patterns = ARTIFACT_GLOBS[:]
    if include_reports:
        patterns.extend(REPORT_GLOBS)

    for pattern in patterns:
        matches = glob.glob(os.path.join(project_dir, pattern), recursive=True)
        for match in matches:
            basename = os.path.basename(match)
            if basename in KEEP_FILES or not os.path.isfile(match):
                continue
            # Skip files inside protected directories
            rel = os.path.relpath(match, project_dir)
            if any(rel.startswith(kd + os.sep) for kd in KEEP_DIRS):
                continue
            files_to_remove.append(match)

    return sorted(set(dirs_to_remove)), sorted(set(files_to_remove))


def format_size(total_bytes):
    """Format byte count as human-readable string."""
    if total_bytes >= 1024 * 1024:
        return f"{total_bytes / (1024 * 1024):.1f} MB"
    elif total_bytes >= 1024:
        return f"{total_bytes / 1024:.1f} KB"
    return f"{total_bytes} bytes"


def main() -> int:
    parser = argparse.ArgumentParser(description="Clean SOCKS project build artifacts")
    parser.add_argument("--project-dir", type=str, default=".",
                        help="Project root directory")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would be removed without deleting")
    parser.add_argument("--all", action="store_true",
                        help="Also remove synthesis reports")
    args = parser.parse_args()

    project_dir = os.path.abspath(args.project_dir)

    print_header("SOCKS Clean")
    print(f"\n  Project: {project_dir}")
    if args.dry_run:
        print("  Mode: DRY RUN (nothing will be deleted)")
    if args.all:
        print("  Scope: ALL (including synthesis reports)")

    dirs_to_remove, files_to_remove = find_artifacts(project_dir, args.all)

    if not dirs_to_remove and not files_to_remove:
        print("\n  No artifacts found -- project is clean.")
        print_separator()
        return 0

    # Calculate total size
    total_bytes = 0
    for f in files_to_remove:
        try:
            total_bytes += os.path.getsize(f)
        except OSError:
            pass
    for d in dirs_to_remove:
        for root, _, filenames in os.walk(d):
            for fname in filenames:
                try:
                    total_bytes += os.path.getsize(os.path.join(root, fname))
                except OSError:
                    pass

    # Show directories
    if dirs_to_remove:
        print(f"\n  Directories ({len(dirs_to_remove)}):")
        for d in dirs_to_remove:
            rel = os.path.relpath(d, project_dir)
            print(f"    {rel}/")

    # Show files
    if files_to_remove:
        print(f"\n  Files ({len(files_to_remove)}):")
        for f in files_to_remove:
            rel = os.path.relpath(f, project_dir)
            size = format_size(os.path.getsize(f)) if os.path.isfile(f) else "?"
            print(f"    {rel:<50s} {size:>10s}")

    print(f"\n  Total: {format_size(total_bytes)}")

    if args.dry_run:
        print()
        print_separator()
        print("  DRY RUN -- no files removed")
        print_separator()
        return 0

    # Remove files first (before directories that contain some of them)
    removed_files = 0
    for f in files_to_remove:
        try:
            os.remove(f)
            removed_files += 1
        except OSError:
            pass  # file may be inside a directory removed below

    # Remove directories
    removed_dirs = 0
    for d in dirs_to_remove:
        try:
            shutil.rmtree(d)
            removed_dirs += 1
        except OSError as e:
            print(f"    WARNING: Could not remove {d}: {e}")

    print()
    print_separator()
    print(f"  Removed {removed_dirs} directories, {removed_files} files ({format_size(total_bytes)})")
    print_separator()

    return 0


if __name__ == "__main__":
    sys.exit(main())
