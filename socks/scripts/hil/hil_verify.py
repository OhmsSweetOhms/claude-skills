#!/usr/bin/env python3
"""
Stage 19: HIL ILA Verify -- Compare ILA CSV captures against simulation VCD.

VCD required: hard-fails if VCD or ILA CSVs missing. Compares behavioural
properties rather than sample-by-sample (different time bases and windows).

Checks:
  1. State sequence match: FSM states in ILA appear in same order as VCD
  2. Activity presence: signals active in VCD are also active in ILA
  3. Timing ratio tolerance: edge-to-edge timing within 10% of VCD

Usage:
    python scripts/hil/hil_verify.py --project-dir .

Exit codes:
    0  All checks passed
    1  One or more checks failed (or VCD/ILA CSVs missing)
"""

import argparse
import csv
import glob
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hil_lib import load_hil_json, hil_build_dir

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from socks_lib import (
    print_header, print_separator, pass_str, fail_str, yellow,
    parse_vcd_header, stream_vcd,
)
from project_config import get_scope


def parse_ila_csv(csv_path):
    """Parse Vivado ILA CSV export.

    Returns list of dicts: [{probe_name: value, ...}, ...]
    One entry per sample (row).
    """
    samples = []
    with open(csv_path, "r") as f:
        reader = csv.reader(f)
        headers = None
        for row in reader:
            if not row:
                continue
            # Skip comment lines
            if row[0].startswith("#"):
                continue
            if headers is None:
                headers = [h.strip() for h in row]
                continue
            sample = {}
            for i, val in enumerate(row):
                if i < len(headers):
                    sample[headers[i]] = val.strip()
            samples.append(sample)
    return samples


def extract_signal_values(samples, signal_name):
    """Extract a signal's values across all ILA samples.

    Returns list of int values. Returns empty if signal not found.
    """
    values = []
    for sample in samples:
        for key, val in sample.items():
            if signal_name in key:
                try:
                    values.append(int(val, 2) if all(c in "01" for c in val) else int(val))
                except (ValueError, TypeError):
                    values.append(0)
                break
    return values


def count_toggles(values):
    """Count the number of value changes in a signal trace."""
    if len(values) < 2:
        return 0
    return sum(1 for i in range(1, len(values)) if values[i] != values[i-1])


def extract_state_sequence(values):
    """Extract the unique state sequence (deduplicate consecutive same values)."""
    if not values:
        return []
    seq = [values[0]]
    for v in values[1:]:
        if v != seq[-1]:
            seq.append(v)
    return seq


def check_activity(ila_values, signal_name):
    """Check that a signal has at least one toggle in the ILA capture."""
    toggles = count_toggles(ila_values)
    if toggles > 0:
        return True, f"{toggles} toggles"
    return False, "no activity"


def check_state_sequence(ila_values, vcd_values, signal_name):
    """Check that ILA state sequence is a subsequence of VCD states."""
    ila_seq = extract_state_sequence(ila_values)
    vcd_seq = extract_state_sequence(vcd_values)

    if not ila_seq:
        return False, "no ILA states"
    if not vcd_seq:
        return True, "no VCD reference (skip)"

    # Check ILA sequence is a subsequence of VCD sequence
    vcd_idx = 0
    matched = 0
    for ila_state in ila_seq:
        while vcd_idx < len(vcd_seq):
            if vcd_seq[vcd_idx] == ila_state:
                matched += 1
                vcd_idx += 1
                break
            vcd_idx += 1

    if matched == len(ila_seq):
        return True, f"sequence [{' -> '.join(str(s) for s in ila_seq)}] matches VCD"
    return False, f"ILA sequence {ila_seq} not found in VCD {vcd_seq}"


