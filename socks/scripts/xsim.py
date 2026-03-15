#!/usr/bin/env python3
"""
Stage 7: Xsim Build & Simulate -- Compile VHDL + SV, elaborate, and run.

Handles settings64.sh sourcing, compile order, elaboration, simulation,
and optional VCD/Tcl batch mode. Replaces all manual bash -c calls.

Usage:
    # Compile + simulate (auto-discover files)
    python scripts/xsim.py --project-dir . --top module_tb

    # Compile only
    python scripts/xsim.py --project-dir . --top module_tb --compile-only

    # Simulate only (already compiled)
    python scripts/xsim.py --project-dir . --top module_tb --sim-only

    # With VCD generation
    python scripts/xsim.py --project-dir . --top module_tb --vcd

    # With custom Tcl batch file
    python scripts/xsim.py --project-dir . --top module_tb --tcl build/sim/_run_vcd.tcl

    # Explicit file lists
    python scripts/xsim.py --top module_tb \\
        --vhdl src/a.vhd src/b.vhd --sv tb/module_tb.sv

    # Clean artifacts first
    python scripts/xsim.py --project-dir . --clean

Exit code 0 if all steps succeed and simulation reports no FAIL, 1 otherwise.
"""

import argparse
import glob
import json
import os
import re
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from socks_lib import (
    find_vivado_settings, print_header, print_separator,
    pass_str, fail_str, green, red, yellow,
)


def find_vhdl_files(project_dir):
    """Find VHDL files in src/ directory."""
    src_dir = os.path.join(project_dir, "src")
    if os.path.isdir(src_dir):
        return sorted(glob.glob(os.path.join(src_dir, "*.vhd")) +
                      glob.glob(os.path.join(src_dir, "*.vhdl")))
    return sorted(glob.glob(os.path.join(project_dir, "*.vhd")) +
                  glob.glob(os.path.join(project_dir, "*.vhdl")))


def find_sv_files(project_dir):
    """Find SystemVerilog testbench files in tb/ directory."""
    tb_dir = os.path.join(project_dir, "tb")
    if os.path.isdir(tb_dir):
        return sorted(glob.glob(os.path.join(tb_dir, "*.sv")))
    return sorted(glob.glob(os.path.join(project_dir, "*.sv")))


def find_dpi_c_files(project_dir):
    """Find DPI-C source files in tb/ directory."""
    tb_dir = os.path.join(project_dir, "tb")
    if os.path.isdir(tb_dir):
        return sorted(glob.glob(os.path.join(tb_dir, "*.c")))
    return []


