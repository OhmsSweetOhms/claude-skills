#!/usr/bin/env python3
"""
Stage 9: Vivado Synthesis -- Generate TCL scripts, invoke Vivado in batch
mode, and parse utilization/timing/DRC reports.

Usage:
    python scripts/synth.py --top my_module --part xc7z020clg484-1 \\
        --src-dir src/ --out-dir sim/
    python scripts/synth.py --top my_module --part xc7z020clg484-1 \\
        --src-dir src/ --out-dir sim/ --clock-period 10.0 --async-ports rxd

Exit code 0 if synthesis succeeds and timing is met, 1 otherwise.
"""

import argparse
import glob
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from socks_lib import (
    find_vivado_settings, parse_utilization_report, parse_timing_report,
    parse_drc_report, print_header, print_separator, pass_str, fail_str,
)


def find_vhdl_files(src_dir):
    """Find all .vhd files in src_dir."""
    patterns = [
        os.path.join(src_dir, "*.vhd"),
        os.path.join(src_dir, "*.vhdl"),
    ]
    files = []
    for pat in patterns:
        files.extend(glob.glob(pat))
    return sorted(files)


def generate_synth_check_tcl(out_dir, src_files, top, part):
    """Generate synth_check.tcl for utilization report.

    Uses relative paths so the TCL is portable and committable.
    Vivado is invoked with cwd=out_dir, so proj_dir=[pwd] works.
    Source files use paths relative to out_dir.
    """
    tcl_path = os.path.join(out_dir, "synth_check.tcl")
    lines = [
        'set proj_dir [pwd]',
        f'create_project -in_memory -part {part}',
    ]
    for f in src_files:
        rel = os.path.relpath(f, out_dir)
        fname = os.path.basename(f)
        lines.append(f'add_files [file join $proj_dir {rel}]')
        lines.append(f'set_property file_type {{VHDL 2008}} [get_files {fname}]')
    lines.extend([
        f'synth_design -top {top} -part {part}',
        f'report_utilization -file [file join $proj_dir {top}_utilization.txt]',
        f'report_timing_summary -file [file join $proj_dir {top}_timing.txt]',
        f'report_drc -file [file join $proj_dir {top}_drc.txt]',
    ])

    with open(tcl_path, "w") as f:
        f.write("\n".join(lines) + "\n")
    return tcl_path


def generate_synth_timing_tcl(out_dir, src_files, top, part,
                               clock_period, async_ports):
    """Generate synth_timing.tcl for constrained timing report.

    Uses relative paths so the TCL is portable and committable.
    """
    tcl_path = os.path.join(out_dir, "synth_timing.tcl")
    lines = [
        'set proj_dir [pwd]',
        f'create_project -in_memory -part {part}',
    ]
    for f in src_files:
        rel = os.path.relpath(f, out_dir)
        fname = os.path.basename(f)
        lines.append(f'add_files [file join $proj_dir {rel}]')
        lines.append(f'set_property file_type {{VHDL 2008}} [get_files {fname}]')
    lines.extend([
        f'synth_design -top {top} -part {part}',
        f'create_clock -period {clock_period} -name sys_clk [get_ports clk]',
    ])

    for port in async_ports:
        lines.append(f'set_false_path -from [get_ports {port}]')

    lines.extend([
        f'report_timing_summary -file [file join $proj_dir {top}_timing_constrained.txt]',
        f'report_timing -nworst 5 -file [file join $proj_dir {top}_timing_paths.txt]',
    ])

    with open(tcl_path, "w") as f:
        f.write("\n".join(lines) + "\n")
    return tcl_path


