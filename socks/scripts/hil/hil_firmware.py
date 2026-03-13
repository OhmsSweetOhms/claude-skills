#!/usr/bin/env python3
"""
Stage 16: HIL Firmware Build -- Build bare-metal test app using XSCT.

Generates build_app.tcl from template (filling driver paths from hil.json),
then runs XSCT to create the Vitis workspace and build the firmware ELF.

Usage:
    python scripts/hil/hil_firmware.py --project-dir . [--debug]

Exit codes:
    0  Firmware built successfully
    1  Error (missing XSA, XSCT failure, etc.)
"""

import argparse
import glob
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hil_lib import (
    load_hil_json, hil_build_dir, tcl_dir, find_xsct, expand_template,
)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from socks_lib import (
    find_vivado_settings, print_header, print_separator,
    pass_str, fail_str,
)


def build_import_sources_tcl(project_dir, hil_config):
    """Generate TCL importsources commands from hil.json firmware config."""
    lines = []
    fw = hil_config.get("firmware", {})

    # Test source
    test_src = fw.get("test_src")
    if test_src:
        src_path = os.path.abspath(os.path.join(project_dir, os.path.dirname(test_src)))
        lines.append(f'importsources -name hil_app -path "{src_path}"')

    # Driver sources (deduplicate directories)
    driver_dirs = set()
    for drv in fw.get("driver_sources", []):
        drv_dir = os.path.abspath(os.path.join(project_dir, os.path.dirname(drv)))
        if drv_dir not in driver_dirs:
            driver_dirs.add(drv_dir)
            lines.append(f'importsources -name hil_app -path "{drv_dir}"')

    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Stage 16: HIL Firmware Build")
    parser.add_argument("--project-dir", required=True, help="Project root")
    parser.add_argument("--debug", action="store_true",
                        help="Enable HIL_DEBUG_MODE (ILA pacing)")
    parser.add_argument("--settings", default=None,
                        help="Path to Vivado settings64.sh")
    args = parser.parse_args()

    project_dir = os.path.abspath(args.project_dir)
    print_header("Stage 16: HIL Firmware Build")

    # Load hil.json
    hil_config = load_hil_json(project_dir)
    if hil_config is None:
        print(f"\n  No hil.json -- skipping")
        return 0

    build_dir = hil_build_dir(project_dir)

    # Check prerequisite: XSA exists
    xsa_path = os.path.join(build_dir, "system_wrapper.xsa")
    if not os.path.isfile(xsa_path):
        print(f"\n  ERROR: XSA not found: {xsa_path}")
        print(f"  Run Stage 15 first.")
        return 1

    # Debug mode: only when explicitly requested (--debug flag).
    # Normal firmware runs quick tests for Stage 17.
    # Debug firmware (HIL_DEBUG_MODE) adds ILA pacing for Stage 18.
    enable_debug = args.debug

    print(f"\n  Project:  {project_dir}")
    print(f"  XSA:      {xsa_path}")
    print(f"  Debug:    {enable_debug}")

    # Find XSCT
    xsct = find_xsct()
    if xsct is None:
        print(f"\n  ERROR: XSCT not found (part of Vitis SDK)")
        return 1
    print(f"  XSCT:     {xsct}")

    # Generate build_app.tcl from template
    import_tcl = build_import_sources_tcl(project_dir, hil_config)
    build_tcl = expand_template(
        os.path.join(tcl_dir(), "build_app.template.tcl"),
        os.path.join(build_dir, "build_app.tcl"),
        {
            "{{BUILD_DIR}}": build_dir,
            "{{IMPORT_SOURCES_TCL}}": import_tcl,
        },
    )

    # Run XSCT (source Vivado settings first for environment)
    settings = args.settings or find_vivado_settings()
    debug_arg = " --debug" if enable_debug else ""

    if settings:
        cmd = f'source "{settings}" && "{xsct}" "{build_tcl}"{debug_arg}'
    else:
        cmd = f'"{xsct}" "{build_tcl}"{debug_arg}'

    print(f"\n  Building firmware...")
    result = subprocess.run(
        ["bash", "-c", cmd],
        cwd=build_dir,
    )

    if result.returncode != 0:
        print(f"\n  {fail_str()}: Firmware build failed (rc={result.returncode})")
        return 1

    # Verify ELF output
    elf_path = os.path.join(build_dir, "vitis_ws", "hil_app", "Debug", "hil_app.elf")
    if not os.path.isfile(elf_path):
        print(f"\n  {fail_str()}: ELF not generated at {elf_path}")
        return 1

    print(f"\n  {pass_str()}: Firmware built successfully")
    print(f"    ELF: {elf_path}")
    print_separator()
    return 0


if __name__ == "__main__":
    sys.exit(main())