def run_tool(settings_path, tool_cmd, work_dir, label, timeout=600):
    """Run an Xsim tool with settings64.sh sourced. Returns (success, stdout+stderr)."""
    full_cmd = f'source "{settings_path}" && cd "{work_dir}" && {tool_cmd}'

    try:
        result = subprocess.run(
            ["bash", "-c", full_cmd],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as e:
        print(f"  [{fail_str()}] {label} (timeout after {timeout}s)")
        output = (e.stdout or "") + (e.stderr or "")
        if isinstance(output, bytes):
            output = output.decode(errors="replace")
        for line in output.splitlines()[-10:]:
            print(f"    ! {line}")
        return False, output

    output = result.stdout + result.stderr

    if result.returncode != 0:
        print(f"  [{fail_str()}] {label}")
        for line in output.splitlines()[-20:]:
            print(f"    ! {line}")
        return False, output

    # Check for ERROR in output (xvhdl/xvlog report errors but sometimes exit 0)
    if re.search(r'\bERROR\b', output):
        print(f"  [{fail_str()}] {label}")
        for line in output.splitlines():
            if "ERROR" in line:
                print(f"    ! {line}")
        return False, output

    print(f"  [{pass_str()}] {label}")
    return True, output


def generate_run_tcl(work_dir, sim_name, run_time="all"):
    """Generate a simple run Tcl script."""
    tcl_path = os.path.join(work_dir, "_run.tcl")
    with open(tcl_path, "w") as f:
        f.write(f"run -{run_time}\nexit\n")
    return tcl_path


def generate_vcd_tcl(work_dir, sim_name, vcd_signals=None):
    """Generate a Tcl script for VCD capture."""
    if "_sim" in sim_name:
        vcd_name = sim_name.replace("_sim", "_verify.vcd")
    else:
        vcd_name = sim_name + "_verify.vcd"
    tcl_path = os.path.join(work_dir, "_run_vcd.tcl")
    with open(tcl_path, "w") as f:
        f.write(f"open_vcd {vcd_name}\n")
        if vcd_signals:
            for sig in vcd_signals:
                # Signal map uses dot notation (a.b.c) for vcd_verify;
                # xsim log_vcd needs slash notation (/a/b/c)
                tcl_path_sig = "/" + sig.replace(".", "/")
                f.write(f"log_vcd {tcl_path_sig}\n")
        else:
            f.write("log_vcd *\n")
        f.write("run -all\nclose_vcd\nexit\n")
    return tcl_path, vcd_name


def clean_artifacts(work_dir):
    """Remove Xsim build artifacts."""
    to_remove = [
        os.path.join(work_dir, "xsim.dir"),
        os.path.join(work_dir, ".Xil"),
        os.path.join(work_dir, "webtalk"),
    ]
    patterns = ["*.pb", "*.wdb", "*.vcd", "*.jou", "*.log",
                "_run.tcl", "_run_vcd.tcl"]

    removed = 0
    for d in to_remove:
        if os.path.isdir(d):
            shutil.rmtree(d)
            removed += 1

    for pat in patterns:
        for f in glob.glob(os.path.join(work_dir, pat)):
            os.remove(f)
            removed += 1

    return removed


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Stage 6: Xsim Build & Simulate",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--project-dir", type=str, default=".",
                        help="Project root directory (default: cwd)")
    parser.add_argument("--top", type=str, default=None,
                        help="Top-level testbench module name")
    parser.add_argument("--sim-name", type=str, default=None,
                        help="Simulation snapshot name (default: <top>_sim)")
    parser.add_argument("--vhdl", type=str, nargs="*", default=None,
                        help="VHDL source files (auto-discovered if omitted)")
    parser.add_argument("--sv", type=str, nargs="*", default=None,
                        help="SV testbench files (auto-discovered if omitted)")
    parser.add_argument("--work-dir", type=str, default=None,
                        help="Working directory for Xsim (default: project-dir/sim)")
    parser.add_argument("--settings", type=str, default=None,
                        help="Path to Vivado settings64.sh")
    parser.add_argument("--compile-only", action="store_true",
                        help="Compile and elaborate only, do not simulate")
    parser.add_argument("--sim-only", action="store_true",
                        help="Simulate only (assumes already compiled)")
    parser.add_argument("--vcd", action="store_true",
                        help="Generate VCD output using auto-generated Tcl")
    parser.add_argument("--tcl", type=str, default=None,
                        help="Custom Tcl batch file for simulation")
    parser.add_argument("--clean", action="store_true",
                        help="Clean Xsim artifacts and exit")
    parser.add_argument("--signal-map", type=str, default=None,
                        help="JSON signal map for selective VCD logging (used with --vcd)")
    parser.add_argument("--timeout", type=int, default=600,
                        help="Simulation timeout in seconds (default: 600)")
    args = parser.parse_args()

    project_dir = os.path.abspath(args.project_dir)

    print_header("SOCKS Stage 6 -- Xsim Build & Simulate")

    # Work directory
    work_dir = args.work_dir or os.path.join(project_dir, "build", "sim")
    os.makedirs(work_dir, exist_ok=True)

    # Clean mode
    if args.clean:
        n = clean_artifacts(work_dir)
        print(f"\n  Cleaned {n} artifacts from {work_dir}")
        print_separator()
        return 0

    if not args.top:
        # Try to guess from SV files
        sv_files = args.sv or find_sv_files(project_dir)
        if sv_files:
            # Use first SV file's module name (filename without extension)
            args.top = os.path.splitext(os.path.basename(sv_files[0]))[0]
            print(f"\n  Auto-detected top: {args.top}")
        else:
            print(f"\n  ERROR: --top required (no SV files found to auto-detect)")
            print_separator()
            return 1

    sim_name = args.sim_name or f"{args.top}_sim"

    # Discover Vivado
    settings_path = args.settings or find_vivado_settings()
    if settings_path is None:
        print("\n  ERROR: Could not find Vivado settings64.sh")
        print("  Use --settings or run env.py first")
        print_separator()
        return 1

    print(f"\n  Project: {project_dir}")
    print(f"  Work dir: {work_dir}")
    print(f"  Top module: {args.top}")
    print(f"  Sim snapshot: {sim_name}")
    print(f"  Settings: {settings_path}")

    all_passed = True

    # --- Pre-flight: scan TB sources for $dumpfile/$dumpvars (rule X8) ---
    if not args.sim_only:
        sv_check_files = args.sv or find_sv_files(project_dir)
        for sv_file in sv_check_files:
            with open(sv_file, "r") as f:
                for lineno, line in enumerate(f, 1):
                    # Skip comments
                    stripped = line.split("//")[0]
                    if re.search(r'\$dump(file|vars)\b', stripped):
                        print(f"\n  ERROR: SV TB contains $dumpfile/$dumpvars.")
                        print(f"    {os.path.basename(sv_file)}:{lineno}: {line.strip()}")
                        print(f"    Remove them — VCD is managed by xsim.py via "
                              f"vcd_signal_map.json. See references/xsim.md rule X8.")
                        print_separator()
                        return 1

    # --- Compile phase ---
    if not args.sim_only:
        vhdl_files = args.vhdl or find_vhdl_files(project_dir)
        sv_files = args.sv or find_sv_files(project_dir)

        # Order VHDL files: compile top-level entity last so dependencies
        # are already in the library. Match --top against filenames.
        if args.top and vhdl_files:
            top_base = args.top.lower()
            top_files = [f for f in vhdl_files
                         if top_base in os.path.basename(f).lower()]
            other_files = [f for f in vhdl_files
                           if top_base not in os.path.basename(f).lower()]
            vhdl_files = other_files + top_files

        print(f"\n  VHDL files ({len(vhdl_files)}):")
        for f in vhdl_files:
            print(f"    {os.path.basename(f)}")
        print(f"  SV files ({len(sv_files)}):")
        for f in sv_files:
            print(f"    {os.path.basename(f)}")

        if not vhdl_files and not sv_files:
            print(f"\n  ERROR: No source files found")
            print_separator()
            return 1

        print(f"\n  Compiling...")

        # Two-pass VHDL compilation to handle dependency order.
        # Alphabetical sorting doesn't guarantee that dependencies compile
        # before the files that instantiate them (e.g. sdlc_axi.vhd sorts
        # before sdlc_v1.vhd). Pass 1 populates the library silently;
        # pass 2 resolves all forward references and reports errors.
        if len(vhdl_files) > 1:
            # Pass 1: silent -- populate library, ignore failures
            for vhd in vhdl_files:
                abs_path = os.path.abspath(vhd)
                full_cmd = (f'source "{settings_path}" && cd "{work_dir}" && '
                            f'xvhdl --2008 "{abs_path}"')
                subprocess.run(["bash", "-c", full_cmd],
                               capture_output=True, text=True, timeout=300)

        # Pass 2 (or single pass if only one file): report results
        for vhd in vhdl_files:
            abs_path = os.path.abspath(vhd)
            fname = os.path.basename(vhd)
            ok, _ = run_tool(
                settings_path,
                f'xvhdl --2008 "{abs_path}"',
                work_dir,
                f"xvhdl {fname}",
            )
            if not ok:
                all_passed = False

        if not all_passed:
            print(f"\n  VHDL compilation failed -- stopping")
            print_separator()
            return 1

        # Compile SV files
        for sv in sv_files:
            abs_path = os.path.abspath(sv)
            fname = os.path.basename(sv)
            ok, _ = run_tool(
                settings_path,
                f'xvlog -sv -d SOCKS_VCD "{abs_path}"',
                work_dir,
                f"xvlog {fname}",
            )
            if not ok:
                all_passed = False

        if not all_passed:
            print(f"\n  SV compilation failed -- stopping")
            print_separator()
            return 1

        # Compile DPI-C files (if any)
        dpi_c_files = find_dpi_c_files(project_dir)
        has_dpi = len(dpi_c_files) > 0
        if has_dpi:
            print(f"\n  DPI-C files ({len(dpi_c_files)}):")
            for f in dpi_c_files:
                print(f"    {os.path.basename(f)}")
            abs_c_paths = " ".join(f'"{os.path.abspath(f)}"' for f in dpi_c_files)
            ok, _ = run_tool(
                settings_path,
                f"xsc {abs_c_paths}",
                work_dir,
                "xsc (DPI-C compile)",
            )
            if not ok:
                all_passed = False
                print(f"\n  DPI-C compilation failed -- stopping")
                print_separator()
                return 1

        # Elaborate
        print(f"\n  Elaborating...")
        elab_cmd = f"xelab -debug typical {args.top} -s {sim_name}"
        if has_dpi:
            elab_cmd += " -sv_lib dpi"
        ok, _ = run_tool(
            settings_path,
            elab_cmd,
            work_dir,
            f"xelab {args.top} -> {sim_name}",
        )
        if not ok:
            all_passed = False
            print(f"\n  Elaboration failed -- stopping")
            print_separator()
            return 1

    if args.compile_only:
        print()
        print_separator()
        if all_passed:
            print(f"  RESULT: {pass_str()} -- compile + elaborate succeeded")
        else:
            print(f"  RESULT: {fail_str()} -- compilation errors")
        print_separator()
        return 0 if all_passed else 1

    # --- Simulate phase ---
    print(f"\n  Simulating...")

    # Determine Tcl file
    if args.tcl:
        tcl_path = os.path.abspath(args.tcl)
        if not os.path.isfile(tcl_path):
            print(f"  ERROR: Tcl file not found: {tcl_path}")
            print_separator()
            return 1
        sim_cmd = f'xsim {sim_name} -tclbatch "{tcl_path}"'
    elif args.vcd:
        vcd_signals = None
        if args.signal_map:
            map_path = os.path.abspath(args.signal_map)
            if os.path.isfile(map_path):
                with open(map_path) as mf:
                    sig_map = json.load(mf)
                vcd_signals = list(sig_map.values())
                print(f"  Signal map: {map_path} ({len(vcd_signals)} signals)")
            else:
                print(f"  WARNING: Signal map not found: {map_path}, falling back to log_vcd *")
        tcl_path, vcd_name = generate_vcd_tcl(work_dir, sim_name, vcd_signals=vcd_signals)
        sim_cmd = f'xsim {sim_name} -tclbatch "{tcl_path}"'
        print(f"  VCD output: {vcd_name}")
    else:
        sim_cmd = f"xsim {sim_name} -R"

    ok, sim_output = run_tool(
        settings_path,
        sim_cmd,
        work_dir,
        f"xsim {sim_name}",
        timeout=args.timeout,
    )

    if not ok:
        all_passed = False

    # Check simulation output for PASS/FAIL
    # Filter out xsim Tcl echo lines (## command ...) to avoid false
    # positives from signal names like "fail_cnt" or "pass_cnt"
    check_lines = [l for l in sim_output.splitlines()
                   if not l.strip().startswith("##")]
    check_text = "\n".join(check_lines).upper()
    has_fail = ("FAIL" in check_text and "ALL PASS" not in check_text and
                "SIMULATION PASSED" not in check_text and "0 FAIL" not in check_text)
    has_pass = ("ALL PASS" in check_text or "ALL TESTS PASSED" in check_text or
                "TEST PASSED" in check_text or "SIMULATION PASSED" in check_text)

    if has_fail:
        all_passed = False
        print(f"\n  Simulation output contains FAIL:")
        for line in sim_output.splitlines():
            if "FAIL" in line.upper():
                print(f"    ! {line.strip()}")

    if has_pass:
        print(f"\n  Simulation self-check: {pass_str()}")
    elif not has_fail and ok:
        print(f"\n  Simulation completed (no explicit PASS/FAIL in output)")

    # Print key simulation output lines
    key_lines = [l for l in sim_output.splitlines()
                 if any(kw in l.upper() for kw in
                        ["PASS", "FAIL", "ERROR", "TEST", "DONE",
                         "VERIFIED", "MISMATCH", "TIMEOUT"])]
    if key_lines:
        print(f"\n  Key output lines:")
        for line in key_lines[:20]:
            print(f"    | {line.strip()}")

    print()
    print_separator()
    if all_passed:
        print(f"  RESULT: {pass_str()} -- build + simulate succeeded")
    else:
        print(f"  RESULT: {fail_str()} -- errors detected")
    print_separator()

    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
