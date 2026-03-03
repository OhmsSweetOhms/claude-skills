#!/usr/bin/env python3
"""
Stage 4: Synthesis Audit -- Static VHDL checks (12 rules).

Checks each VHDL source file for common synthesis hazards and coding
standard violations.

Usage:
    python scripts/audit.py src/*.vhd
    python scripts/audit.py --files src/module_a.vhd src/module_b.vhd

Exit code 0 if all checks pass, 1 if any fail.
"""

import argparse
import re
import sys
import os
from dataclasses import dataclass, field
from typing import List, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from socks_lib import (
    print_header, print_result, print_separator, strip_vhdl_comments,
)


@dataclass
class Violation:
    file: str
    line: int
    text: str


@dataclass
class CheckResult:
    name: str
    passed: bool
    violations: List[Violation] = field(default_factory=list)


def basename(path: str) -> str:
    return os.path.basename(path)


# ---------------------------------------------------------------------------
# 12 checks
# ---------------------------------------------------------------------------

def check_two_star_n(path: str, lines: List[str]) -> CheckResult:
    """Check 1: No 2**N integer overflow."""
    result = CheckResult(name="No 2**N integer overflow", passed=True)
    pat = re.compile(r'\b2\s*\*\*', re.IGNORECASE)
    for i, raw_line in enumerate(lines, 1):
        code = strip_vhdl_comments(raw_line)
        if pat.search(code):
            result.passed = False
            result.violations.append(
                Violation(basename(path), i, raw_line.rstrip()))
    return result


def check_saturation_constants(path: str, lines: List[str]) -> CheckResult:
    """Check 2: No to_signed(2**N, ...) or to_unsigned(2**N, ...)."""
    result = CheckResult(
        name="Saturation constants use bit-vector aggregates", passed=True)
    pat = re.compile(
        r'to_(signed|unsigned)\s*\(\s*2\s*\*\*', re.IGNORECASE)
    for i, raw_line in enumerate(lines, 1):
        code = strip_vhdl_comments(raw_line)
        if pat.search(code):
            result.passed = False
            result.violations.append(
                Violation(basename(path), i, raw_line.rstrip()))
    return result


def check_abs_signed(path: str, lines: List[str]) -> CheckResult:
    """Check 3: No abs() on signed values."""
    result = CheckResult(name="No abs() on signed values", passed=True)
    pat = re.compile(r'\babs\s*\(', re.IGNORECASE)
    for i, raw_line in enumerate(lines, 1):
        code = strip_vhdl_comments(raw_line)
        if pat.search(code):
            result.passed = False
            result.violations.append(
                Violation(basename(path), i, raw_line.rstrip()))
    return result


def check_dead_signals(path: str, lines: List[str]) -> CheckResult:
    """Check 4: No dead signals (declared but never used)."""
    result = CheckResult(name="No dead signals", passed=True)

    sig_decl = re.compile(r'^\s*signal\s+(\w[\w,\s]*?)\s*:', re.IGNORECASE)
    declared_signals: List[Tuple[str, int]] = []

    for i, raw_line in enumerate(lines, 1):
        code = strip_vhdl_comments(raw_line)
        m = sig_decl.match(code)
        if m:
            names_str = m.group(1)
            for name in names_str.split(","):
                name = name.strip().lower()
                if name:
                    declared_signals.append((name, i))

    full_code_lines = [strip_vhdl_comments(l) for l in lines]

    for sig_name, decl_line in declared_signals:
        usage_pat = re.compile(r'\b' + re.escape(sig_name) + r'\b',
                               re.IGNORECASE)
        use_count = 0
        for i, code_line in enumerate(full_code_lines, 1):
            if i == decl_line:
                continue
            if usage_pat.search(code_line):
                use_count += 1

        if use_count == 0:
            result.passed = False
            result.violations.append(
                Violation(basename(path), decl_line,
                          f"signal '{sig_name}' declared but never used"))

    return result


def check_multiply_widths(path: str, lines: List[str]) -> CheckResult:
    """Check 5: Product widths >= sum of operand widths."""
    result = CheckResult(
        name="Product widths >= sum of operand widths", passed=True)

    width_map: dict = {}
    decl_pat = re.compile(
        r'(?:signal|variable)\s+(\w[\w,\s]*?)\s*:\s*'
        r'(?:signed|unsigned)\s*\(\s*(\d+)\s+downto\s+(\d+)\s*\)',
        re.IGNORECASE)
    for raw_line in lines:
        code = strip_vhdl_comments(raw_line)
        m = decl_pat.search(code)
        if m:
            names_str = m.group(1)
            width = int(m.group(2)) - int(m.group(3)) + 1
            for name in names_str.split(","):
                name = name.strip().lower()
                if name:
                    width_map[name] = width

    assign_mul_pat = re.compile(
        r'(\w+)\s*(?:\([^)]*\))?\s*<=\s*.*?\b(\w+)\s*\*\s*(\w+)',
        re.IGNORECASE)
    for i, raw_line in enumerate(lines, 1):
        code = strip_vhdl_comments(raw_line)
        m = assign_mul_pat.search(code)
        if m:
            target = m.group(1).strip().lower()
            op_a = m.group(2).strip().lower()
            op_b = m.group(3).strip().lower()

            if op_a in width_map and op_b in width_map:
                required_width = width_map[op_a] + width_map[op_b]
                if target in width_map:
                    if width_map[target] < required_width:
                        result.passed = False
                        result.violations.append(
                            Violation(basename(path), i,
                                      f"multiply {op_a}({width_map[op_a]}b) * "
                                      f"{op_b}({width_map[op_b]}b) -> "
                                      f"{target}({width_map[target]}b), "
                                      f"need {required_width}b"))
    return result