def run_vivado(settings_path, tcl_path, out_dir, label):
    """Run Vivado in batch mode with cwd=out_dir for relative paths."""
    tcl_basename = os.path.basename(tcl_path)
    log_name = os.path.splitext(tcl_basename)[0]
    cmd = (f"source {settings_path} && "
           f"vivado -mode batch -source {tcl_basename} "
           f"-log {log_name}.log "
           f"-journal {log_name}.jou")

    print(f"\n  Running {label}...")
    print(f"    TCL: {tcl_path}")

    result = subprocess.run(
        ["bash", "-c", cmd],
        capture_output=True,
        text=True,
        timeout=600,
        cwd=out_dir,
    )

    if result.returncode != 0:
        print(f"    ERROR: Vivado exited with code {result.returncode}")
        if result.stderr:
            for line in result.stderr.splitlines()[-10:]:
                print(f"    ! {line}")
        return False

    print(f"    Vivado completed successfully")
    return True


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


def main() -> int:
    parser = argparse.ArgumentParser(description="Stage 9: Vivado Synthesis")
    parser.add_argument("--top", required=True, help="Top-level entity name")
    parser.add_argument("--part", default="xc7z020clg484-1",
                        help="FPGA part (default: xc7z020clg484-1)")
    parser.add_argument("--src-dir", required=True,
                        help="Directory containing VHDL source files")
    parser.add_argument("--out-dir", required=True,
                        help="Output directory for reports and TCL")
    parser.add_argument("--clock-period", type=float, default=10.0,
                        help="Clock period in ns (default: 10.0 = 100 MHz)")
    parser.add_argument("--async-ports", type=str, nargs="*", default=[],
                        help="Async input port names for false paths")
    parser.add_argument("--settings", type=str, default=None,
                        help="Path to Vivado settings64.sh")
    parser.add_argument("--skip-timing", action="store_true",
                        help="Skip constrained timing run")
    args = parser.parse_args()

    print_header("SOCKS Stage 9 -- Vivado Synthesis")

    # Discover Vivado
    settings_path = args.settings or find_vivado_settings()
    if settings_path is None:
        print("\n  ERROR: Could not find Vivado settings64.sh")
        print("  Use --settings or run env.py first")
        print_separator()
        return 1

    # Find VHDL files
    src_files = find_vhdl_files(args.src_dir)
    if not src_files:
        print(f"\n  ERROR: No VHDL files found in {args.src_dir}")
        print_separator()
        return 1

    print(f"\n  Top entity: {args.top}")
    print(f"  Part: {args.part}")
    print(f"  Clock period: {args.clock_period} ns ({1000/args.clock_period:.1f} MHz)")
    print(f"  Source files:")
    for f in src_files:
        print(f"    {os.path.basename(f)}")

    os.makedirs(args.out_dir, exist_ok=True)

    all_passed = True

    # Run utilization synthesis
    tcl1 = generate_synth_check_tcl(args.out_dir, src_files, args.top, args.part)
    if not run_vivado(settings_path, tcl1, args.out_dir, "utilization synthesis"):
        all_passed = False
    else:
        util_file = os.path.join(args.out_dir, f"{args.top}_utilization.txt")
        util_rows = parse_utilization_report(util_file)
        print(f"\n  Utilization:")
        print_utilization(util_rows)

        drc_file = os.path.join(args.out_dir, f"{args.top}_drc.txt")
        errors, warnings, critical = parse_drc_report(drc_file)
        print(f"\n  DRC: {errors} errors, {warnings} warnings")
        for msg in critical:
            print(f"    ! {msg}")

    # Run constrained timing
    if not args.skip_timing:
        tcl2 = generate_synth_timing_tcl(
            args.out_dir, src_files, args.top, args.part,
            args.clock_period, args.async_ports)
        if not run_vivado(settings_path, tcl2, args.out_dir, "constrained timing"):
            all_passed = False
        else:
            timing_file = os.path.join(
                args.out_dir, f"{args.top}_timing_constrained.txt")
            timing = parse_timing_report(timing_file)
            print(f"\n  Timing ({args.clock_period} ns constraint):")
            print_timing(timing)

            if any(not t.met for t in timing):
                all_passed = False
                print(f"\n  WARNING: Timing violations detected!")

    print()
    print_separator()
    if all_passed:
        print(f"  RESULT: {pass_str()} -- synthesis complete, timing met")
    else:
        print(f"  RESULT: {fail_str()} -- synthesis issues found")
    print_separator()

    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
