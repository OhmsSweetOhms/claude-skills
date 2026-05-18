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
import json
import os
import shlex
import shutil
import subprocess
import sys
from datetime import datetime

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
from project_config import load_project_config


def _resolve_project_path(project_dir, path_value):
    if os.path.isabs(path_value):
        return os.path.abspath(path_value)
    return os.path.abspath(os.path.join(project_dir, path_value))


def _load_state_json(project_dir, name):
    path = os.path.join(project_dir, "build", "state", name)
    if not os.path.isfile(path):
        return None
    with open(path, "r") as f:
        return json.load(f)


def _no_os_make_config(project_dir, socks_cfg, hil_config):
    build_cfg = socks_cfg.get("build", {}) if socks_cfg else {}
    fw_cfg = hil_config.get("firmware", {}) if hil_config else {}
    fw_flow = fw_cfg.get("flow")
    if fw_flow and fw_flow != "no_os_make":
        return None
    nested = build_cfg.get("no_os_make", {})
    fw_nested = fw_cfg.get("no_os_make", {})

    enabled = (
        build_cfg.get("flow") == "no_os_make" or
        build_cfg.get("firmware_flow") == "no_os_make" or
        fw_cfg.get("flow") == "no_os_make" or
        bool(nested) or
        bool(fw_nested)
    )
    if not enabled:
        return None

    cfg = {}
    if build_cfg.get("flow") == "no_os_make":
        cfg.update(build_cfg)
    cfg.update(nested)
    cfg.update(fw_nested)

    apply_state = _load_state_json(project_dir, "adi-profile-apply.json") or {}
    no_os_root = cfg.get("no_os_root") or apply_state.get("no_os_build_root")
    if not no_os_root:
        raise ValueError(
            "no_os_make requires build.no_os_make.no_os_root or a prior "
            "Stage 14 ADI profile apply state")

    no_os_root = _resolve_project_path(project_dir, no_os_root)
    project_rel = (
        cfg.get("project_dir") or
        cfg.get("no_os_project_dir") or
        "projects/ad9081"
    )
    hardware = cfg.get("hardware") or os.path.join("build", "hil", "system_wrapper.xsa")
    hardware = _resolve_project_path(project_dir, hardware)

    return {
        "no_os_root": no_os_root,
        "project_dir": project_rel,
        "platform": cfg.get("platform", "xilinx"),
        "hardware": hardware,
        "target": cfg.get("target", "all"),
    }


