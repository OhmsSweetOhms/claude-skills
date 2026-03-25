#!/usr/bin/env python3
"""
Validate ila_trigger_plan.json against hil_top.vhd MARK_DEBUG signals.

Runs as a sub-step of Stage 14, after Vivado generates hil_top.vhd.
Checks probe names, trigger value formats, and signal widths to catch
errors before Stage 15 implementation (instead of hanging in Stage 18).

Usage (standalone):
    python scripts/hil/validate_trigger_plan.py \
        --hil-top build/hil/hil_top.vhd \
        --trigger-plan build/hil/ila_trigger_plan.json

Usage (from hil_project.py):
    from validate_trigger_plan import validate_trigger_plan
    ok = validate_trigger_plan(hil_top_path, trigger_plan_path)
"""

import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from socks_lib import pass_str, fail_str, yellow


def parse_mark_debug_signals(hil_top_path):
    """Parse hil_top.vhd for MARK_DEBUG signal declarations.

    Returns dict: signal_name -> width (int, 1 for std_logic).
    Only includes signals with MARK_DEBUG attribute set to "true".
    """
    with open(hil_top_path, "r") as f:
        text = f.read()

    # Pass 1: collect all signal declarations with their types
    # Matches: signal foo : std_logic;
    #          signal bar : std_logic_vector(6 downto 0);
    sig_pattern = re.compile(
        r'signal\s+(\w+)\s*:\s*'
        r'(std_logic_vector\s*\(\s*(\d+)\s+downto\s+(\d+)\s*\)|std_logic)',
        re.IGNORECASE,
    )
    all_signals = {}
    for m in sig_pattern.finditer(text):
        name = m.group(1)
        if m.group(3) is not None:
            width = int(m.group(3)) - int(m.group(4)) + 1
        else:
            width = 1
        all_signals[name] = width

    # Pass 2: filter to only MARK_DEBUG signals
    debug_pattern = re.compile(
        r'attribute\s+MARK_DEBUG\s+of\s+(\w+)\s*:\s*signal\s+is\s+"true"',
        re.IGNORECASE,
    )
    debug_signals = {}
    for m in debug_pattern.finditer(text):
        name = m.group(1)
        if name in all_signals:
            debug_signals[name] = all_signals[name]

    return debug_signals


def validate_trigger_plan(hil_top_path, trigger_plan_path):
    """Validate trigger plan against hil_top.vhd MARK_DEBUG signals.

    Returns True if no errors (warnings are OK), False if any errors.
    """
    # Load trigger plan
    with open(trigger_plan_path, "r") as f:
        plan = json.load(f)

    captures = plan.get("captures", [])
    if not captures:
        print(f"  Trigger plan has no captures -- skipping validation")
        return True

    # Parse hil_top.vhd
    debug_signals = parse_mark_debug_signals(hil_top_path)
    if not debug_signals:
        print(f"  {fail_str()}: No MARK_DEBUG signals found in hil_top.vhd")
        return False

    print(f"\n  Validating ILA trigger plan ({len(captures)} captures, "
          f"{len(debug_signals)} MARK_DEBUG signals)...")

    errors = 0
    warnings = 0

    for cap in captures:
        name = cap.get("name", "unnamed")
        probe = cap.get("trigger_probe", "")
        value = cap.get("trigger_value", "")
        compare = cap.get("trigger_compare", "")

        # Check 1: probe exists in MARK_DEBUG signals
        if probe not in debug_signals:
            print(f"    {fail_str()} {name}: probe '{probe}' not in MARK_DEBUG signals")
            available = ", ".join(sorted(debug_signals.keys()))
            print(f"         Available: {available}")
            errors += 1
            continue

        width = debug_signals[probe]

        # Check 2: trigger_value is binary only
        if not re.fullmatch(r'[01]+', value):
            print(f"    {fail_str()} {name}: trigger_value '{value}' is not binary "
                  f"(must be only 0/1 characters)")
            errors += 1
            continue

        # Check 3: width matches
        if len(value) != width:
            print(f"    {fail_str()} {name}: trigger_value width {len(value)} "
                  f"!= signal '{probe}' width {width}")
            errors += 1
            continue

        # Check 4: compare is a valid ILA comparator
        valid_compares = ("eq", "neq", "gt", "lt", "gteq", "lteq")
        if compare not in valid_compares:
            print(f"    {fail_str()} {name}: trigger_compare '{compare}' "
                  f"not valid (must be one of: {', '.join(valid_compares)})")
            errors += 1
            continue

        # Check 5: all-zero warning
        if all(c == '0' for c in value):
            print(f"    {yellow('WARN')} {name}: {probe} ({width}-bit, trigger={value}) "
                  f"-- all-zero trigger is likely idle state")
            warnings += 1
        else:
            print(f"    {pass_str()} {name}: {probe} ({width}-bit, trigger={value})")

    # Summary
    if errors > 0:
        print(f"\n  {fail_str()}: {errors} error(s), {warnings} warning(s) "
              f"in trigger plan")
        return False
    elif warnings > 0:
        print(f"\n  {pass_str()} (with {warnings} warning(s))")
        return True
    else:
        print(f"\n  {pass_str()}: All {len(captures)} captures validated")
        return True


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate ILA trigger plan against hil_top.vhd MARK_DEBUG signals")
    parser.add_argument("--hil-top", required=True,
                        help="Path to hil_top.vhd")
    parser.add_argument("--trigger-plan", required=True,
                        help="Path to ila_trigger_plan.json")
    args = parser.parse_args()

    if not os.path.isfile(args.hil_top):
        print(f"ERROR: {args.hil_top} not found")
        return 1
    if not os.path.isfile(args.trigger_plan):
        print(f"ERROR: {args.trigger_plan} not found")
        return 1

    ok = validate_trigger_plan(args.hil_top, args.trigger_plan)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
