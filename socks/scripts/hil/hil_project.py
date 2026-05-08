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
import shutil
import subprocess
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hil_lib import (
    load_hil_json, hil_build_dir, tcl_dir, presets_dir, xdc_dir,
    expand_template, resolve_sources, board_family, boot_init_filename,
)
from hil_prep import maybe_generate_artifacts
from adi_profile_apply import apply_active_profile
from validate_trigger_plan import validate_trigger_plan

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from socks_lib import (
    find_vivado_settings, print_header, print_separator, print_result,
    pass_str, fail_str, yellow, bold,
)
from project_config import get_scope, load_project_config


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


def _read_make_project_name(makefile_path):
    with open(makefile_path, "r") as f:
        for line in f:
            stripped = line.strip()
            if stripped.startswith("PROJECT_NAME") and ":=" in stripped:
                return stripped.split(":=", 1)[1].strip()
    return os.path.basename(os.path.dirname(makefile_path))


def _resolve_project_path(project_dir, path_value):
    if os.path.isabs(path_value):
        return os.path.abspath(path_value)
    return os.path.abspath(os.path.join(project_dir, path_value))


def _extract_boot_init_from_xsa(xsa_path, build_dir, family):
    filename = boot_init_filename(family)
    dst = os.path.join(build_dir, filename)
    try:
        with zipfile.ZipFile(xsa_path) as zf:
            matches = [name for name in zf.namelist()
                       if name.endswith("/" + filename) or os.path.basename(name) == filename]
            if not matches:
                return None
            with zf.open(matches[0]) as src, open(dst, "wb") as out:
                shutil.copyfileobj(src, out)
        return dst
    except zipfile.BadZipFile:
        return None


