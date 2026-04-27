#!/usr/bin/env python3
"""
Stage 15: HIL Implementation -- Synthesize, implement, generate bitstream and XSA.

Runs run_impl.tcl via Vivado batch mode. Parses timing, copies artifacts
(XSA, ps7_init.tcl, optionally .ltx) to build/hil/.

Usage:
    python scripts/hil/hil_impl.py --project-dir .

Exit codes:
    0  Implementation succeeded, timing met
    1  Error (missing project, Vivado failure, timing violated)
"""

import argparse
import glob
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hil_lib import load_hil_json, hil_build_dir, tcl_dir, board_family, boot_init_filename

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from socks_lib import (
    find_vivado_settings, print_header, print_separator,
    pass_str, fail_str,
)
from project_config import get_scope


def main() -> int:
    parser = argparse.ArgumentParser(description="Stage 15: HIL Implementation")
    parser.add_argument("--project-dir", required=True, help="Project root")
    parser.add_argument("--settings", default=None,
                        help="Path to Vivado settings64.sh")
    args = parser.parse_args()

    project_dir = os.path.abspath(args.project_dir)
    print_header("Stage 15: HIL Implementation")

    # Load hil.json for project name
    hil_config = load_hil_json(project_dir)
    if hil_config is None:
        print(f"\n  No hil.json -- skipping")
        return 0

    build_dir = hil_build_dir(project_dir)

    # System scope: bitstream already staged by Stage 14 from Stage 10 artifacts
    if get_scope(project_dir) == "system":
        bit_files = glob.glob(os.path.join(build_dir, "vivado_project", "*", "impl_1", "*.bit"))
        xsa_files = glob.glob(os.path.join(build_dir, "*.xsa"))
        if bit_files and xsa_files:
            print(f"\n  System scope: bitstream and XSA already staged by Stage 14")
            print(f"  Bitstream: {os.path.relpath(bit_files[0], project_dir)}")
            print(f"  XSA:       {os.path.relpath(xsa_files[0], project_dir)}")
            print(f"\n  {pass_str()} Skipping implementation (reusing Stage 10 artifacts)")
            return 0
        else:
            print(f"\n  System scope: expected staged artifacts not found in build/hil/")
            print(f"  Run Stage 14 first to stage artifacts from Stage 10.")
            return 1

    dut_entity = hil_config["dut"]["entity"]
    project_name = f"hil_{dut_entity}"

    # Check prerequisite: .xpr exists
    xpr_files = glob.glob(os.path.join(build_dir, "vivado_project", "*.xpr"))
    if not xpr_files:
        print(f"\n  ERROR: No Vivado project found in {build_dir}/vivado_project/")
        print(f"  Run Stage 14 first.")
        return 1

    print(f"\n  Project:  {project_dir}")
    print(f"  Build:    {build_dir}")

    # Find Vivado
    settings = args.settings or find_vivado_settings()
    if settings is None:
        print(f"\n  ERROR: Vivado settings64.sh not found")
        return 1

    # Run implementation
    run_impl_tcl = os.path.join(tcl_dir(), "run_impl.tcl")
    cmd = (f'source "{settings}" && '
           f'vivado -mode batch -nojournal -nolog '
           f'-source "{run_impl_tcl}" '
           f'-tclargs "{build_dir}" "{project_name}"')

    print(f"\n  Running synthesis + implementation...")
    vivado_log = os.path.join(build_dir, "stage15_vivado.log")
    with open(vivado_log, "w") as log_f:
        result = subprocess.run(
            ["bash", "-c", cmd],
            cwd=build_dir,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        log_f.write(result.stdout)
    # Always show Vivado output on terminal
    print(result.stdout)

    if result.returncode != 0:
        print(f"\n  {fail_str()}: Implementation failed (rc={result.returncode})")
        print(f"  Log: {vivado_log}")
        lines = result.stdout.strip().splitlines()
        if len(lines) > 20:
            print(f"\n  --- last 20 lines ---")
            for ln in lines[-20:]:
                print(f"  {ln}")
        return 1

    # Verify outputs
    xsa_path = os.path.join(build_dir, "system_wrapper.xsa")
    if not os.path.isfile(xsa_path):
        print(f"\n  {fail_str()}: XSA not generated")
        return 1

    # Check for bitstream
    bit_files = glob.glob(os.path.join(
        build_dir, "vivado_project", f"{project_name}.runs", "impl_1", "*.bit"))
    if not bit_files:
        print(f"\n  {fail_str()}: No bitstream generated")
        return 1

    print(f"\n  {pass_str()}: HIL implementation complete")
    print(f"    XSA:       {xsa_path}")
    print(f"    Bitstream: {bit_files[0]}")

    init_name = boot_init_filename(board_family(hil_config))
    boot_init = os.path.join(build_dir, init_name)
    if os.path.isfile(boot_init):
        print(f"    Boot init: {boot_init}")

    ltx_path = os.path.join(build_dir, "hil_top.ltx")
    if os.path.isfile(ltx_path):
        print(f"    LTX:       {ltx_path}")

    print_separator()
    return 0


if __name__ == "__main__":
    sys.exit(main())
