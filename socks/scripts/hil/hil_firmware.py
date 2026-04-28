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
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hil_lib import (
    load_hil_json, hil_build_dir, tcl_dir, find_xsct, expand_template,
    find_vitis_settings, firmware_processor,
)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from socks_lib import (
    print_header, print_separator,
    pass_str, fail_str,
)


def stage_firmware_sources(project_dir, build_dir, hil_config):
    """Stage only the firmware files listed in hil.json into a clean dir.

    `importsources -path <dir>` imports every file in the directory, so we
    cannot point it at `sw/` -- unrelated files (e.g. a standalone `main.c`
    in a system project) would collide with the HIL test's `main`. Copy the
    listed files into `build/hil/fw_src/` and import that dir instead.
    """
    stage = os.path.join(build_dir, "fw_src")
    shutil.rmtree(stage, ignore_errors=True)
    os.makedirs(stage, exist_ok=True)

    fw = hil_config.get("firmware", {})
    rel_paths = []
    if fw.get("test_src"):
        rel_paths.append(fw["test_src"])
    rel_paths.extend(fw.get("driver_sources", []))

    for rel in rel_paths:
        src = os.path.abspath(os.path.join(project_dir, rel))
        if not os.path.isfile(src):
            raise FileNotFoundError(
                f"hil.json references missing firmware file: {rel}")
        shutil.copy2(src, os.path.join(stage, os.path.basename(rel)))

    return stage


def build_import_sources_tcl(stage_dir):
    """Generate a single importsources TCL line for the staged dir."""
    return f'importsources -name hil_app -path "{stage_dir}"'


SUPPRESSED_PATTERNS = [
    "WARNING: CONFIG.DEVICE_ID",
    "WARNING: No matching IP",
]


def filter_xsct_output(text):
    """Filter suppressed XSCT warning patterns from stdout display.

    Returns (filtered_text, suppressed_count).
    Full output is always logged to build/logs/.
    """
    lines = text.splitlines(keepends=True)
    filtered = []
    suppressed = 0
    for line in lines:
        if any(pat in line for pat in SUPPRESSED_PATTERNS):
            suppressed += 1
        else:
            filtered.append(line)
    return "".join(filtered), suppressed


def main() -> int:
    parser = argparse.ArgumentParser(description="Stage 16: HIL Firmware Build")
    parser.add_argument("--project-dir", required=True, help="Project root")
    parser.add_argument("--debug", action="store_true",
                        help="Enable HIL_DEBUG_MODE (ILA pacing)")
    parser.add_argument("--settings", default=None,
                        help="Path to Xilinx settings64.sh")
    args = parser.parse_args()

    project_dir = os.path.abspath(args.project_dir)
    print_header("Stage 16: HIL Firmware Build")

    # Load hil.json
    hil_config = load_hil_json(project_dir)
    if hil_config is None:
        print(f"\n  No hil.json -- skipping")
        return 0

    build_dir = hil_build_dir(project_dir)

    # Hard-fail if firmware source is missing (Claude must author it)
    fw_config = hil_config.get("firmware", {})
    test_src = fw_config.get("test_src", "sw/hil_test_main.c")
    test_src_path = os.path.join(project_dir, test_src)
    if not os.path.isfile(test_src_path):
        print(f"\n  ERROR: {test_src} not found. "
              f"Claude must write firmware before Stage 16 can build.")
        return 1

    # Check prerequisite: XSA exists
    xsa_path = os.path.join(build_dir, "system_wrapper.xsa")
    if not os.path.isfile(xsa_path):
        print(f"\n  ERROR: XSA not found: {xsa_path}")
        print(f"  Run Stage 15 first.")
        return 1

    # Debug mode: --debug flag or SOCKS_DEBUG_BUILD env var (set by hil_ila.py rebuild)
    enable_debug = args.debug or os.environ.get("SOCKS_DEBUG_BUILD") == "1"

    print(f"\n  Project:  {project_dir}")
    print(f"  XSA:      {xsa_path}")
    print(f"  Debug:    {enable_debug}")
    processor = firmware_processor(hil_config)
    print(f"  Proc:     {processor}")

    # Find XSCT
    xsct = find_xsct()
    if xsct is None:
        print(f"\n  ERROR: XSCT not found (part of Vitis SDK)")
        return 1
    print(f"  XSCT:     {xsct}")

    # Stage only the files listed in hil.json, then generate build_app.tcl
    try:
        stage_dir = stage_firmware_sources(project_dir, build_dir, hil_config)
    except FileNotFoundError as e:
        print(f"\n  ERROR: {e}")
        return 1
    import_tcl = build_import_sources_tcl(stage_dir)
    build_tcl = expand_template(
        os.path.join(tcl_dir(), "build_app.template.tcl"),
        os.path.join(build_dir, "build_app.tcl"),
        {
            "{{BUILD_DIR}}": build_dir,
            "{{IMPORT_SOURCES_TCL}}": import_tcl,
            "{{PROCESSOR}}": processor,
            "{{BSP_CONFIG_TCL}}": os.path.abspath(os.path.join(
                project_dir, fw_config.get("bsp_config_tcl", "")))
            if fw_config.get("bsp_config_tcl") else "",
        },
    )

    # Run XSCT from the Vitis environment. If the orchestrator forwards a
    # Vivado settings path, prefer the matching Vitis version for firmware.
    settings = find_vitis_settings(args.settings)
    debug_arg = " --debug" if enable_debug else ""

    if settings:
        cmd = f'source "{settings}" && "{xsct}" "{build_tcl}"{debug_arg}'
    else:
        cmd = f'"{xsct}" "{build_tcl}"{debug_arg}'

    print(f"\n  Building firmware...")
    result = subprocess.run(
        ["bash", "-c", cmd],
        cwd=build_dir,
        capture_output=True,
        text=True,
    )

    # Log full output to build/logs/
    logs_dir = os.path.join(project_dir, "build", "logs")
    os.makedirs(logs_dir, exist_ok=True)
    log_path = os.path.join(logs_dir, "hil_firmware_build.log")
    with open(log_path, "w") as lf:
        lf.write(result.stdout)
        lf.write(result.stderr)

    # Filter suppressed warnings from stdout display
    combined = result.stdout + result.stderr
    filtered, suppressed = filter_xsct_output(combined)
    if filtered.strip():
        for line in filtered.strip().splitlines()[-20:]:
            print(f"    {line}")
    if suppressed > 0:
        print(f"    ({suppressed} harmless XSCT warnings suppressed, full log: {log_path})")

    if result.returncode != 0:
        print(f"\n  {fail_str()}: Firmware build failed (rc={result.returncode})")
        return 1

    # Verify ELF output
    elf_path = os.path.join(build_dir, "vitis_ws", "hil_app", "Debug", "hil_app.elf")
    if not os.path.isfile(elf_path):
        print(f"\n  {fail_str()}: ELF not generated at {elf_path}")
        return 1

    # Write debug build marker if debug mode
    if enable_debug:
        marker_path = os.path.join(build_dir, "vitis_ws", ".debug_build")
        with open(marker_path, "w") as mf:
            mf.write("debug\n")
        print(f"    Debug build marker written: {marker_path}")

    print(f"\n  {pass_str()}: Firmware built successfully")
    print(f"    ELF: {elf_path}")
    print(f"    Debug: {enable_debug}")
    print_separator()
    return 0


if __name__ == "__main__":
    sys.exit(main())
