#!/usr/bin/env python3
"""
architecture-system.py -- Stage 1 for system scope designs.

Validates DESIGN-INTENT.md for system scope (Xilinx IP block design),
checks board references, and sets dut.entity in socks.json.

Does NOT generate TCL or run Vivado -- that is Claude's job in Stage 20
(system design loop).

Usage:
    python scripts/architecture-system.py --project-dir .
"""

import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from socks_lib import print_header, print_result, print_separator, pass_str, fail_str, yellow
from project_config import load_project_config, update_project_config

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SKILL_DIR = os.path.dirname(SCRIPT_DIR)


def check_design_intent(project_dir):
    """Validate DESIGN-INTENT.md has system scope sections."""
    info = []
    warnings = []
    passed = True

    intent_path = os.path.join(project_dir, "docs", "DESIGN-INTENT.md")
    if not os.path.isfile(intent_path):
        info.append(("DESIGN-INTENT.md", False, "NOT FOUND in docs/"))
        return False, warnings, info

    with open(intent_path, "r") as f:
        content = f.read()

    info.append(("DESIGN-INTENT.md", True, "found"))

    # Check required system scope sections
    required_sections = [
        ("What Are We Building", r"##\s+What Are We Building"),
        ("IP Configuration", r"##\s+IP Configuration"),
        ("Pin Assignment", r"##\s+Pin Assignment"),
        ("Memory Map", r"##\s+Memory Map"),
        ("Success Criteria", r"##\s+Success Criteria"),
    ]

    for name, pattern in required_sections:
        if re.search(pattern, content, re.IGNORECASE):
            info.append((f"  {name}", True, "present"))
        else:
            info.append((f"  {name}", False, "MISSING"))
            warnings.append(f"DESIGN-INTENT.md missing section: {name}")

    return passed, warnings, info


def check_board_references(project_dir, config):
    """Check for board preset and master XDC."""
    info = []
    warnings = []

    preset = config.get("board", {}).get("preset")
    if not preset:
        info.append(("Board preset", None, "not configured in socks.json"))
        warnings.append("No board.preset in socks.json -- board references not checked")
        return True, warnings, info

    boards_dir = os.path.join(SKILL_DIR, "references", "boards", preset)
    if os.path.isdir(boards_dir):
        info.append(("Board directory", True, f"references/boards/{preset}/"))

        # Check for preset TCL
        preset_files = [f for f in os.listdir(boards_dir) if f.endswith("_preset.tcl")]
        if preset_files:
            info.append(("  PS7 preset", True, preset_files[0]))
        else:
            info.append(("  PS7 preset", False, "no *_preset.tcl found"))
            warnings.append(f"No PS7 preset TCL in references/boards/{preset}/")

        # Check for master XDC
        xdc_files = [f for f in os.listdir(boards_dir) if f.endswith(".xdc")]
        if xdc_files:
            info.append(("  Master XDC", True, xdc_files[0]))
        else:
            info.append(("  Master XDC", None, "not present (optional)"))

        # Check for board.md
        if os.path.isfile(os.path.join(boards_dir, "board.md")):
            info.append(("  Board info", True, "board.md"))
    else:
        info.append(("Board directory", False,
                     f"references/boards/{preset}/ not found"))
        warnings.append(
            f"Board preset '{preset}' not found. Claude cannot reliably find "
            f"board docs via web search. User should provide: "
            f"(a) PS7 preset TCL, (b) master XDC, (c) board datasheet.")

    return True, warnings, info


def set_dut_entity(project_dir, entity="system_wrapper"):
    """Set dut.entity in socks.json."""
    result = update_project_config(project_dir, {"dut": {"entity": entity}})
    return result is not None


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Stage 1 (system scope): Architecture validation")
    parser.add_argument("--project-dir", type=str, default=".",
                        help="Project root directory")
    parser.add_argument("--top", type=str, default=None,
                        help="Top entity name override (default: system_wrapper)")
    args = parser.parse_args()

    project_dir = os.path.abspath(args.project_dir)

    print_header("SOCKS Stage 1 -- Architecture (System Scope)")
    print(f"\n  Project: {project_dir}")

    all_passed = True
    all_warnings = []

    # Load socks.json
    config = load_project_config(project_dir)
    if config is None:
        print(f"\n  ERROR: socks.json not found. Run --design to create it.")
        return 1

    scope = config.get("scope")
    if scope != "system":
        print(f"\n  ERROR: socks.json scope is '{scope}', expected 'system'.")
        print(f"  Use architecture.py for module/block scope.")
        return 1

    print(f"  Scope:   {scope}")

    # Section 1: Validate DESIGN-INTENT.md
    print(f"\n  Design Intent:")
    di_ok, di_warn, di_info = check_design_intent(project_dir)
    for name, ok, detail in di_info:
        if ok is None:
            print(f"    [----] {name:24s} {detail}")
        else:
            print_result(f"{name:24s} {detail}", ok)
    if not di_ok:
        all_passed = False
    all_warnings.extend(di_warn)

    # Section 2: Check board references
    print(f"\n  Board References:")
    br_ok, br_warn, br_info = check_board_references(project_dir, config)
    for name, ok, detail in br_info:
        if ok is None:
            print(f"    [----] {name:24s} {detail}")
        else:
            print_result(f"{name:24s} {detail}", ok)
    all_warnings.extend(br_warn)

    # Section 3: Set dut.entity
    entity = args.top or "system_wrapper"
    print(f"\n  DUT Entity:")
    if set_dut_entity(project_dir, entity):
        print_result(f"{'dut.entity':24s} set to '{entity}' in socks.json", True)
    else:
        print_result(f"{'dut.entity':24s} failed to update socks.json", False)
        all_passed = False

    # Section 4: System design loop guidance
    print(f"\n  Next Steps:")
    print(f"    Stage 20 (System Design Loop) -- Claude authors:")
    print(f"      1. build/synth/create_bd.tcl   (Vivado block design TCL)")
    print(f"      2. build/synth/build_bitstream.tcl (synthesis/impl/bitstream)")
    print(f"      3. constraints/*.xdc            (pin assignment + I/O standard)")
    print(f"      4. docs/ARCHITECTURE.md         (data flow, clocking, rate summary)")
    print(f"    Read references/design-loop-system.md for the full guide.")

    # Warnings
    if all_warnings:
        print(f"\n  Warnings:")
        for w in all_warnings:
            print(f"    - {w}")

    # Summary
    print()
    print_separator()
    if all_passed and not all_warnings:
        print(f"  RESULT: {pass_str()} -- system architecture validated")
    elif all_passed:
        print(f"  RESULT: {pass_str()} -- validated (with warnings)")
    else:
        print(f"  RESULT: {fail_str()} -- validation failed")
    print_separator()

    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
