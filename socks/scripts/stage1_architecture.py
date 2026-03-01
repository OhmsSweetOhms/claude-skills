#!/usr/bin/env python3
"""
Stage 1: Architecture Analysis -- Parse VHDL entities and estimate resources.

Extracts generic widths, estimates DSP48E1 usage from multiply operations,
and flags potential timing issues.

Usage:
    python scripts/stage1_architecture.py src/*.vhd
    python scripts/stage1_architecture.py --top my_module src/my_module.vhd

Exit code 0 always (advisory output only).
"""

import argparse
import re
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from socks_lib import (
    print_header, print_separator, strip_vhdl_comments,
    bold, yellow,
)


def extract_generics(lines):
    """Extract generic declarations: name, type, default value."""
    generics = []
    in_generic = False
    for line in lines:
        code = strip_vhdl_comments(line).strip()
        code_lower = code.lower()

        if "generic" in code_lower and "(" in code_lower:
            in_generic = True
            continue

        if in_generic:
            if ");" in code or (code.strip() == ")" and not code.strip().startswith("--")):
                in_generic = False
                # Check if this closing line also has a generic decl
                m = re.match(r'\s*(\w+)\s*:\s*(\w+)\s*:=\s*(.+?)[\s;)]*$',
                             code, re.IGNORECASE)
                if m:
                    generics.append((m.group(1), m.group(2), m.group(3).strip().rstrip(";)")))
                continue

            m = re.match(r'\s*(\w+)\s*:\s*(\w+)\s*:=\s*(.+?)[\s;]*$',
                         code, re.IGNORECASE)
            if m:
                generics.append((m.group(1), m.group(2), m.group(3).strip().rstrip(";")))

    return generics


def extract_ports(lines):
    """Extract port declarations: name, direction, type."""
    ports = []
    in_port = False
    for line in lines:
        code = strip_vhdl_comments(line).strip()
        code_lower = code.lower()

        if re.match(r'^\s*port\s*\(', code_lower):
            in_port = True
            continue

        if in_port:
            if code.strip() == ");":
                in_port = False
                continue

            m = re.match(
                r'\s*(\w+)\s*:\s*(in|out|inout)\s+(.+?)[\s;]*$',
                code, re.IGNORECASE)
            if m:
                ports.append((m.group(1), m.group(2).lower(), m.group(3).strip().rstrip(";")))

    return ports


def estimate_multiplies(lines):
    """Find multiply operations and estimate DSP48E1 usage."""
    multiplies = []
    width_map = {}

    # Collect signal/variable widths
    decl_pat = re.compile(
        r'(?:signal|variable)\s+(\w[\w,\s]*?)\s*:\s*'
        r'(?:signed|unsigned)\s*\(\s*(\d+)\s+downto\s+(\d+)\s*\)',
        re.IGNORECASE)

    for line in lines:
        code = strip_vhdl_comments(line)
        m = decl_pat.search(code)
        if m:
            names_str = m.group(1)
            width = int(m.group(2)) - int(m.group(3)) + 1
            for name in names_str.split(","):
                name = name.strip().lower()
                if name:
                    width_map[name] = width

    # Find multiplies
    mul_pat = re.compile(r'(\w+)\s*\*\s*(\w+)', re.IGNORECASE)
    for i, line in enumerate(lines, 1):
        code = strip_vhdl_comments(line)
        for m in mul_pat.finditer(code):
            op_a = m.group(1).lower()
            op_b = m.group(2).lower()
            w_a = width_map.get(op_a, "?")
            w_b = width_map.get(op_b, "?")

            # DSP48E1 estimate: 27x18 native, wider needs multiple
            dsp_count = "?"
            if isinstance(w_a, int) and isinstance(w_b, int):
                if w_a <= 27 and w_b <= 18:
                    dsp_count = 1
                elif w_a <= 18 and w_b <= 27:
                    dsp_count = 1
                elif max(w_a, w_b) <= 32 and min(w_a, w_b) <= 18:
                    dsp_count = 2
                else:
                    dsp_count = "2+"

            multiplies.append({
                "line": i,
                "op_a": op_a, "w_a": w_a,
                "op_b": op_b, "w_b": w_b,
                "dsp": dsp_count,
            })

    return multiplies


def check_timing_risks(lines):
    """Flag potential timing issues."""
    risks = []

    # Check for long combinational chains (multiple operations in one assign)
    for i, line in enumerate(lines, 1):
        code = strip_vhdl_comments(line)
        # Count operators in a single assignment
        ops = len(re.findall(r'[+\-*/]', code))
        if ops >= 3:
            risks.append((i, f"Complex expression ({ops} operators) -- check timing"))

    return risks


def analyze_file(filepath):
    """Analyze a single VHDL file."""
    with open(filepath, "r") as f:
        lines = f.readlines()

    filename = os.path.basename(filepath)
    print(f"\n--- {filename} ---")

    # Entity name
    for line in lines:
        m = re.match(r'\s*entity\s+(\w+)\s+is', line, re.IGNORECASE)
        if m:
            print(f"  Entity: {m.group(1)}")
            break

    # Generics
    generics = extract_generics(lines)
    if generics:
        print(f"\n  Generics:")
        for name, typ, default in generics:
            print(f"    {name:20s} : {typ:10s} := {default}")

    # Ports
    ports = extract_ports(lines)
    if ports:
        print(f"\n  Ports ({len(ports)} total):")
        for name, direction, typ in ports:
            print(f"    {name:25s} : {direction:5s} {typ}")

    # Multiplies
    muls = estimate_multiplies(lines)
    if muls:
        total_dsp = 0
        print(f"\n  Multiply operations:")
        for m in muls:
            dsp_str = str(m['dsp'])
            print(f"    L{m['line']:4d}: {m['op_a']}({m['w_a']}b) * "
                  f"{m['op_b']}({m['w_b']}b)  -> ~{dsp_str} DSP48E1")
            if isinstance(m['dsp'], int):
                total_dsp += m['dsp']
        print(f"  Estimated DSP48E1: {total_dsp}")

    # Timing risks
    risks = check_timing_risks(lines)
    if risks:
        print(f"\n  {yellow('Timing risks:')}")
        for line_no, msg in risks:
            print(f"    L{line_no:4d}: {msg}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Stage 1: Architecture Analysis")
    parser.add_argument("files", nargs="+", help="VHDL source files to analyze")
    parser.add_argument("--top", type=str, default=None,
                        help="Top-level entity name (for summary)")
    args = parser.parse_args()

    print_header("SOCKS Stage 1 -- Architecture Analysis")

    for filepath in args.files:
        if not os.path.isfile(filepath):
            print(f"\n  ERROR: File not found: {filepath}")
            continue
        analyze_file(filepath)

    print()
    print_separator()
    print("  Stage 1 complete -- review analysis above before writing VHDL")
    print_separator()

    return 0


if __name__ == "__main__":
    sys.exit(main())