def check_static_loop_bounds(path: str, lines: List[str]) -> CheckResult:
    """Check 6: All for loop bounds are static constants."""
    result = CheckResult(
        name="All for loop bounds are static constants", passed=True)
    loop_pat = re.compile(
        r'\bfor\s+(\w+)\s+in\s+(.+?)\s+loop\b', re.IGNORECASE)

    for i, raw_line in enumerate(lines, 1):
        code = strip_vhdl_comments(raw_line)
        m = loop_pat.search(code)
        if m:
            range_expr = m.group(2).strip()
            cleaned = range_expr
            cleaned = re.sub(r"\w+'\w+", "", cleaned)
            cleaned = re.sub(r'\b\d+\b', '', cleaned)
            cleaned = re.sub(
                r'\b(to|downto|natural|integer|positive)\b', '',
                cleaned, flags=re.IGNORECASE)
            cleaned = re.sub(r'[\s+\-*/()]+', '', cleaned)
            remaining_ids = re.findall(r'[a-z]\w*', cleaned)
            if remaining_ids:
                result.passed = False
                result.violations.append(
                    Violation(basename(path), i,
                              f"for loop with potentially non-static "
                              f"bound: {range_expr}"))
    return result


def check_async_reg(path: str, lines: List[str]) -> CheckResult:
    """Check 7: ASYNC_REG attribute on CDC synchroniser pairs."""
    result = CheckResult(
        name="ASYNC_REG attribute on CDC synchroniser pairs", passed=True)

    sync_signals = set()
    sig_decl = re.compile(r'^\s*signal\s+(\w+)\s*:', re.IGNORECASE)
    for raw_line in lines:
        code = strip_vhdl_comments(raw_line)
        m = sig_decl.match(code)
        if m:
            name = m.group(1).lower()
            if name.endswith("_sync1") or name.endswith("_sync2"):
                sync_signals.add(name)

    if not sync_signals:
        return result

    attr_pat = re.compile(
        r'attribute\s+ASYNC_REG\s+of\s+(\w+)\s*:\s*signal\s+is\s+"TRUE"',
        re.IGNORECASE)
    attributed_signals = set()
    for raw_line in lines:
        code = strip_vhdl_comments(raw_line)
        m = attr_pat.search(code)
        if m:
            attributed_signals.add(m.group(1).lower())

    for sig in sorted(sync_signals):
        if sig not in attributed_signals:
            for i, raw_line in enumerate(lines, 1):
                code = strip_vhdl_comments(raw_line)
                if re.match(r'^\s*signal\s+' + re.escape(sig) + r'\s*:',
                            code, re.IGNORECASE):
                    result.passed = False
                    result.violations.append(
                        Violation(basename(path), i,
                                  f"CDC signal '{sig}' missing ASYNC_REG"))
                    break

    return result


def check_no_clk_event(path: str, lines: List[str]) -> CheckResult:
    """Check 8: No clk'event usage (only rising_edge)."""
    result = CheckResult(name="No clk'event usage", passed=True)
    pat = re.compile(r"\bclk\s*'\s*event\b", re.IGNORECASE)
    for i, raw_line in enumerate(lines, 1):
        code = strip_vhdl_comments(raw_line)
        if pat.search(code):
            result.passed = False
            result.violations.append(
                Violation(basename(path), i, raw_line.rstrip()))
    return result


def check_sync_reset(path: str, lines: List[str]) -> CheckResult:
    """Check 9: Reset inside rising_edge block (synchronous reset)."""
    result = CheckResult(
        name="Reset inside rising_edge block", passed=True)

    sens_pat = re.compile(
        r'process\s*\(\s*.*\brst_n\b.*\)', re.IGNORECASE)
    for i, raw_line in enumerate(lines, 1):
        code = strip_vhdl_comments(raw_line)
        if sens_pat.search(code):
            result.passed = False
            result.violations.append(
                Violation(basename(path), i,
                          "rst_n in sensitivity list (async reset pattern)"))

    in_process = False
    found_rising_edge = False
    for i, raw_line in enumerate(lines, 1):
        code = strip_vhdl_comments(raw_line).lower().strip()

        if re.search(r'\bprocess\b', code):
            in_process = True
            found_rising_edge = False

        if in_process and re.search(r'rising_edge\s*\(\s*clk\s*\)', code):
            found_rising_edge = True

        if (in_process and not found_rising_edge and
                re.search(r"rst_n\s*=\s*'0'", code)):
            result.passed = False
            result.violations.append(
                Violation(basename(path), i,
                          "rst_n check before rising_edge(clk)"))

        if re.search(r'\bend\s+process\b', code):
            in_process = False
            found_rising_edge = False

    return result


