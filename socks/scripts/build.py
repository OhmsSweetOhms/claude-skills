#!/usr/bin/env python3
"""
build.py -- Clean and rebuild all project artifacts.

Runs clean.py followed by the full SOCKS pipeline to regenerate
simulation and synthesis outputs from source.

Usage:
    python scripts/build.py --project-dir . --top my_module
    python scripts/build.py --project-dir . --top my_module --part xc7z020clg484-1
    python scripts/build.py --project-dir . --top my_module --skip-synth
    python scripts/build.py --project-dir . --top my_module --synth-only
"""

import argparse
import os
import subprocess
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

sys.path.insert(0, SCRIPT_DIR)
from socks_lib import print_header, print_separator, pass_str, fail_str


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Clean and rebuild all project artifacts",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__)
    parser.add_argument("--project-dir", type=str, default=".",
                        help="Project root directory (default: current dir)")
    parser.add_argument("--top", type=str, required=True,
                        help="Top-level entity name")
    parser.add_argument("--part", type=str, default="xc7z020clg484-1",
                        help="FPGA part (default: xc7z020clg484-1)")
    parser.add_argument("--settings", type=str, default=None,
                        help="Path to Vivado settings64.sh")
    parser.add_argument("--skip-synth", action="store_true",
                        help="Skip Vivado synthesis (stages 0-8,11 only)")
    parser.add_argument("--synth-only", action="store_true",
                        help="Only run synthesis (stages 0,9)")
    parser.add_argument("--no-clean", action="store_true",
                        help="Skip clean step")
    args = parser.parse_args()

    project_dir = os.path.abspath(args.project_dir)

    print_header("SOCKS Build")
    print(f"\n  Project: {project_dir}")
    print(f"  Top: {args.top}")
    print(f"  Part: {args.part}")

    # Step 1: Clean
    if not args.no_clean:
        print(f"\n  Step 1: Clean")
        clean_script = os.path.join(SCRIPT_DIR, "clean.py")
        rc = subprocess.run(
            [sys.executable, clean_script, "--project-dir", project_dir],
            cwd=project_dir,
        ).returncode
        if rc != 0:
            print(f"  WARNING: Clean exited with code {rc}")
    else:
        print(f"\n  Step 1: Clean (skipped)")

    # Step 2: Pipeline
    if args.synth_only:
        stages = "0,9"
        print(f"\n  Step 2: Synthesis only (stages {stages})")
    elif args.skip_synth:
        stages = "0,1,4,5,6,7,8,11"
        print(f"\n  Step 2: Pipeline without synthesis (stages {stages})")
    else:
        stages = "automated"
        print(f"\n  Step 2: Full pipeline (automated stages)")

    socks_cmd = [
        sys.executable, os.path.join(SCRIPT_DIR, "socks.py"),
        "--project-dir", project_dir,
        "--stages", stages,
        "--top", args.top,
        "--part", args.part,
    ]
    if args.settings:
        socks_cmd.extend(["--settings", args.settings])

    rc = subprocess.run(socks_cmd, cwd=project_dir).returncode

    return rc


if __name__ == "__main__":
    sys.exit(main())
