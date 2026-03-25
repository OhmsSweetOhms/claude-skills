#!/usr/bin/env python3
"""
Stage 14: HIL Vivado Project -- Generate block design, wrapper, hil_top, constraints.

Reads hil.json, expands TCL templates, runs Vivado in batch mode to create the
HIL project. The project includes: PS7 block design, AXI interconnect, DUT
module reference, auto-generated hil_top.vhd wrapper, and XDC constraints.

Calls hil_prep.py to auto-generate missing artifacts (hil.json,
ila_trigger_plan.json, hil_test_main.c) before project creation.

Usage:
    python scripts/hil/hil_project.py --project-dir . --top usart_frame_ctrl

Exit codes:
    0  Project created successfully
    1  Error (missing files, Vivado failure, etc.)
"""

import argparse
import glob
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hil_lib import (
    load_hil_json, hil_build_dir, tcl_dir, presets_dir, xdc_dir,
    expand_template, resolve_sources,
)
from hil_prep import maybe_generate_artifacts
from validate_trigger_plan import validate_trigger_plan

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from socks_lib import (
    find_vivado_settings, print_header, print_separator,
    pass_str, fail_str, yellow, bold,
)
from project_config import get_scope


def build_externalize_tcl(hil_config):
    """Generate TCL commands to externalize DUT ports from hil.json wiring."""
    lines = []
    wiring = hil_config.get("wiring", {})

    # Loopback ports
    for pair in wiring.get("loopback", []):
        for port in pair:
            lines.append(f'make_bd_pins_external [get_bd_pins dut/{port}]')

    # Monitor ports (by prefix)
    monitor = wiring.get("monitor", {})
    prefixes = monitor.get("prefixes", [])
    ports = monitor.get("ports", [])
    for port in ports:
        lines.append(f'make_bd_pins_external [get_bd_pins dut/{port}]')
    # Note: prefix-based externalization is handled by listing explicit ports
    # in hil.json. The prefixes are used by gen_hil_top.tcl for MARK_DEBUG.

    return "\n".join(lines)


def build_sources_tcl(project_dir, hil_config):
    """Generate TCL add_files command for DUT sources, with VHDL 2008 property."""
    dut = hil_config["dut"]
    sources = dut.get("sources", [])
    resolved = resolve_sources(project_dir, sources)
    if not resolved:
        return ""
    files_tcl = " \\\n    ".join(f'"{s}"' for s in resolved)
    tcl = f'add_files -norecurse [list \\\n    {files_tcl}\n]'
    # Set VHDL 2008 file type on non-top .vhd files (Vivado module reference
    # does not allow VHDL 2008 as the top file, so only set it on sub-entities)
    top_entity = hil_config["dut"]["entity"]
    vhd_non_top = [s for s in resolved
                   if s.endswith(".vhd") and
                   os.path.splitext(os.path.basename(s))[0] != top_entity]
    if vhd_non_top:
        vhd_tcl = " \\\n    ".join(f'"{s}"' for s in vhd_non_top)
        tcl += f'\nset_property file_type {{VHDL 2008}} [get_files [list \\\n    {vhd_tcl}\n]]'
    return tcl


def write_sources_tcl(build_dir, project_dir, hil_config):
    """Write add_sources.tcl to build_dir and return a 'source' command string."""
    tcl_content = build_sources_tcl(project_dir, hil_config)
    path = os.path.join(build_dir, "add_sources.tcl")
    with open(path, "w") as f:
        f.write(tcl_content + "\n")
    return path


