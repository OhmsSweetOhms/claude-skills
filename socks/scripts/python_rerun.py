#!/usr/bin/env python3
"""
Stage 5: Python Testbench Re-run -- Run the project's Python testbench
after audit fixes and verify it still passes.

Usage:
    python scripts/python_rerun.py tb/module_tb.py
    python scripts/python_rerun.py --project-dir /path/to/project tb/module_tb.py

Exit code 0 if the testbench passes, 1 if it fails or errors.
"""

import argparse
import subprocess
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from socks_lib import print_header, print_separator, pass_str, fail_str


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Stage 5: Python Testbench Re-run")
    parser.add_argument("testbench", help="Path to Python testbench script")
    parser.add_argument("--project-dir", type=str, default=None,
                        help="Project root directory (cwd for the testbench)")
    args = parser.parse_args()

    print_header("SOCKS Stage 5 -- Python Testbench Re-run")

    tb_path = os.path.abspath(args.testbench)
    if not os.path.isfile(tb_path):
        print(f"\n  ERROR: Testbench not found: {tb_path}")
        print_separator()
        return 1

    cwd = args.project_dir or os.path.dirname(tb_path)
    print(f"\n  Testbench: {tb_path}")
    print(f"  Working dir: {cwd}")
    print(f"\n  Running...")

    try:
        result = subprocess.run(
            [sys.executable, tb_path],
            capture_output=True,
            text=True,
            cwd=cwd,
            timeout=300,
        )

        # Print output
        if result.stdout:
            for line in result.stdout.splitlines():
                print(f"  | {line}")
        if result.stderr:
            for line in result.stderr.splitlines():
                print(f"  ! {line}")

        # Check for pass/fail indicators
        output = result.stdout + result.stderr
        output_upper = output.upper()

        passed = (result.returncode == 0 and
                  ("FAIL" not in output_upper or "ALL PASS" in output_upper or
                   "ALL TESTS PASSED" in output_upper))

        print()
        print_separator()
        if passed:
            print(f"  RESULT: {pass_str()} -- testbench passed (exit code {result.returncode})")
        else:
            print(f"  RESULT: {fail_str()} -- testbench failed (exit code {result.returncode})")
        print_separator()

        return 0 if passed else 1

    except subprocess.TimeoutExpired:
        print(f"\n  ERROR: Testbench timed out after 300 seconds")
        print_separator()
        return 1
    except Exception as e:
        print(f"\n  ERROR: {e}")
        print_separator()
        return 1


if __name__ == "__main__":
    sys.exit(main())
