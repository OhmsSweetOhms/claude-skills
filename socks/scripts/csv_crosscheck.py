#!/usr/bin/env python3
"""
Stage 8: CSV Cross-Check -- Compare simulation CSV against Python model output.

Reads two CSV files (simulation and Python model), aligns by event count,
and compares each signal column within a tolerance.

Usage:
    python scripts/csv_crosscheck.py sim.csv model.csv
    python scripts/csv_crosscheck.py sim.csv model.csv --tolerance 1 --skip-cols cycle,time_ns

Exit code 0 if all values match within tolerance, 1 on first divergence.
"""

import argparse
import csv
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from socks_lib import print_header, print_separator, pass_str, fail_str


def read_csv(filepath):
    """Read CSV file, return (headers, rows_as_dicts)."""
    with open(filepath, "r") as f:
        reader = csv.DictReader(f)
        headers = reader.fieldnames or []
        rows = list(reader)
    return headers, rows


def parse_value(s):
    """Parse a string value to int or float."""
    s = s.strip()
    if not s:
        return None
    try:
        return int(s)
    except ValueError:
        pass
    try:
        return float(s)
    except ValueError:
        return s


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Stage 8: CSV Cross-Check")
    parser.add_argument("sim_csv", help="Simulation CSV file")
    parser.add_argument("model_csv", help="Python model CSV file")
    parser.add_argument("--tolerance", type=float, default=0,
                        help="Numeric tolerance for comparisons (default: exact)")
    parser.add_argument("--skip-cols", type=str, default="",
                        help="Comma-separated column names to skip")
    parser.add_argument("--max-errors", type=int, default=10,
                        help="Maximum number of errors to report (default: 10)")
    args = parser.parse_args()

    print_header("SOCKS Stage 8 -- CSV Cross-Check")

    for path, label in [(args.sim_csv, "Simulation"),
                        (args.model_csv, "Model")]:
        if not os.path.isfile(path):
            print(f"\n  ERROR: {label} CSV not found: {path}")
            print_separator()
            return 1

    sim_headers, sim_rows = read_csv(args.sim_csv)
    mod_headers, mod_rows = read_csv(args.model_csv)

    skip_cols = set(c.strip() for c in args.skip_cols.split(",") if c.strip())

    print(f"\n  Simulation CSV: {args.sim_csv}")
    print(f"    Columns: {', '.join(sim_headers)}")
    print(f"    Rows: {len(sim_rows)}")
    print(f"\n  Model CSV: {args.model_csv}")
    print(f"    Columns: {', '.join(mod_headers)}")
    print(f"    Rows: {len(mod_rows)}")

    # Find common columns
    common_cols = [c for c in sim_headers if c in mod_headers and c not in skip_cols]
    print(f"\n  Comparing columns: {', '.join(common_cols)}")
    print(f"  Skipping: {', '.join(skip_cols) if skip_cols else 'none'}")
    print(f"  Tolerance: {args.tolerance}")

    if not common_cols:
        print(f"\n  ERROR: No common columns to compare")
        print_separator()
        return 1

    # Align by row index (event count)
    min_rows = min(len(sim_rows), len(mod_rows))
    if len(sim_rows) != len(mod_rows):
        print(f"\n  WARNING: Row count mismatch -- comparing first {min_rows} rows")

    errors = []
    for i in range(min_rows):
        for col in common_cols:
            sim_val = parse_value(sim_rows[i].get(col, ""))
            mod_val = parse_value(mod_rows[i].get(col, ""))

            if sim_val is None or mod_val is None:
                continue

            # Compare
            match = False
            if isinstance(sim_val, (int, float)) and isinstance(mod_val, (int, float)):
                match = abs(sim_val - mod_val) <= args.tolerance
            else:
                match = str(sim_val) == str(mod_val)

            if not match:
                errors.append({
                    "row": i,
                    "col": col,
                    "sim": sim_val,
                    "model": mod_val,
                })
                if len(errors) >= args.max_errors:
                    break

        if len(errors) >= args.max_errors:
            break

    # Report
    if errors:
        print(f"\n  Divergences found ({len(errors)} shown):")
        for e in errors:
            print(f"    Row {e['row']:6d}, {e['col']:20s}: "
                  f"sim={e['sim']}, model={e['model']}")

    print()
    print_separator()
    if not errors:
        print(f"  RESULT: {pass_str()} -- {min_rows} rows x {len(common_cols)} columns match")
    else:
        print(f"  RESULT: {fail_str()} -- {len(errors)} divergences found")
        print(f"  First divergence at row {errors[0]['row']}, column '{errors[0]['col']}'")
    print_separator()

    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