def run_adi_make_stage14(project_dir, build_dir, build_cfg, settings_path):
    """Run ADI Make and stage artifacts for later HIL stages."""
    required = ["adi_root", "project_dir"]
    missing = [key for key in required if not build_cfg.get(key)]
    if missing:
        print(f"\n  ERROR: build.flow=adi_make missing: {', '.join(missing)}")
        return 1

    adi_root = _resolve_project_path(project_dir, build_cfg["adi_root"])
    adi_project_rel = build_cfg["project_dir"]
    adi_project_dir = os.path.abspath(os.path.join(adi_root, adi_project_rel))
    makefile = os.path.join(adi_project_dir, "Makefile")
    if not os.path.isfile(makefile):
        print(f"\n  ERROR: ADI project Makefile not found: {makefile}")
        return 1

    settings = settings_path or find_vivado_settings()
    if settings is None:
        print(f"\n  ERROR: Vivado settings64.sh not found")
        return 1

    os.makedirs(build_dir, exist_ok=True)
    log_path = os.path.join(build_dir, "stage14_adi_make.log")
    cmd = f'source "{settings}" && make -C "{adi_project_dir}"'

    print(f"\n  ADI Make flow")
    print(f"  ADI root: {adi_root}")
    print(f"  Project:  {adi_project_dir}")
    print(f"  Log:      {log_path}")

    with open(log_path, "w") as log_f:
        result = subprocess.run(
            ["bash", "-c", cmd],
            cwd=project_dir,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        log_f.write(result.stdout)
    print(result.stdout)

    if result.returncode != 0:
        print(f"\n  {fail_str()}: ADI make failed (rc={result.returncode})")
        print(f"  Log: {log_path}")
        return 1

    project_name = _read_make_project_name(makefile)
    xsa_src = os.path.join(adi_project_dir, f"{project_name}.sdk", "system_top.xsa")
    if not os.path.isfile(xsa_src):
        print(f"\n  {fail_str()}: ADI XSA not found: {xsa_src}")
        return 1

    xsa_dst = os.path.join(build_dir, "system_wrapper.xsa")
    shutil.copy2(xsa_src, xsa_dst)
    print(f"  XSA:      {os.path.relpath(xsa_dst, project_dir)}")

    bit_files = glob.glob(os.path.join(adi_project_dir, f"{project_name}.runs", "impl_1", "*.bit"))
    if not bit_files:
        print(f"\n  {fail_str()}: ADI bitstream not found under {project_name}.runs/impl_1")
        return 1

    impl_dir = os.path.join(build_dir, "vivado_project", f"{project_name}.runs", "impl_1")
    os.makedirs(impl_dir, exist_ok=True)
    bit_dst = os.path.join(impl_dir, os.path.basename(bit_files[0]))
    shutil.copy2(bit_files[0], bit_dst)
    print(f"  Bitstream: {os.path.relpath(bit_dst, project_dir)}")

    cfg_for_family = {"board": build_cfg.get("board", {})}
    family = build_cfg.get("family") or board_family(cfg_for_family)
    init_dst = _extract_boot_init_from_xsa(xsa_dst, build_dir, family)
    if init_dst:
        print(f"  Boot init: {os.path.relpath(init_dst, project_dir)}")
    else:
        print(f"  WARNING: {boot_init_filename(family)} not found in XSA; Stage 16 may generate it")

    print(f"\n  {pass_str()}: ADI Make artifacts staged for HIL")
    print_result("ADI Make Stage 14", True, "artifacts staged")
    return 0


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

    # Verify IP packaging (Stage 21) has run — component.xml must exist
    ip_dir = os.path.join(project_dir, "build", "ip")
    component_xml = os.path.join(ip_dir, "component.xml")
    if project_scope != "system" and not os.path.isfile(component_xml):
        print(f"\n  ERROR: build/ip/component.xml not found. "
              f"Stage 21 (IP Packaging) must run before Stage 14.")
        print(f"  Run: python scripts/socks.py --project-dir {project_dir} --stages 4,21")
        return 1

    # Compute VLNV from socks.json ip section
    socks_cfg = load_project_config(project_dir)
    ip_cfg = socks_cfg.get("ip", {}) if socks_cfg else {}
    ip_vlnv = ""
    if ip_cfg and project_scope != "system":
        ip_vlnv = (f"{ip_cfg.get('vendor', 'socks')}:{ip_cfg.get('library', 'socks')}:"
                   f"{args.top}:{ip_cfg.get('version', '1.0')}")

    build_cfg = socks_cfg.get("build", {}) if socks_cfg else {}
    if project_scope == "system" and build_cfg.get("flow", "vivado_native") == "adi_make":
        if socks_cfg.get("adi"):
            try:
                print(f"\n  Applying ADI active profile before Stage 14...")
                apply_active_profile(project_dir)
            except Exception as e:
                print(f"\n  ERROR: ADI active profile apply failed: {e}")
                return 1
        build_cfg = dict(build_cfg)
        build_cfg.setdefault("board", socks_cfg.get("board", {}) if socks_cfg else {})
        build_dir = hil_build_dir(project_dir)
        return run_adi_make_stage14(project_dir, build_dir, build_cfg, args.settings)

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

    # Prepare build directory
    build_dir = hil_build_dir(project_dir)
    os.makedirs(build_dir, exist_ok=True)

    # ---- System scope: reuse Stage 10 artifacts instead of creating new BD ----
    if project_scope == "system":
        print(f"\n  System scope: reusing Stage 10 bitstream and XSA")

        # Find XSA from Stage 10
        xsa_files = glob.glob(os.path.join(project_dir, "build", "synth", "*.xsa"))
        if not xsa_files:
            print(f"\n  ERROR: No XSA found in build/synth/. Run Stage 10 first.")
            return 1
        xsa_src = xsa_files[0]

        # Find bitstream from Stage 10
        bit_files = glob.glob(os.path.join(
            project_dir, "build", "vivado_project", "*.runs", "impl_1", "*.bit"))
        if not bit_files:
            print(f"\n  ERROR: No bitstream found in build/vivado_project/. "
                  f"Run Stage 10 first.")
            return 1
        bit_src = bit_files[0]

        # Copy XSA to build/hil/
        xsa_dst = os.path.join(build_dir, os.path.basename(xsa_src))
        shutil.copy2(xsa_src, xsa_dst)
        print(f"  XSA:      {os.path.relpath(xsa_dst, project_dir)}")

        # Create expected directory structure for Stage 17
        # Stage 17 globs: build/hil/vivado_project/*/impl_1/*.bit
        project_name = f"hil_{dut['entity']}"
        impl_dir = os.path.join(build_dir, "vivado_project",
                                f"{project_name}.runs", "impl_1")
        os.makedirs(impl_dir, exist_ok=True)
        bit_dst = os.path.join(impl_dir, os.path.basename(bit_src))
        shutil.copy2(bit_src, bit_dst)
        print(f"  Bitstream: {os.path.relpath(bit_dst, project_dir)}")

        # Extract ps7_init.tcl from Vitis workspace if firmware was already built,
        # otherwise it will be generated during Stage 16
        ps7_dst = os.path.join(build_dir, "ps7_init.tcl")
        if not os.path.isfile(ps7_dst):
            # Look in Vitis workspace from a prior Stage 16 run
            ps7_candidates = glob.glob(os.path.join(
                build_dir, "vitis_ws", "hil_app", "_ide", "psinit", "ps7_init.tcl"))
            if ps7_candidates:
                shutil.copy2(ps7_candidates[0], ps7_dst)
                print(f"  ps7_init:  {os.path.relpath(ps7_dst, project_dir)}")

        print(f"\n  System scope Stage 14 complete -- artifacts staged for Stage 16-17")
        print_result("System scope Stage 14", True, "artifacts staged from Stage 10")
        return 0

    print(f"  VCD found: ILA debug always enabled")

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

    # Determine if JTAG-to-AXI debug master is needed
    debug_config = hil_config.get("debug", {})
    has_jtag_axi = bool(debug_config.get("jtag_axi_dump"))
    num_si = "2" if has_jtag_axi else "1"

    # Generate JTAG-AXI TCL block (empty string if not needed)
    # Topology: jtag_axi (AXI master) → interconnect S01 → M00 → DUT
    # This gives JTAG direct AXI access to PL registers even when CPU is dead
    if has_jtag_axi:
        jtag_axi_tcl = (
            "# Add JTAG-to-AXI master for CPU-fault register dump\n"
            "create_bd_cell -type ip -vlnv xilinx.com:ip:jtag_axi:1.2 jtag_axi\n"
            "set_property -dict [list CONFIG.PROTOCOL {2}] [get_bd_cells jtag_axi]\n"
            "\n"
            "# Clock and reset for JTAG-AXI\n"
            "connect_bd_net [get_bd_pins ps7/FCLK_CLK0] [get_bd_pins jtag_axi/aclk]\n"
            "connect_bd_net [get_bd_pins rst/peripheral_aresetn] [get_bd_pins jtag_axi/aresetn]\n"
            "\n"
            "# Connect JTAG-AXI as second slave on interconnect (S01)\n"
            "connect_bd_net [get_bd_pins ps7/FCLK_CLK0] [get_bd_pins axi_ic/S01_ACLK]\n"
            "connect_bd_net [get_bd_pins rst/peripheral_aresetn] [get_bd_pins axi_ic/S01_ARESETN]\n"
            "connect_bd_intf_net [get_bd_intf_pins jtag_axi/M_AXI] [get_bd_intf_pins axi_ic/S01_AXI]\n"
            "\n"
            f"# Assign address — JTAG-AXI can reach DUT registers\n"
            f"assign_bd_address -target_address_space /jtag_axi/Data "
            f"[get_bd_addr_segs dut/s_axi/reg0] -range {axi.get('range', '4K')} "
            f"-offset {axi['base_address']}\n"
            "\n"
            'puts "  JTAG-to-AXI debug master added (S01 -> DUT)"'
        )
    else:
        jtag_axi_tcl = "# No JTAG-to-AXI (jtag_axi_dump not configured in hil.json)"

    if has_jtag_axi:
        print(f"  JTAG-AXI: enabled (jtag_axi_dump configured)")

    # Generate block_design.tcl from template
    fclk = str(axi.get("fclk_mhz", 100))
    bd_tcl = expand_template(
        os.path.join(tcl_dir(), "block_design.template.tcl"),
        os.path.join(build_dir, "block_design.tcl"),
        {
            "{{PRESET_TCL}}": preset_path,
            "{{FCLK_MHZ}}": fclk,
            "{{DUT_ENTITY}}": dut["entity"],
            "{{IP_VLNV}}": ip_vlnv,
            "{{EXTERNALIZE_TCL}}": build_externalize_tcl(hil_config),
            "{{AXI_RANGE}}": axi.get("range", "4K"),
            "{{AXI_BASE_ADDRESS}}": axi["base_address"],
            "{{NUM_SI}}": num_si,
            "{{JTAG_AXI_TCL}}": jtag_axi_tcl,
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
            "{{IP_REPO_DIR}}": ip_dir if ip_vlnv else "",
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
