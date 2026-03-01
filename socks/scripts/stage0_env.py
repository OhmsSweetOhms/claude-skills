#!/usr/bin/env python3
"""
Stage 0: Environment Setup -- Vivado/Xsim discovery and verification.

Searches for Vivado's settings64.sh, sources it, and verifies that all
required EDA tools (xvhdl, xvlog, xelab, xsim, vivado) are on PATH.

Usage:
    python scripts/stage0_env.py
    python scripts/stage0_env.py --settings /tools/Xilinx/Vivado/2023.2/settings64.sh

Exit code 0 if all tools found, 1 otherwise.
"""

import argparse
import sys
import os

# Allow importing socks_lib from the same directory
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from socks_lib import (
    find_vivado_settings, verify_tools, get_vivado_version,
    print_header, print_result, print_separator, REQUIRED_TOOLS,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Stage 0: Environment Setup")
    parser.add_argument("--settings", type=str, default=None,
                        help="Explicit path to Vivado settings64.sh")
    args = parser.parse_args()

    print_header("SOCKS Stage 0 -- Environment Setup")

    # Discover settings64.sh
    settings_path = args.settings or find_vivado_settings()

    if settings_path is None:
        print("\n  ERROR: Could not find Vivado settings64.sh")
        print("  Searched:")
        print("    /tools/Xilinx/Vivado/*/settings64.sh")
        print("    /opt/Xilinx/Vivado/*/settings64.sh")
        print("    ~/Xilinx/Vivado/*/settings64.sh")
        print("\n  Use --settings /path/to/settings64.sh to specify manually.")
        print_separator()
        return 1

    if not os.path.isfile(settings_path):
        print(f"\n  ERROR: settings64.sh not found at: {settings_path}")
        print_separator()
        return 1

    version = get_vivado_version(settings_path)
    print(f"\n  Vivado settings: {settings_path}")
    print(f"  Vivado version:  {version or 'unknown'}")

    # Verify tools
    print(f"\n  Tool discovery:")
    tools = verify_tools(settings_path)
    all_found = True

    for tool in REQUIRED_TOOLS:
        path = tools.get(tool)
        if path:
            print_result(f"{tool:8s} -> {path}", True)
        else:
            print_result(f"{tool:8s} -> NOT FOUND", False)
            all_found = False

    # Python version
    print(f"\n  Python: {sys.executable}")
    print(f"  Python version: {sys.version.split()[0]}")

    # Summary
    print()
    print_separator()
    if all_found:
        print("  RESULT: PASS -- all tools found")
        print(f"\n  Shell prefix for all Bash commands:")
        print(f"    bash -c 'source {settings_path} && <command>'")
    else:
        print("  RESULT: FAIL -- missing tools")
        print("  Install Vivado or check your settings64.sh path.")
    print_separator()

    return 0 if all_found else 1


if __name__ == "__main__":
    sys.exit(main())