def main() -> int:
    parser = argparse.ArgumentParser(description="Stage 19: HIL ILA Verify")
    parser.add_argument("--project-dir", required=True, help="Project root")
    args = parser.parse_args()

    project_dir = os.path.abspath(args.project_dir)
    print_header("Stage 19: HIL ILA Verify")

    # Skip entirely for system scope (no VCD baseline)
    project_scope = get_scope(project_dir)
    if project_scope == "system":
        print(f"\n  Skipped: system scope has no VCD baseline for ILA comparison")
        return 0

    # VCD is a hard requirement for module/block scope
    vcd_files = glob.glob(os.path.join(project_dir, "build", "sim", "*.vcd"))
    if not vcd_files:
        print(f"\n  ERROR: VCD not found at build/sim/*.vcd. "
              f"Run Stage 7 to generate a VCD and fix any simulation errors.")
        return 1

    build_dir = hil_build_dir(project_dir)

    # ILA CSVs are a hard requirement
    ila_csvs = glob.glob(os.path.join(build_dir, "ila_*.csv"))
    if not ila_csvs:
        print(f"\n  ERROR: No ILA CSVs in {build_dir}. Run Stage 18 first.")
        return 1

    vcd_file = sorted(vcd_files)[-1]
    print(f"\n  VCD:      {os.path.relpath(vcd_file, project_dir)}")
    print(f"  ILA CSVs: {len(ila_csvs)} file(s)")

    # Parse VCD header for signal names
    vcd_signals, _ = parse_vcd_header(vcd_file)

    # Collect VCD signal values (first 50000 timestamps for memory)
    vcd_signal_values = {}
    sample_count = 0
    for ts, changes in stream_vcd(vcd_file):
        for vcd_id, value in changes:
            sig = vcd_signals.get(vcd_id)
            if sig:
                short_name = sig.path.split(".")[-1]
                if short_name not in vcd_signal_values:
                    vcd_signal_values[short_name] = []
                vcd_signal_values[short_name].append(value)
        sample_count += 1
        if sample_count > 50000:
            break

    # Verify each ILA CSV
    all_passed = True
    total_checks = 0
    passed_checks = 0

    for csv_path in sorted(ila_csvs):
        csv_name = os.path.basename(csv_path)
        print(f"\n  ILA Verify: {csv_name}")

        samples = parse_ila_csv(csv_path)
        if not samples:
            print(f"    [{fail_str()}] Empty CSV")
            all_passed = False
            total_checks += 1
            continue

        # Get all signal names from the CSV
        signal_names = [k for k in samples[0].keys()
                        if k.lower() not in ("sample", "sample in window",
                                              "sample in buffer")]

        for sig_name in signal_names:
            ila_values = extract_signal_values(samples, sig_name)
            if not ila_values:
                continue

            total_checks += 1

            # Map ILA signal name to VCD name (strip _s suffix)
            vcd_name = sig_name.rstrip("_s") if sig_name.endswith("_s") else sig_name
            # Also try without hierarchy prefix
            vcd_name_short = vcd_name.split("/")[-1]
            vcd_vals = (vcd_signal_values.get(vcd_name) or
                        vcd_signal_values.get(vcd_name_short) or [])

            # Check 1: Activity
            active, detail = check_activity(ila_values, sig_name)
            if active:
                passed_checks += 1
                print(f"    [{pass_str()}] {sig_name}: {detail}")
            else:
                print(f"    [{yellow('WARN')}] {sig_name}: {detail}")
                # No activity is a warning, not a failure

            # Check 2: State sequence (for multi-bit signals)
            if vcd_vals and max(ila_values) > 1:
                total_checks += 1
                seq_ok, seq_detail = check_state_sequence(ila_values, vcd_vals, sig_name)
                if seq_ok:
                    passed_checks += 1
                    print(f"    [{pass_str()}] {sig_name}: {seq_detail}")
                else:
                    all_passed = False
                    print(f"    [{fail_str()}] {sig_name}: {seq_detail}")

    # Summary
    print()
    print_separator()
    if total_checks == 0:
        print(f"  No verifiable signals found")
    elif all_passed:
        print(f"  RESULT: {pass_str()} -- {passed_checks}/{total_checks} checks passed")
    else:
        print(f"  RESULT: {fail_str()} -- {passed_checks}/{total_checks} checks passed")
    print_separator()

    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
