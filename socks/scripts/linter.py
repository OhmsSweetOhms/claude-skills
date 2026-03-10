#!/usr/bin/env python3
"""
Stage 3: VHDL Linter -- Run vhdl-linter on project source files.

Discovers the vhdl-linter installation, runs it on src/*.vhd, and reports
errors, warnings, and info counts. Errors are blocking; warnings and info
are advisory.

Usage:
    python scripts/linter.py src/*.vhd
    python scripts/linter.py --files src/module_a.vhd src/module_b.vhd

Exit codes:
    0  No errors (warnings/info OK)
    1  Linter reported errors or linter not found
"""

import argparse
import os
import re
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from socks_lib import print_header, print_separator, pass_str, fail_str, yellow


# Known install locations for vhdl-linter CLI
LINTER_CANDIDATES = [
    os.path.expanduser("~/vhdl-linter/dist/lib/cli/cli.js"),
]


def find_linter():
    """Find vhdl-linter CLI. Returns path or None."""
    # Check if globally installed via npm
    npx = shutil.which("vhdl-linter")
    if npx:
        return npx

    # Check known locations
    for candidate in LINTER_CANDIDATES:
        if os.path.isfile(candidate):
            return candidate

    # Try npm global list
    try:
        result = subprocess.run(
            ["npm", "list", "-g", "--parseable", "vhdl-linter"],
            capture_output=True, text=True, timeout=10)
        if result.returncode == 0 and result.stdout.strip():
            cli_path = os.path.join(
                result.stdout.strip(), "dist", "lib", "cli", "cli.js")
            if os.path.isfile(cli_path):
                return cli_path
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    return None


def run_linter(linter_path, src_dir):
    """Run vhdl-linter on a directory. Returns (exit_code, stdout, stderr)."""
    node = shutil.which("node")
    if not node:
        return 1, "", "node not found"

    # If it's a .js file, run with node; otherwise run directly
    if linter_path.endswith(".js"):
        cmd = [node, linter_path, src_dir]
    else:
        cmd = [linter_path, src_dir]

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=120)
        return result.returncode, result.stdout, result.stderr
    except subprocess.TimeoutExpired:
        return 1, "", "Linter timed out after 120s"
    except Exception as e:
        return 1, "", str(e)


def parse_summary(output):
    """Parse linter summary line: '0 error(s), 5 warning(s), 220 info(s)'."""
    m = re.search(
        r'(\d+)\s+error\(s\)\s*,\s*(\d+)\s+warning\(s\)\s*,\s*(\d+)\s+info\(s\)',
        output)
    if m:
        return int(m.group(1)), int(m.group(2)), int(m.group(3))
    return None, None, None


def main() -> int:
    parser = argparse.ArgumentParser(description="Stage 3: VHDL Linter")
    parser.add_argument("files", nargs="*", help="VHDL source files or directories")
    args = parser.parse_args()

    print_header("SOCKS Stage 3 -- VHDL Linter")

    # Find linter
    linter_path = find_linter()
    if not linter_path:
        print(f"\n  {fail_str()} vhdl-linter not found")
        print(f"  Install: npm install -g vhdl-linter")
        print(f"  Or clone: https://github.com/vhdl-linter/vhdl-linter")
        print()
        print_separator()
        print(f"  RESULT: {fail_str()} -- linter not available")
        print_separator()
        return 1

    print(f"\n  Linter: {linter_path}")

    # Determine what to lint
    if not args.files:
        print(f"\n  No files specified")
        print()
        print_separator()
        print(f"  RESULT: {fail_str()} -- no files to lint")
        print_separator()
        return 1

    # If files are individual .vhd files, find their common parent directory
    # vhdl-linter works on directories
    dirs_to_lint = set()
    for f in args.files:
        if os.path.isdir(f):
            dirs_to_lint.add(os.path.abspath(f))
        elif os.path.isfile(f):
            dirs_to_lint.add(os.path.dirname(os.path.abspath(f)))
        else:
            print(f"  WARNING: {f} not found, skipping")

    if not dirs_to_lint:
        print(f"\n  No valid paths to lint")
        print()
        print_separator()
        print(f"  RESULT: {fail_str()} -- no valid paths")
        print_separator()
        return 1

    total_errors = 0
    total_warnings = 0
    total_info = 0

    for src_dir in sorted(dirs_to_lint):
        rel_dir = os.path.relpath(src_dir)
        print(f"\n  Linting: {rel_dir}/")

        rc, stdout, stderr = run_linter(linter_path, src_dir)

        # Print output (indent for readability)
        if stdout:
            for line in stdout.splitlines():
                line = line.strip()
                if not line:
                    continue
                if "error" in line.lower() and not line.startswith(" "):
                    print(f"    {line}")
                elif "warning" in line.lower() and not line.startswith(" "):
                    print(f"    {line}")
                else:
                    print(f"    {line}")

        if stderr:
            for line in stderr.splitlines():
                if line.strip():
                    print(f"    {line.strip()}")

        # Parse summary
        errors, warnings, info = parse_summary(stdout)
        if errors is not None:
            total_errors += errors
            total_warnings += warnings
            total_info += info
            print(f"\n    Summary: {errors} error(s), {warnings} warning(s), {info} info(s)")
        elif rc != 0:
            total_errors += 1
            print(f"\n    Linter exited with code {rc}")

    # Final result
    print()
    print_separator()
    if total_errors > 0:
        print(f"  RESULT: {fail_str()} -- {total_errors} error(s) found")
        print(f"  Fix all errors before proceeding to Stage 4.")
    elif total_warnings > 0:
        print(f"  RESULT: {pass_str()} -- {total_warnings} warning(s), {total_info} info(s)")
        print(f"  Fix actionable warnings in own code. Info items are advisory.")
    else:
        print(f"  RESULT: {pass_str()} -- clean ({total_info} info(s))")
    print_separator()

    return 1 if total_errors > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