def run_no_os_make(project_dir, build_dir, cfg, settings_path):
    required = ["no_os_root", "project_dir", "platform", "hardware"]
    missing = [key for key in required if not cfg.get(key)]
    if missing:
        print(f"\n  ERROR: no_os_make missing: {', '.join(missing)}")
        return 1

    no_os_project_dir = os.path.abspath(os.path.join(
        cfg["no_os_root"], cfg["project_dir"]))
    makefile = os.path.join(no_os_project_dir, "Makefile")
    if not os.path.isfile(makefile):
        print(f"\n  ERROR: no-OS project Makefile not found: {makefile}")
        return 1
    if not os.path.isfile(cfg["hardware"]):
        print(f"\n  ERROR: no-OS HARDWARE XSA not found: {cfg['hardware']}")
        return 1

    settings = find_vitis_settings(settings_path)
    if settings is None:
        print(f"\n  ERROR: Vitis settings64.sh not found")
        return 1

    no_os_build = os.path.join(no_os_project_dir, "build")
    shutil.rmtree(no_os_build, ignore_errors=True)

    logs_dir = os.path.join(project_dir, "build", "logs")
    os.makedirs(logs_dir, exist_ok=True)
    log_path = os.path.join(logs_dir, "no_os_make_build.log")

    make_cmd = [
        "make",
        "-C", no_os_project_dir,
        f"PLATFORM={cfg['platform']}",
        f"HARDWARE={cfg['hardware']}",
        cfg.get("target", "build"),
    ]
    cmd = f"source {shlex.quote(settings)} && " + " ".join(
        shlex.quote(part) for part in make_cmd)

    print(f"\n  no-OS Make flow")
    print(f"  no-OS root: {cfg['no_os_root']}")
    print(f"  Project:    {no_os_project_dir}")
    print(f"  Hardware:   {cfg['hardware']}")
    print(f"  Settings:   {settings}")
    print(f"  Log:        {log_path}")

    result = subprocess.run(
        ["bash", "-c", cmd],
        cwd=project_dir,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    with open(log_path, "w") as lf:
        lf.write(result.stdout)
    print(result.stdout)

    if result.returncode != 0:
        print(f"\n  {fail_str()}: no-OS Make failed (rc={result.returncode})")
        return 1

    stage_dir = os.path.join(build_dir, "no_os")
    shutil.rmtree(stage_dir, ignore_errors=True)
    os.makedirs(stage_dir, exist_ok=True)

    elf_candidates = sorted(glob.glob(os.path.join(no_os_build, "*.elf")))
    if not elf_candidates:
        print(f"\n  {fail_str()}: no-OS ELF not found under {no_os_build}")
        return 1
    elf_src = elf_candidates[0]
    elf_dst = os.path.join(stage_dir, os.path.basename(elf_src))
    shutil.copy2(elf_src, elf_dst)

    copied = {"elf": elf_dst}
    for name in ("BOOT.BIN", "fsbl.elf"):
        candidates = sorted(glob.glob(os.path.join(
            no_os_build, "output_boot_bin", name)))
        if candidates:
            dst = os.path.join(stage_dir, name)
            shutil.copy2(candidates[0], dst)
            copied[name.lower().replace(".", "_")] = dst

    state_dir = os.path.join(project_dir, "build", "state")
    os.makedirs(state_dir, exist_ok=True)
    state_path = os.path.join(state_dir, "no-os-make.json")
    with open(state_path, "w") as f:
        json.dump({
            "status": "built",
            "timestamp": datetime.now().isoformat(),
            "config": cfg,
            "log_path": log_path,
            "artifacts": copied,
        }, f, indent=2)
        f.write("\n")

    print(f"\n  {pass_str()}: no-OS firmware built successfully")
    print(f"    ELF: {elf_dst}")
    if copied.get("boot_bin"):
        print(f"    BOOT.BIN: {copied['boot_bin']}")
    print(f"    State: {state_path}")
    print_separator()
    return 0


def stage_firmware_sources(project_dir, build_dir, hil_config):
    """Stage only the firmware files listed in hil.json into a clean dir.

    `importsources -path <dir>` imports every file in the directory, so we
    cannot point it at `sw/` -- unrelated files (e.g. a standalone `main.c`
    in a system project) would collide with the HIL test's `main`. Copy the
    listed files into `build/hil/fw_src/` and import that dir instead.
    Projects with a nested firmware tree can also provide
    `firmware.source_roots`: each entry copies a source directory into the
    staged import tree while preserving its internal layout.
    """
    stage = os.path.join(build_dir, "fw_src")
    shutil.rmtree(stage, ignore_errors=True)
    os.makedirs(stage, exist_ok=True)

    fw = hil_config.get("firmware", {})
    for root in fw.get("source_roots", []):
        if isinstance(root, str):
            src_rel = root
            dst_rel = os.path.basename(os.path.normpath(root))
        else:
            src_rel = root.get("src")
        if not src_rel:
            raise FileNotFoundError("firmware.source_roots entry missing src")
        if not isinstance(root, str):
            dst_rel = root.get("dest", os.path.basename(os.path.normpath(src_rel)))

        src = os.path.abspath(os.path.join(project_dir, src_rel))
        if not os.path.isdir(src):
            raise FileNotFoundError(
                f"hil.json references missing firmware source root: {src_rel}")

        dst = os.path.abspath(os.path.join(stage, dst_rel))
        if os.path.commonpath([stage, dst]) != stage:
            raise FileNotFoundError(
                f"firmware source root destination escapes stage dir: {dst_rel}")
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copytree(src, dst, dirs_exist_ok=True)

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


# --- ELF overlap preflight (Stage 17 dependency) ----------------------------
#
# Stage 17's XSDB-download path has no overlap detection: if two firmware
# ELFs are downloaded in the same boot and their PT_LOAD segments overlap,
# the second download silently overwrites parts of the first in DDR. The
# observable failure mode (firmware crashes seconds into runtime) is many
# layers downstream from the cause. verify_elf_layout() reads each ELF's
# PT_LOAD segments and the configured reserved memory arenas, and returns a
# structured conflict list so Stage 17 can bail before any board side-effect.
#
# pyelftools is imported lazily so projects that do not use this preflight
# pay no import-time cost. See scripts/hil/requirements-hil.txt for the
# install line.

class ElfLoadSegment(tuple):
    """A PT_LOAD segment named (label, start, end) where end is exclusive."""
    __slots__ = ()

    def __new__(cls, label, start, end):
        if end <= start:
            raise ValueError(
                f"PT_LOAD with non-positive length: label={label} "
                f"start=0x{start:08x} end=0x{end:08x}")
        return tuple.__new__(cls, (label, start, end))

    @property
    def label(self): return self[0]
    @property
    def start(self): return self[1]
    @property
    def end(self):   return self[2]
    @property
    def length(self): return self[2] - self[1]


class LayoutConflict(tuple):
    """A pair of overlapping memory ranges, named (left, right) where each
    side is an ElfLoadSegment-style 3-tuple."""
    __slots__ = ()

    def __new__(cls, left, right):
        return tuple.__new__(cls, (left, right))

    @property
    def left(self):  return self[0]
    @property
    def right(self): return self[1]

    def describe(self):
        l_label, l_start, l_end = self[0]
        r_label, r_start, r_end = self[1]
        return (
            f"{l_label} [0x{l_start:08x}..0x{l_end:08x}) overlaps "
            f"{r_label} [0x{r_start:08x}..0x{r_end:08x})"
        )


def _read_elf_load_segments(elf_path, role_label):
    """Return a list of ElfLoadSegment instances (one per PT_LOAD with
    non-zero memory size) for the given ELF file."""
    try:
        from elftools.elf.elffile import ELFFile
    except ImportError as exc:
        raise RuntimeError(
            "verify_elf_layout requires pyelftools. Install with "
            "`pip install pyelftools` (or "
            "`pip install -r scripts/hil/requirements-hil.txt`)."
        ) from exc

    segments = []
    with open(elf_path, "rb") as f:
        elf = ELFFile(f)
        for seg in elf.iter_segments():
            if seg["p_type"] != "PT_LOAD":
                continue
            memsz = int(seg["p_memsz"])
            if memsz == 0:
                continue
            start = int(seg["p_paddr"]) or int(seg["p_vaddr"])
            label = f"{role_label}:{os.path.basename(elf_path)}@0x{start:08x}"
            segments.append(ElfLoadSegment(label, start, start + memsz))
    return segments


def _ranges_overlap(a, b):
    """True iff half-open intervals [a.start, a.end) and [b.start, b.end)
    share at least one byte."""
    return a.start < b.end and b.start < a.end


def verify_elf_layout(firmware_elfs, reserved_arenas):
    """Verify that no firmware ELF's PT_LOAD segments overlap any other
    firmware ELF's segments or any reserved memory arena.

    Args:
        firmware_elfs: iterable of (role_label, elf_path). role_label is
            free-form text used to identify the firmware in conflict
            messages (typical values: "A53", "R5_0", "R5_1"). Single-role
            HIL flows pass a single-element iterable.
        reserved_arenas: iterable of (label, base, length) tuples for
            memory regions that must not be touched by any firmware ELF.
            Typical examples: PL-owned DMA arenas, OCM reservations, the
            FSBL stack on Zynq UltraScale+. Pass an empty iterable when
            no arenas are reserved.

    Returns:
        list of LayoutConflict instances; empty list means no overlap.
        Raises RuntimeError when pyelftools is missing or an ELF cannot
        be parsed.
    """
    all_segments = []
    for role_label, elf_path in firmware_elfs:
        if not os.path.isfile(elf_path):
            raise RuntimeError(f"firmware ELF not found: {elf_path}")
        all_segments.extend(_read_elf_load_segments(elf_path, role_label))

    arena_segments = []
    for label, base, length in reserved_arenas:
        if length <= 0:
            continue
        arena_segments.append(ElfLoadSegment(
            f"reserved:{label}", int(base), int(base) + int(length)))

    conflicts = []
    # Firmware-vs-firmware overlap (all pairs).
    for i in range(len(all_segments)):
        for j in range(i + 1, len(all_segments)):
            if _ranges_overlap(all_segments[i], all_segments[j]):
                conflicts.append(LayoutConflict(all_segments[i], all_segments[j]))
    # Firmware-vs-reserved-arena overlap.
    for seg in all_segments:
        for arena in arena_segments:
            if _ranges_overlap(seg, arena):
                conflicts.append(LayoutConflict(seg, arena))
    return conflicts


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
    socks_cfg = load_project_config(project_dir) or {}

    try:
        no_os_cfg = _no_os_make_config(project_dir, socks_cfg, hil_config)
    except ValueError as e:
        print(f"\n  ERROR: {e}")
        return 1
    if no_os_cfg:
        return run_no_os_make(project_dir, build_dir, no_os_cfg, args.settings)

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
