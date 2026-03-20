#!/usr/bin/env python3
"""
Stage 10: Vivado Synthesis (System Scope) -- Run user-authored TCL scripts
for block design creation and bitstream generation, then parse reports.

System scope projects use Xilinx IP block designs (no custom VHDL in src/).
The build flow is:
  1. create_bd.tcl   -> Vivado block design project
  2. build_bitstream.tcl -> synthesis + implementation + bitstream

Usage:
    python scripts/synth-system.py --project-dir /path/to/project
    python scripts/synth-system.py --project-dir . --settings /tools/Xilinx/Vivado/2023.2/settings64.sh

Exit code 0 if synthesis succeeds and timing is met, 1 otherwise.
"""

import argparse
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from socks_lib import (
    find_vivado_settings, parse_utilization_report, parse_timing_report,
    print_header, print_separator, pass_str, fail_str,
)


def print_utilization(rows):
    """Print utilization table."""
    if not rows:
        print("    No utilization data found")
        return

    print(f"    {'Resource':<25s} {'Used':>8s} {'Available':>10s} {'Util%':>8s}")
    print(f"    {'-'*25} {'-'*8} {'-'*10} {'-'*8}")
    for r in rows:
        print(f"    {r.resource:<25s} {r.used:>8d} {r.available:>10d} {r.util_pct:>7.2f}%")


def print_timing(results):
    """Print timing table."""
    if not results:
        print("    No timing data found")
        return

    print(f"    {'Check':<25s} {'Slack':>10s} {'Status':>8s}")
    print(f"    {'-'*25} {'-'*10} {'-'*8}")
    for t in results:
        status = "MET" if t.met else "VIOLATED"
        print(f"    {t.check:<25s} {t.slack_ns:>+9.3f}ns {status:>8s}")


def run_vivado(settings_path, tcl_path, project_dir, label, timeout=1800):
    """Run Vivado in batch mode with cwd=project_dir."""
    tcl_rel = os.path.relpath(tcl_path, project_dir)
    log_name = os.path.splitext(os.path.basename(tcl_path))[0]
    cmd = (f'source "{settings_path}" && '
           f'vivado -mode batch -source "{tcl_rel}" '
           f'-log "build/synth/{log_name}.log" '
           f'-journal "build/synth/{log_name}.jou"')

    print(f"\n  Running {label}...")
    print(f"    TCL: {tcl_rel}")

    result = subprocess.run(
        ["bash", "-c", cmd],
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=project_dir,
    )

    if result.returncode != 0:
        print(f"    ERROR: Vivado exited with code {result.returncode}")
        if result.stderr:
            for line in result.stderr.splitlines()[-10:]:
                print(f"    ! {line}")
        return False

    print(f"    Vivado completed successfully")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Stage 10: Vivado Synthesis (System Scope)")
    parser.add_argument("--project-dir", required=True,
                        help="Project root directory")
    parser.add_argument("--settings", type=str, default=None,
                        help="Path to Vivado settings64.sh")
    args = parser.parse_args()

    project_dir = os.path.abspath(args.project_dir)

    print_header("SOCKS Stage 10 -- Vivado Synthesis (System Scope)")
    print(f"\n  Project: {project_dir}")

    # Discover Vivado
    settings_path = args.settings or find_vivado_settings()
    if settings_path is None:
        print("\n  ERROR: Could not find Vivado settings64.sh")
        print("  Use --settings or run env.py first")
        print_separator()
        return 1

    # Check required TCL scripts
    create_bd = os.path.join(project_dir, "build", "synth", "create_bd.tcl")
    build_bit = os.path.join(project_dir, "build", "synth", "build_bitstream.tcl")

    if not os.path.isfile(create_bd):
        print(f"\n  ERROR: {os.path.relpath(create_bd, project_dir)} not found")
        print("  Stage 20 (design loop) should have created this file.")
        print_separator()
        return 1

    if not os.path.isfile(build_bit):
        print(f"\n  ERROR: {os.path.relpath(build_bit, project_dir)} not found")
        print("  Stage 20 (design loop) should have created this file.")
        print_separator()
        return 1

    print(f"  TCL scripts found:")
    print(f"    {os.path.relpath(create_bd, project_dir)}")
    print(f"    {os.path.relpath(build_bit, project_dir)}")

    all_passed = True

    # Check if reports already exist (prior successful build)
    util_file = os.path.join(project_dir, "build", "synth", "utilization.rpt")
    timing_file = os.path.join(project_dir, "build", "synth", "timing.rpt")
    reports_exist = os.path.isfile(util_file) and os.path.isfile(timing_file)

    if reports_exist:
        # Validate existing reports before skipping Vivado
        timing = parse_timing_report(timing_file)
        if timing:
            print(f"\n  Reports found from prior build -- skipping Vivado run")
        else:
            reports_exist = False

    if not reports_exist:
        # Step 1: Create block design if Vivado project doesn't exist
        xpr_path = os.path.join(project_dir, "build", "vivado_project", "system.xpr")
        if not os.path.isfile(xpr_path):
            print(f"\n  Vivado project not found -- running create_bd.tcl first")
            if not run_vivado(settings_path, create_bd, project_dir,
                              "block design creation", timeout=600):
                print_separator()
                print(f"  RESULT: {fail_str()} -- block design creation failed")
                print_separator()
                return 1
        else:
            print(f"\n  Vivado project exists: {os.path.relpath(xpr_path, project_dir)}")

        # Step 2: Run build_bitstream.tcl (synth + impl + bitstream)
        if not run_vivado(settings_path, build_bit, project_dir,
                          "synthesis + implementation + bitstream", timeout=1800):
            all_passed = False

    # Parse utilization report
    if os.path.isfile(util_file):
        util_rows = parse_utilization_report(util_file)
        print(f"\n  Utilization:")
        print_utilization(util_rows)
    else:
        print(f"\n  WARNING: utilization.rpt not found")

    # Parse timing report
    if os.path.isfile(timing_file):
        timing = parse_timing_report(timing_file)
        print(f"\n  Timing:")
        print_timing(timing)

        if any(not t.met for t in timing):
            all_passed = False
            print(f"\n  WARNING: Timing violations detected!")
    else:
        print(f"\n  WARNING: timing.rpt not found")

    # Step 5: Check XSA (informational)
    xsa_file = os.path.join(project_dir, "build", "synth", "system_wrapper.xsa")
    if os.path.isfile(xsa_file):
        print(f"\n  XSA: {os.path.relpath(xsa_file, project_dir)} (present)")
    else:
        print(f"\n  XSA: not found (informational -- may not be needed)")

    # Summary
    print()
    print_separator()
    if all_passed:
        print(f"  RESULT: {pass_str()} -- system synthesis complete, timing met")
    else:
        print(f"  RESULT: {fail_str()} -- synthesis issues found")
    print_separator()

    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