def build_import_tcl(hil_config):
    """Generate TCL importsources commands for firmware build."""
    lines = []
    fw = hil_config.get("firmware", {})
    # Test source directory
    test_src = fw.get("test_src")
    if test_src:
        src_dir = os.path.dirname(test_src)
        lines.append(f'importsources -name hil_app -path "{src_dir}"')
    # Driver sources
    for drv in fw.get("driver_sources", []):
        drv_dir = os.path.dirname(drv)
        lines.append(f'importsources -name hil_app -path "{drv_dir}"')
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Stage 14: HIL Vivado Project")
    parser.add_argument("--project-dir", required=True, help="Project root")
    parser.add_argument("--top", required=True,
                        help="Top-level VHDL entity name")
    parser.add_argument("--part", default="xc7z020clg484-1",
                        help="FPGA part number")
    parser.add_argument("--settings", default=None,
                        help="Path to Vivado settings64.sh")
    args = parser.parse_args()

    project_dir = os.path.abspath(args.project_dir)
    print_header("Stage 14: HIL Vivado Project")

    # VCD is a hard requirement for module/block scope; optional for system scope
    project_scope = get_scope(project_dir)
    vcd_files = glob.glob(os.path.join(project_dir, "build", "sim", "*.vcd"))
    if not vcd_files and project_scope != "system":
        print(f"\n  ERROR: VCD not found at build/sim/*.vcd. "
              f"Run Stage 7 to generate a VCD and fix any simulation errors.")
        return 1

    # Run hil_prep to generate missing artifacts
    if not maybe_generate_artifacts(project_dir, args.top, args.part):
        return 1

    # Load hil.json (hard-fail if missing after prep)
    hil_config = load_hil_json(project_dir)
    if hil_config is None:
        print(f"\n  ERROR: hil.json not found after prep. "
              f"Create it manually or run test discovery first.")
        return 1

    dut = hil_config["dut"]
    board = hil_config["board"]
    axi = hil_config["axi"]

    print(f"\n  Project:  {project_dir}")
    print(f"  DUT:      {dut['entity']}")
    print(f"  Part:     {board['part']}")
    print(f"  AXI base: {axi['base_address']}")
    print(f"  VCD found: ILA debug always enabled")

    # Prepare build directory
    build_dir = hil_build_dir(project_dir)
    os.makedirs(build_dir, exist_ok=True)

    # Resolve preset path
    preset_name = board.get("preset", "microzed_ps7_preset.tcl")
    if os.path.isabs(preset_name):
        preset_path = preset_name
    elif os.path.isfile(os.path.join(project_dir, preset_name)):
        preset_path = os.path.abspath(os.path.join(project_dir, preset_name))
    else:
        preset_path = os.path.join(presets_dir(), os.path.basename(preset_name))

    if not os.path.isfile(preset_path):
        print(f"\n  ERROR: Board preset not found: {preset_path}")
        return 1

    # Generate block_design.tcl from template
    fclk = str(axi.get("fclk_mhz", 100))
    bd_tcl = expand_template(
        os.path.join(tcl_dir(), "block_design.template.tcl"),
        os.path.join(build_dir, "block_design.tcl"),
        {
            "{{PRESET_TCL}}": preset_path,
            "{{FCLK_MHZ}}": fclk,
            "{{DUT_ENTITY}}": dut["entity"],
            "{{EXTERNALIZE_TCL}}": build_externalize_tcl(hil_config),
            "{{AXI_RANGE}}": axi.get("range", "4K"),
            "{{AXI_BASE_ADDRESS}}": axi["base_address"],
        },
    )

    # Extract loopback and monitor wiring from hil.json
    wiring = hil_config.get("wiring", {})
    loopback = wiring.get("loopback", [])
    if not loopback or len(loopback[0]) < 2:
        print(f"\n  ERROR: hil.json wiring.loopback must have at least one [out, in] pair")
        return 1
    loopback_out = loopback[0][0]
    loopback_in = loopback[0][1]
    # Build extra loopback pairs (index 1+) and tie_low as space-separated lists
    extra_lb_pairs = " ".join(f"{p[0]}:{p[1]}" for p in loopback[1:])
    tie_low_ports = " ".join(wiring.get("tie_low", []))
    monitor = wiring.get("monitor", {})
    monitor_prefixes = " ".join(monitor.get("prefixes", []))

    # Generate create_project.tcl from template
    project_name = f"hil_{dut['entity']}"
    hil_top_path = os.path.join(build_dir, "hil_top.vhd")

    cp_tcl = expand_template(
        os.path.join(tcl_dir(), "create_project.template.tcl"),
        os.path.join(build_dir, "create_project.tcl"),
        {
            "{{PROJECT_NAME}}": project_name,
            "{{BUILD_DIR}}": build_dir,
            "{{PART}}": board["part"],
            "{{SOURCES_TCL}}": write_sources_tcl(build_dir, project_dir, hil_config),
            "{{GEN_HIL_TOP_TCL}}": os.path.join(tcl_dir(), "gen_hil_top.tcl"),
            "{{BLOCK_DESIGN_TCL}}": bd_tcl,
            "{{BASE_XDC}}": os.path.join(xdc_dir(), "microzed.xdc"),
            "{{DEBUG_XDC}}": os.path.join(xdc_dir(), "insert_debug.xdc"),
            "{{HIL_TOP_PATH}}": hil_top_path,
            "{{LOOPBACK_OUT}}": loopback_out,
            "{{LOOPBACK_IN}}": loopback_in,
            "{{EXTRA_LB_PAIRS}}": extra_lb_pairs,
            "{{TIE_LOW_PORTS}}": tie_low_ports,
            "{{MONITOR_PREFIXES}}": monitor_prefixes,
        },
    )

    # Find Vivado
    settings = args.settings or find_vivado_settings()
    if settings is None:
        print(f"\n  ERROR: Vivado settings64.sh not found")
        return 1

    # Run Vivado batch -- always enable debug (VCD is guaranteed)
    cmd = (f'source "{settings}" && '
           f'vivado -mode batch -nojournal -nolog '
           f'-source "{cp_tcl}" -tclargs --debug')

    print(f"\n  Running Vivado project creation...")
    vivado_log = os.path.join(build_dir, "stage14_vivado.log")
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
        print(f"\n  {fail_str()}: Vivado project creation failed (rc={result.returncode})")
        print(f"  Log: {vivado_log}")
        # Show last 20 lines for quick diagnosis
        lines = result.stdout.strip().splitlines()
        if len(lines) > 20:
            print(f"\n  --- last 20 lines ---")
            for ln in lines[-20:]:
                print(f"  {ln}")
        return 1

    # Verify outputs
    xpr_files = glob.glob(os.path.join(build_dir, "vivado_project", "*.xpr"))
    if not xpr_files:
        print(f"\n  {fail_str()}: No .xpr file generated")
        return 1

    if not os.path.isfile(hil_top_path):
        print(f"\n  {fail_str()}: hil_top.vhd not generated")
        return 1

    # Validate trigger plan against MARK_DEBUG signals in hil_top.vhd
    trigger_plan_path = os.path.join(build_dir, "ila_trigger_plan.json")
    if os.path.isfile(trigger_plan_path):
        if not validate_trigger_plan(hil_top_path, trigger_plan_path):
            return 1

    print(f"\n  {pass_str()}: HIL Vivado project created")
    print(f"    Project: {xpr_files[0]}")
    print(f"    Top:     {hil_top_path}")
    print(f"    Debug:   True")
    print_separator()
    return 0


if __name__ == "__main__":
    sys.exit(main())
