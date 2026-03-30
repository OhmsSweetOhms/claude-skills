#!/usr/bin/env python3
"""Generate an ADI-compatible IP folder from a SOCKS module's socks.json.

Reads socks.json for entity name and source files, then creates a directory
compatible with the ADI IP packaging scripts (library.mk + adi_ip_xilinx.tcl).

Usage:
    python3 gen_adi_ip.py --output /path/to/analog_ip_scripts/usart_frame_ctrl_axi
    python3 gen_adi_ip.py  # defaults to ./{entity} in cwd
"""

import argparse
import json
import os
import shutil
import sys
from pathlib import Path


def read_socks_json(project_dir: Path) -> dict:
    socks_path = project_dir / "socks.json"
    if not socks_path.exists():
        sys.exit(f"Error: {socks_path} not found")
    with open(socks_path) as f:
        return json.load(f)


def gen_makefile(ip_name: str, vhd_files: list[str]) -> str:
    deps = "\n".join(f"GENERIC_DEPS += {f}" for f in vhd_files)
    return (
        f"LIBRARY_NAME := {ip_name}\n"
        f"\n"
        f"{deps}\n"
        f"\n"
        f"XILINX_DEPS += {ip_name}_ip.tcl\n"
        f"\n"
        f"include ../scripts/library.mk\n"
    )


def gen_ip_tcl(ip_name: str, vhd_files: list[str],
               vendor: str, library: str, display_name: str,
               has_axi: bool) -> str:
    file_list = " \\\n   ".join(f'"{f}"' for f in vhd_files)
    props_fn = "adi_ip_properties" if has_axi else "adi_ip_properties_lite"

    return (
        f"source ../scripts/adi_env.tcl\n"
        f"source $ad_hdl_dir/library/scripts/adi_ip_xilinx.tcl\n"
        f"\n"
        f"adi_ip_create {ip_name}\n"
        f"adi_ip_files {ip_name} [list \\\n"
        f"   {file_list} \\\n"
        f"]\n"
        f"\n"
        f"{props_fn} {ip_name}\n"
        f"\n"
        f'set_property display_name "{display_name}" [ipx::current_core]\n'
        f'set_property description  "{display_name}" [ipx::current_core]\n'
        f'set_property vendor {vendor} [ipx::current_core]\n'
        f'set_property library {library} [ipx::current_core]\n'
        f'set_property vendor_display_name {{{vendor}}} [ipx::current_core]\n'
        f"\n"
        f"ipx::create_xgui_files [ipx::current_core]\n"
        f"ipx::save_core [ipx::current_core]\n"
    )


def gen_xgui_tcl() -> str:
    return (
        'proc init_gui { IPINST } {\n'
        '  ipgui::add_param $IPINST -name "Component_Name"\n'
        '  ipgui::add_page $IPINST -name "Page 0"\n'
        '}\n'
    )


def main():
    parser = argparse.ArgumentParser(
        description="Generate ADI-compatible IP folder from SOCKS module")
    parser.add_argument("--project-dir", type=Path, default=Path("."),
                        help="SOCKS module directory containing socks.json")
    parser.add_argument("--output", type=Path, default=None,
                        help="Output directory (default: ./{entity})")
    parser.add_argument("--vendor", default="socks",
                        help="IP vendor name (default: socks)")
    parser.add_argument("--library", default="socks",
                        help="IP library name (default: socks)")
    args = parser.parse_args()

    project_dir = args.project_dir.resolve()
    socks = read_socks_json(project_dir)

    # Extract entity and sources from socks.json
    dut = socks.get("dut", {})
    ip_name = dut.get("entity")
    sources = dut.get("sources", [])
    if not ip_name:
        sys.exit("Error: socks.json missing dut.entity")
    if not sources:
        sys.exit("Error: socks.json missing dut.sources")

    # Determine output directory
    out_dir = args.output or Path(ip_name)
    out_dir = Path(out_dir).resolve()

    # Check if any source has s_axi_ ports (use adi_ip_properties vs _lite)
    has_axi = "_axi" in ip_name

    # Filenames only (strip src/ prefix)
    vhd_files = [Path(s).name for s in sources]

    # Display name from entity
    display_name = ip_name.replace("_", " ")

    print(f"Entity:  {ip_name}")
    print(f"Sources: {', '.join(vhd_files)}")
    print(f"AXI:     {'yes' if has_axi else 'no'}")
    print(f"Output:  {out_dir}")

    # Create output directory
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "xgui").mkdir(exist_ok=True)

    # Copy VHDL sources
    for src in sources:
        src_path = project_dir / src
        dst_path = out_dir / Path(src).name
        if not src_path.exists():
            print(f"Warning: {src_path} not found, skipping")
            continue
        shutil.copy2(src_path, dst_path)
        print(f"  Copied {src} -> {dst_path.name}")

    # Generate makefile
    makefile_path = out_dir / "makefile"
    makefile_path.write_text(gen_makefile(ip_name, vhd_files))
    print(f"  Generated makefile")

    # Generate IP TCL script
    tcl_path = out_dir / f"{ip_name}_ip.tcl"
    tcl_path.write_text(gen_ip_tcl(
        ip_name, vhd_files, args.vendor, args.library, display_name, has_axi))
    print(f"  Generated {ip_name}_ip.tcl")

    # Generate xgui TCL
    xgui_path = out_dir / "xgui" / f"{ip_name}_v1_0.tcl"
    xgui_path.write_text(gen_xgui_tcl())
    print(f"  Generated xgui/{ip_name}_v1_0.tcl")

    print(f"\nDone. To build: cd {out_dir} && make xilinx")


if __name__ == "__main__":
    main()