def check_architecture_rtl(path: str, lines: List[str]) -> CheckResult:
    """Check 10: Architecture name is 'rtl'."""
    result = CheckResult(name="Architecture name is 'rtl'", passed=True)
    arch_pat = re.compile(
        r'^\s*architecture\s+(\w+)\s+of\b', re.IGNORECASE)
    for i, raw_line in enumerate(lines, 1):
        code = strip_vhdl_comments(raw_line)
        m = arch_pat.search(code)
        if m:
            if m.group(1).lower() != "rtl":
                result.passed = False
                result.violations.append(
                    Violation(basename(path), i,
                              f"architecture name '{m.group(1)}' "
                              f"(expected 'rtl')"))
    return result


def check_no_component_decl(path: str, lines: List[str]) -> CheckResult:
    """Check 11: No component declarations."""
    result = CheckResult(
        name="No component declarations", passed=True)
    comp_pat = re.compile(r'^\s*component\s+\w+', re.IGNORECASE)
    for i, raw_line in enumerate(lines, 1):
        code = strip_vhdl_comments(raw_line)
        if comp_pat.search(code):
            result.passed = False
            result.violations.append(
                Violation(basename(path), i, raw_line.rstrip()))
    return result


def check_state_prefix(path: str, lines: List[str]) -> CheckResult:
    """Check 12: State enum values use ST_ prefix."""
    result = CheckResult(
        name="State enum values use ST_ prefix", passed=True)

    full_code = "".join(strip_vhdl_comments(l) for l in lines)
    full_code_flat = re.sub(r'\s+', ' ', full_code)

    type_pat = re.compile(
        r'type\s+(\w+)\s+is\s*\(([^)]+)\)', re.IGNORECASE)

    for m in type_pat.finditer(full_code_flat):
        type_name = m.group(1).lower()
        if "state" not in type_name:
            continue

        enum_values = [v.strip() for v in m.group(2).split(",")]
        for val in enum_values:
            val_clean = val.strip()
            if val_clean and not val_clean.upper().startswith("ST_"):
                line_no = 0
                for i, raw_line in enumerate(lines, 1):
                    if val_clean in strip_vhdl_comments(raw_line):
                        line_no = i
                        break
                result.passed = False
                result.violations.append(
                    Violation(basename(path), line_no,
                              f"state value '{val_clean}' missing ST_ prefix "
                              f"in type '{m.group(1)}'"))
    return result


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

ALL_CHECKS = [
    check_two_star_n,
    check_saturation_constants,
    check_abs_signed,
    check_dead_signals,
    check_multiply_widths,
    check_static_loop_bounds,
    check_async_reg,
    check_no_clk_event,
    check_sync_reset,
    check_architecture_rtl,
    check_no_component_decl,
    check_state_prefix,
]


def run_all_checks(path: str) -> List[CheckResult]:
    with open(path, "r") as f:
        lines = f.readlines()
    return [chk(path, lines) for chk in ALL_CHECKS]


def main() -> int:
    parser = argparse.ArgumentParser(description="Stage 4: Synthesis Audit")
    parser.add_argument("files", nargs="+", help="VHDL source files to audit")
    args = parser.parse_args()

    any_fail = False

    print_header("SOCKS Stage 4 -- Synthesis Audit")

    for path in args.files:
        if not os.path.isfile(path):
            print(f"\n  ERROR: File not found: {path}")
            any_fail = True
            continue

        fname = basename(path)
        print(f"\n--- {fname} ---")
        results = run_all_checks(path)

        for idx, r in enumerate(results, 1):
            status = "PASS" if r.passed else "FAIL"
            print(f"  [{status}] {idx:2d}. {r.name}")
            if not r.passed:
                any_fail = True
                for v in r.violations:
                    print(f"         {v.file}:{v.line}: {v.text}")

    print(f"\n{'-' * 72}")
    total = len(args.files) * len(ALL_CHECKS)
    fails = sum(1 for path in args.files if os.path.isfile(path)
                for r in run_all_checks(path) if not r.passed)
    passed = total - fails

    print()
    print_separator()
    if any_fail:
        print("  RESULT: FAIL -- one or more checks failed")
    else:
        print(f"  RESULT: ALL {len(ALL_CHECKS)} CHECKS PASSED")
    print_separator()

    return 1 if any_fail else 0


if __name__ == "__main__":
    sys.exit(main())
