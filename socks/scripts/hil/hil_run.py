#!/usr/bin/env python3
"""
Stage 17: HIL Program + Test -- Flash bitstream, run firmware, capture UART.

Programs the board via XSDB (flash.tcl), captures UART output in a background
thread, and scans for HIL_PASS/HIL_FAIL markers.

Usage:
    python scripts/hil/hil_run.py --project-dir .

Exit codes:
    0  Test passed (HIL_PASS received)
    1  Test failed (HIL_FAIL, timeout, or programming error)
"""

import argparse
import glob
import json
import os
import re
import shlex
import subprocess
import sys
import threading
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hil_lib import (
    load_hil_json, hil_build_dir, tcl_dir, find_xsdb, find_serial_port,
    board_family, boot_init_filename, firmware_processor, firmware_uart_role,
    list_serial_candidates, select_uart_by_role,
)
from hil_firmware import verify_elf_layout

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from socks_lib import (
    print_header, print_separator, pass_str, fail_str, yellow, bold,
)


class UartCapture:
    """Background thread that captures UART output and scans for markers."""

    def __init__(self, port, baud=115200, pass_marker="HIL_PASS",
                 fail_marker="HIL_FAIL", pass_markers=None,
                 match_mode="all", log_path=None):
        self.port = port
        self.baud = baud
        self.pass_marker = pass_marker
        self.fail_marker = fail_marker
        self.pass_markers = pass_markers or [pass_marker]
        self.match_mode = match_mode
        self._compiled_markers = [
            re.compile(pattern) for pattern in self.pass_markers
        ]
        self.matched = {pattern: False for pattern in self.pass_markers}
        self.log_path = log_path
        self.log_fh = None
        self.ser = None
        self.stop_event = threading.Event()
        self.thread = None
        self.log = []
        self.lock = threading.Lock()
        self.result_event = threading.Event()
        self.result = None

    def start(self):
        import serial as _serial
        self.ser = _serial.Serial(
            self.port, self.baud, timeout=0.1,
            xonxoff=False, rtscts=False, dsrdtr=False,
        )
        self.ser.reset_input_buffer()
        if self.log_path:
            os.makedirs(os.path.dirname(self.log_path), exist_ok=True)
            self.log_fh = open(self.log_path, "w")
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _update_marker_state(self, buf):
        for pattern, compiled in zip(self.pass_markers, self._compiled_markers):
            if not self.matched[pattern] and compiled.search(buf):
                self.matched[pattern] = True
        if self.match_mode == "any":
            return any(self.matched.values())
        return all(self.matched.values())

    def _run(self):
        buf = ""
        while not self.stop_event.is_set():
            try:
                data = self.ser.read(1024)
            except Exception:
                break
            if not data:
                continue

            text = data.decode("ascii", errors="replace").replace("\r", "")
            sys.stdout.write(text)
            sys.stdout.flush()
            if self.log_fh:
                self.log_fh.write(text)
                self.log_fh.flush()

            with self.lock:
                self.log.append(text)
                buf += text
                if len(buf) > 32768:
                    buf = buf[-16384:]

                if self._update_marker_state(buf):
                    self.result = "PASS"
                    self.result_event.set()
                elif self.fail_marker in buf:
                    self.result = "FAIL"
                    self.result_event.set()

    def wait_result(self, timeout):
        """Wait for pass/fail marker. Returns 'PASS', 'FAIL', or 'TIMEOUT'."""
        if self.result_event.wait(timeout=timeout):
            return self.result
        return "TIMEOUT"

    def stop(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=2)
        if self.ser:
            self.ser.close()
        if self.log_fh:
            self.log_fh.close()
            self.log_fh = None


def _load_state_json(project_dir, name):
    path = os.path.join(project_dir, "build", "state", name)
    if not os.path.isfile(path):
        return None
    with open(path, "r") as f:
        return json.load(f)


def _repo_root(project_dir):
    try:
        result = subprocess.run(
            ["git", "-C", project_dir, "rev-parse", "--show-toplevel"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            check=True,
        )
        return os.path.abspath(result.stdout.strip())
    except subprocess.CalledProcessError:
        return os.path.abspath(project_dir)


def _active_profile_marker_config(project_dir):
    state = _load_state_json(project_dir, "adi-profile-apply.json") or {}
    manifest_path = state.get("manifest_path")
    if not manifest_path:
        return None, None

    candidates = []
    if os.path.isabs(manifest_path):
        candidates.append(manifest_path)
    else:
        candidates.append(os.path.join(_repo_root(project_dir), manifest_path))
        candidates.append(os.path.join(project_dir, manifest_path))

    manifest = None
    for candidate in candidates:
        if os.path.isfile(candidate):
            with open(candidate, "r") as f:
                manifest = json.load(f)
            break
    if not manifest:
        return None, None

    markers = manifest.get("uart_pass_markers")
    if not markers:
        return None, None
    return markers, manifest.get("match_mode")


def _is_no_os_flow(hil_config, project_dir):
    fw = hil_config.get("firmware", {}) if hil_config else {}
    flow = fw.get("flow")
    if flow:
        return flow == "no_os_make"
    if fw.get("test_src") or fw.get("source_roots") or fw.get("driver_sources"):
        return False
    return _load_state_json(project_dir, "no-os-make.json") is not None


def _select_serial_port(hil_config, override=None):
    if override:
        return override
    candidates = list_serial_candidates(hil_config)
    role = firmware_uart_role(hil_config)
    selected = select_uart_by_role(role, candidates, hil_config)
    return selected or find_serial_port(hil_config)


def _reserved_arenas(hil_config):
    """Read the optional firmware.reserved_arenas list from hil.json.

    Schema (all entries optional in hil.json):
        firmware.reserved_arenas: [
            {"label": "r5_dma", "base": "0x70000000", "length": "0x04000000"},
            ...
        ]

    base / length accept hex strings (`"0x..."`) or plain integers.
    Returns a list of (label, base_int, length_int) tuples; empty list
    when reserved_arenas is absent or empty. Bad entries raise ValueError.
    """
    fw = hil_config.get("firmware", {}) if hil_config else {}
    raw = fw.get("reserved_arenas", []) or []
    out = []
    for entry in raw:
        if not isinstance(entry, dict):
            raise ValueError(
                "firmware.reserved_arenas entries must be objects with "
                "label/base/length")
        label = entry.get("label") or "unnamed"
        base = entry.get("base")
        length = entry.get("length")
        if base is None or length is None:
            raise ValueError(
                f"firmware.reserved_arenas[{label!r}] missing base or length")
        if isinstance(base, str):
            base = int(base, 0)
        if isinstance(length, str):
            length = int(length, 0)
        out.append((label, int(base), int(length)))
    return out


def _firmware_specs_for_preflight(hil_config, elf_path, processor, no_os_flow):
    """Return the (role_label, elf_path) firmware list the ELF overlap
    preflight should check.

    Single-role HIL returns a single-element list; multi-role HIL
    (firmware.firmwares set) returns one entry per declared firmware.
    """
    fw = hil_config.get("firmware", {}) if hil_config else {}
    if isinstance(fw.get("firmwares"), list) and fw["firmwares"]:
        specs = []
        for f in fw["firmwares"]:
            role = f.get("role") or f.get("label") or "fw"
            # Resolve elf path relative to project_dir when relative.
            ep = f.get("elf")
            if not ep:
                raise ValueError(
                    f"firmware.firmwares[{role!r}] missing elf path")
            specs.append((role, ep))
        return specs
    if "cortexa53" in (processor or "").lower() or no_os_flow:
        role = "A53"
    elif "cortexr5" in (processor or "").lower():
        role = "R5"
    else:
        role = "ps"
    return [(role, elf_path)]


# --- Multi-firmware Stage 17 (firmware.firmwares list) ---------------------

# Keys that belong to the single-firmware schema. If any of these appear
# alongside firmware.firmwares in the same hil.json, the config is
# ambiguous and Stage 17 hard-fails before any board side-effect.
_SINGLE_FW_EXCLUSIVE_KEYS = (
    "test_src", "driver_sources", "source_roots", "flow",
    "pass_marker", "pass_markers", "fail_marker",
    "match_mode", "use_active_profile_markers",
)


def _validate_firmwares_schema(fw_block):
    """If firmware.firmwares is set, no single-firmware key may also be
    set inside the same firmware block. Returns a list of conflicting
    keys; empty when clean."""
    if not (isinstance(fw_block.get("firmwares"), list) and fw_block["firmwares"]):
        return []
    return [k for k in _SINGLE_FW_EXCLUSIVE_KEYS if k in fw_block]


def _default_target_filter(role, processor=None):
    """Pick the XSDB targets -filter expression for a firmware role
    when the entry does not declare its own `target` field. Best-effort:
    if the role hints don't match, the caller MUST set `target`
    explicitly."""
    role_l = (role or "").lower()
    proc_l = (processor or "").lower()
    if "a53" in role_l or "cortexa53" in proc_l:
        return '{name =~ "*Cortex-A53 #0*"}'
    if "r5_1" in role_l or "r5#1" in role_l or "r5 #1" in role_l:
        return '{name =~ "*Cortex-R5 #1*"}'
    if "r5" in role_l or "cortexr5" in proc_l:
        return '{name =~ "*Cortex-R5 #0*"}'
    if "a9" in role_l or "cortexa9" in proc_l:
        return '{name =~ "*Cortex-A9 #0*"}'
    return None


def _resolve_entry_elf(project_dir, build_dir, elf_value):
    """Resolve a firmwares[i].elf value to an absolute path. Accepts
    project-relative or absolute paths."""
    if os.path.isabs(elf_value):
        return elf_value
    return os.path.abspath(os.path.join(project_dir, elf_value))


def _select_uart_for_role(hil_config, role):
    """Resolve a serial port for a per-firmware UART role. Falls back
    to find_serial_port() when role is unset or no match is found."""
    if role:
        candidates = list_serial_candidates(hil_config)
        sel = select_uart_by_role(role, candidates, hil_config)
        if sel:
            return sel
    return find_serial_port(hil_config)


def _build_first_firmware_cmd(xsdb, entry, bitstream, boot_init,
                              zynqmp_boot_elfs, build_dir, project_dir,
                              processor, no_os_flow):
    """Build the XSDB command for the FIRST entry in firmware.firmwares.

    The first entry does the full board boot (system reset, PSU init,
    PL program, firmware download, run). It uses either flash_psu.tcl
    or flash_psu_no_os.tcl, picked by the entry's `flash_tcl` override
    or by processor/no-OS heuristic (the same rule single-firmware
    Stage 17 uses)."""
    elf = _resolve_entry_elf(project_dir, build_dir, entry["elf"])
    flash_name = entry.get("flash_tcl")
    if not flash_name:
        if "cortexa53" in (processor or "").lower() or no_os_flow or \
                "a53" in entry.get("role", "").lower():
            flash_name = "flash_psu_no_os.tcl"
        else:
            flash_name = "flash_psu.tcl"
    flash_tcl = os.path.join(tcl_dir(), flash_name)
    cmd = [xsdb, flash_tcl, bitstream, elf, boot_init]
    if flash_name == "flash_psu_no_os.tcl":
        no_os_state = _load_state_json(project_dir, "no-os-make.json") or {}
        fsbl_path = no_os_state.get("artifacts", {}).get("fsbl_elf")
        if not fsbl_path:
            fsbl_candidates = glob.glob(os.path.join(build_dir, "no_os", "fsbl.elf"))
            fsbl_path = fsbl_candidates[0] if fsbl_candidates else ""
        cmd.append(fsbl_path)
    elif flash_name == "flash_psu.tcl":
        cmd += zynqmp_boot_elfs
    return cmd, flash_name


def _build_secondary_firmware_cmd(xsdb, entry, build_dir, project_dir, processor):
    """Build the XSDB command for a SECONDARY firmware entry. Uses
    flash_psu_load_only.tcl which connects to an already-booted board
    and loads/starts the firmware on the specified core, leaving peer
    cores untouched."""
    elf = _resolve_entry_elf(project_dir, build_dir, entry["elf"])
    target_filter = entry.get("target")
    if not target_filter:
        target_filter = _default_target_filter(
            entry.get("role"), entry.get("processor") or processor)
    if not target_filter:
        raise ValueError(
            f"firmware.firmwares[{entry.get('role','?')!r}] is a "
            f"secondary entry but no `target` filter could be inferred. "
            f"Declare an explicit `target` (e.g. "
            f"'{{name =~ \"*Cortex-R5 #0*\"}}').")
    flash_tcl = os.path.join(tcl_dir(), "flash_psu_load_only.tcl")
    return [xsdb, flash_tcl, elf, target_filter], "flash_psu_load_only.tcl"


def _run_multi_firmware(hil_config, project_dir, build_dir, bitstream, boot_init,
                        family, zynqmp_boot_elfs, xsdb, args, processor,
                        no_os_flow, timestamp):
    """Stage 17 multi-firmware orchestration. Sequentially flashes each
    firmware entry, waits for its declared markers, and proceeds to the
    next entry only after the current one passes. Per-firmware UART
    loggers run concurrently so an earlier firmware's output keeps
    streaming while a later firmware boots."""
    fw = hil_config.get("firmware", {})
    firmwares = fw["firmwares"]
    uart_log_dir = os.path.join(build_dir, f"uart-multi-{timestamp}")
    os.makedirs(uart_log_dir, exist_ok=True)
    print(f"\n  Multi-firmware Stage 17: {len(firmwares)} firmware role(s)")
    print(f"  UART log dir: {uart_log_dir}")

    all_uarts = []
    try:
        for i, entry in enumerate(firmwares):
            role = entry.get("role") or f"fw{i}"
            elf = _resolve_entry_elf(project_dir, build_dir, entry["elf"])
            if not os.path.isfile(elf):
                print(f"\n  ERROR: firmware ELF missing for role {role!r}: {elf}")
                return 1

            # Resolve per-firmware UART role -> port (concurrent loggers)
            uart_role = entry.get("uart_role")
            port = _select_uart_for_role(hil_config, uart_role)
            if port is None:
                print(f"\n  ERROR: no UART port resolved for role {role!r} "
                      f"(uart_role={uart_role!r}). Set firmware.firmwares[{i}]."
                      f"uart_role or hil.json::board.serial_fallback.")
                return 1

            # Per-entry marker set
            markers = entry.get("pass_markers") or [entry.get("pass_marker", "HIL_PASS")]
            match_mode = entry.get("match_mode", "all")
            if match_mode not in ("any", "all"):
                print(f"\n  ERROR: firmware.firmwares[{i}].match_mode must be 'any' or 'all'")
                return 1
            fail_marker = entry.get("fail_marker", "HIL_FAIL")
            entry_timeout = int(entry.get("timeout_s", args.timeout))
            uart_log = os.path.join(uart_log_dir, f"{i:02d}-{role}.log")

            print(f"\n  --- [{i+1}/{len(firmwares)}] {role} ---")
            print(f"      ELF:     {os.path.relpath(elf, project_dir)}")
            print(f"      UART:    {port} (role={uart_role!r})")
            print(f"      Markers: {markers} ({match_mode})")
            print(f"      Log:     {uart_log}")

            uart = UartCapture(
                port,
                pass_marker=markers[0] if isinstance(markers[0], str) else "HIL_PASS",
                fail_marker=fail_marker,
                pass_markers=markers,
                match_mode=match_mode,
                log_path=uart_log,
            )
            uart.start()
            all_uarts.append((role, uart, uart_log))

            # Pick + run flash command
            if i == 0:
                cmd, tcl_name = _build_first_firmware_cmd(
                    xsdb, entry, bitstream, boot_init, zynqmp_boot_elfs,
                    build_dir, project_dir, processor, no_os_flow)
            else:
                try:
                    cmd, tcl_name = _build_secondary_firmware_cmd(
                        xsdb, entry, build_dir, project_dir, processor)
                except ValueError as e:
                    print(f"\n  ERROR: {e}")
                    return 1
            print(f"      Flash:   {tcl_name}")

            program_timeout = int(entry.get("program_timeout_s",
                                  fw.get("program_timeout_s", 300)))
            try:
                prog_result = subprocess.run(
                    cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    timeout=program_timeout, text=True, cwd=build_dir,
                )
            except subprocess.TimeoutExpired as e:
                print(f"      Programming timed out after {program_timeout}s")
                return 1
            if prog_result.stdout:
                # Indent flash TCL output for readability inside the multi-fw loop
                for line in prog_result.stdout.splitlines():
                    print(f"      | {line}")
            if prog_result.returncode != 0:
                print(f"\n  {fail_str()}: {role} programming failed "
                      f"(rc={prog_result.returncode})")
                return 1

            print(f"      Waiting for pass markers (timeout={entry_timeout}s)...")
            result = uart.wait_result(entry_timeout)
            if result == "PASS":
                print(f"      {pass_str()}: {role} reached markers")
            elif result == "FAIL":
                print(f"\n  {fail_str()}: {role} hit fail marker {fail_marker!r}")
                return 1
            else:
                missing = [m for m, ok in uart.matched.items() if not ok]
                print(f"\n  {fail_str()}: {role} TIMEOUT")
                print(f"  Missing markers: {missing}")
                return 1

        print()
        print_separator()
        print(f"  RESULT: {pass_str()} -- all {len(firmwares)} firmware role(s) reached markers")
        for role, uart, log in all_uarts:
            print(f"  UART log ({role}): {log}")
        print_separator()
        return 0
    finally:
        # Stop all UART loggers cleanly regardless of outcome.
        for role, uart, log in all_uarts:
            try:
                uart.stop()
            except Exception:
                pass


def _pass_marker_config(hil_config, project_dir=None):
    fw = hil_config.get("firmware", {}) if hil_config else {}
    markers = None
    match_mode = None
    use_profile_markers = fw.get(
        "use_active_profile_markers", fw.get("flow") == "no_os_make")
    if project_dir and use_profile_markers:
        markers, match_mode = _active_profile_marker_config(project_dir)
    if not markers:
        markers = hil_config.get("pass_markers") or fw.get("pass_markers")
    if not markers:
        markers = [fw.get("pass_marker", "HIL_PASS")]
    match_mode = (
        match_mode or hil_config.get("match_mode") or fw.get("match_mode", "all")
    )
    if match_mode not in ("any", "all"):
        raise ValueError("pass marker match_mode must be 'any' or 'all'")
    return markers, match_mode


def main() -> int:
    parser = argparse.ArgumentParser(description="Stage 17: HIL Program + Test")
    parser.add_argument("--project-dir", required=True, help="Project root")
    parser.add_argument("--serial", default=None, help="Serial port override")
    parser.add_argument("--timeout", type=int, default=30,
                        help="UART capture timeout (seconds)")
    args = parser.parse_args()

    project_dir = os.path.abspath(args.project_dir)
    print_header("Stage 17: HIL Program + Test")

    # Load hil.json (hard-fail if missing)
    hil_config = load_hil_json(project_dir)
    if hil_config is None:
        print(f"\n  ERROR: hil.json not found after prep. "
              f"Create it manually or run test discovery first.")
        return 1

    build_dir = hil_build_dir(project_dir)
    dut_entity = hil_config["dut"]["entity"]
    project_name = f"hil_{dut_entity}"
    fw = hil_config.get("firmware", {})
    no_os_flow = _is_no_os_flow(hil_config, project_dir)

    # Schema validation: firmware.firmwares (multi-role) and single-firmware
    # keys are mutually exclusive. Detect mix early so the user sees a clear
    # config error instead of a confusing runtime failure.
    schema_conflict = _validate_firmwares_schema(fw)
    if schema_conflict:
        print(f"\n  ERROR: hil.json has both firmware.firmwares list AND "
              f"single-firmware keys: {schema_conflict}.")
        print(f"  These shapes are mutually exclusive. Either remove the "
              f"single-firmware keys from firmware.* and move them into "
              f"the relevant firmwares[] entry, or remove the firmwares "
              f"list to use the single-firmware schema.")
        return 1

    family = board_family(hil_config)
    init_name = boot_init_filename(family)

    # Check prerequisites: bitstream + ELF + boot init
    bit_files = glob.glob(os.path.join(
        build_dir, "vivado_project", f"{project_name}.runs", "impl_1", "*.bit"))
    if not bit_files:
        bit_files = glob.glob(os.path.join(
            build_dir, "vivado_project", "*.runs", "impl_1", "*.bit"))
    is_multi_firmware = (
        isinstance(fw.get("firmwares"), list) and fw["firmwares"]
    )
    if is_multi_firmware:
        elf_path = None  # each firmwares[] entry brings its own ELF path
    elif no_os_flow:
        no_os_state = _load_state_json(project_dir, "no-os-make.json") or {}
        elf_path = no_os_state.get("artifacts", {}).get("elf")
        if not elf_path:
            elf_candidates = glob.glob(os.path.join(build_dir, "no_os", "*.elf"))
            elf_path = elf_candidates[0] if elf_candidates else None
    else:
        elf_path = os.path.join(build_dir, "vitis_ws", "hil_app", "Debug", "hil_app.elf")
    boot_init = os.path.join(build_dir, init_name)

    missing = []
    if not bit_files:
        missing.append("bitstream (.bit)")
    if not is_multi_firmware and (not elf_path or not os.path.isfile(elf_path)):
        missing.append("firmware (hil_app.elf)")
    if not os.path.isfile(boot_init):
        missing.append(init_name)

    if missing:
        print(f"\n  ERROR: Missing: {', '.join(missing)}")
        print(f"  Run Stages 15-16 first.")
        return 1

    # ELF overlap preflight: bail before any XSDB side-effect if the
    # firmware ELF(s) intersect each other or a reserved memory arena.
    # Single-role flows (current default) pass a one-element firmware
    # list; multi-role flows will pass several when item #2 lands.
    reserved_arenas = _reserved_arenas(hil_config)
    firmware_specs = _firmware_specs_for_preflight(
        hil_config, elf_path, processor=firmware_processor(hil_config),
        no_os_flow=no_os_flow,
    )
    if reserved_arenas or len(firmware_specs) > 1:
        try:
            conflicts = verify_elf_layout(firmware_specs, reserved_arenas)
        except RuntimeError as e:
            print(f"\n  ERROR: ELF overlap preflight failed: {e}")
            return 1
        if conflicts:
            print(f"\n  {fail_str()}: ELF layout overlap before XSDB download:")
            for c in conflicts:
                print(f"    {c.describe()}")
            print(f"  Aborting before any board side-effect. Adjust the "
                  f"firmware linker placement or reserved_arenas in "
                  f"hil.json before retrying.")
            return 1

    bitstream = bit_files[0]
    zynqmp_boot_elfs = []
    if family == "zynqmp":
        pmufw_candidates = glob.glob(os.path.join(
            build_dir, "vitis_ws", "hil_platform", "zynqmp_pmufw", "pmufw.elf"))
        fsbl_candidates = glob.glob(os.path.join(
            build_dir, "vitis_ws", "hil_platform", "zynqmp_fsbl", "fsbl_a53.elf"))
        pmufw_candidates += glob.glob(os.path.join(
            build_dir, "vitis_ws", "hil_platform", "export", "*", "sw",
            "*", "boot", "pmufw.elf"))
        fsbl_candidates += glob.glob(os.path.join(
            build_dir, "vitis_ws", "hil_platform", "export", "*", "sw",
            "*", "boot", "fsbl.elf"))
        if pmufw_candidates and fsbl_candidates:
            zynqmp_boot_elfs = [pmufw_candidates[0], fsbl_candidates[0]]

    # Find tools
    xsdb = find_xsdb()
    if xsdb is None:
        print(f"\n  ERROR: XSDB not found")
        return 1

    processor = firmware_processor(hil_config)

    # Multi-firmware dispatch. When firmware.firmwares is set, hand off to
    # the multi-role orchestrator -- it does per-entry UART logging and
    # sequential boot. Single-firmware flows (the existing path) continue
    # below unchanged.
    if is_multi_firmware:
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        return _run_multi_firmware(
            hil_config=hil_config, project_dir=project_dir, build_dir=build_dir,
            bitstream=bit_files[0], boot_init=boot_init, family=family,
            zynqmp_boot_elfs=zynqmp_boot_elfs, xsdb=xsdb, args=args,
            processor=processor, no_os_flow=no_os_flow, timestamp=timestamp,
        )

    # Find serial port
    port = _select_serial_port(hil_config, args.serial)
    if port is None:
        print(f"\n  ERROR: No serial port found (use --serial)")
        return 1
    print(f"\n  Project:   {project_dir}")
    print(f"  Bitstream: {os.path.relpath(bitstream, project_dir)}")
    print(f"  Firmware:  {os.path.relpath(elf_path, project_dir)}")
    print(f"  Proc:      {processor}")
    print(f"  Flow:      {'no_os_make' if no_os_flow else 'vitis_app'}")
    if zynqmp_boot_elfs:
        print(f"  PMUFW:     {os.path.relpath(zynqmp_boot_elfs[0], project_dir)}")
        print(f"  FSBL:      {os.path.relpath(zynqmp_boot_elfs[1], project_dir)}")
    print(f"  Serial:    {port}")
    print(f"  XSDB:      {xsdb}")

    # Start UART capture before programming
    try:
        pass_markers, match_mode = _pass_marker_config(hil_config, project_dir)
    except ValueError as e:
        print(f"\n  {fail_str()}: {e}")
        return 1
    pass_marker = fw.get("pass_marker", "HIL_PASS")
    fail_marker = fw.get("fail_marker", "HIL_FAIL")
    timeout = fw.get("timeout_s", args.timeout)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    uart_log = os.path.join(build_dir, f"uart-{timestamp}.log")

    uart = UartCapture(
        port,
        pass_marker=pass_marker,
        fail_marker=fail_marker,
        pass_markers=pass_markers,
        match_mode=match_mode,
        log_path=uart_log,
    )
    uart.start()

    # Program the board via XSDB
    if family == "zynq7000":
        flash_name = "flash_ps7.tcl"
    elif "cortexa53" in processor.lower() or no_os_flow:
        flash_name = "flash_psu_no_os.tcl"
    else:
        flash_name = "flash_psu.tcl"
    flash_tcl = os.path.join(tcl_dir(), flash_name)
    cmd = [xsdb, flash_tcl, bitstream, elf_path, boot_init]
    if flash_name == "flash_psu_no_os.tcl":
        no_os_state = _load_state_json(project_dir, "no-os-make.json") or {}
        fsbl_path = no_os_state.get("artifacts", {}).get("fsbl_elf")
        if not fsbl_path:
            fsbl_candidates = glob.glob(os.path.join(build_dir, "no_os", "fsbl.elf"))
            fsbl_path = fsbl_candidates[0] if fsbl_candidates else ""
        cmd.append(fsbl_path)
    if flash_name == "flash_psu.tcl":
        cmd += zynqmp_boot_elfs
    print(f"\n  Programming board...")
    program_timeout = fw.get("program_timeout_s", 300)
    try:
        prog_result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=program_timeout,
            text=True,
            cwd=build_dir,
        )
    except subprocess.TimeoutExpired as e:
        uart.stop()
        output = e.stdout or ""
        if isinstance(output, bytes):
            output = output.decode("utf-8", errors="replace")
        if output:
            print(output)
        print(f"\n  {fail_str()}: Programming timed out after {program_timeout}s")
        return 1
    print(prog_result.stdout)

    if prog_result.returncode != 0:
        uart.stop()
        print(f"\n  {fail_str()}: Programming failed (rc={prog_result.returncode})")
        return 1

    # Wait for test result
    print(f"\n  Waiting for result (timeout={timeout}s)...")
    result = uart.wait_result(timeout)
    uart.stop()

    print()
    print_separator()
    if result == "PASS":
        print(f"  RESULT: {pass_str()} -- UART markers ({match_mode})")
    elif result == "FAIL":
        print(f"  RESULT: {fail_str()} -- {fail_marker}")
    else:
        missing_markers = [m for m, ok in uart.matched.items() if not ok]
        print(f"  RESULT: {fail_str()} -- TIMEOUT")
        print(f"  Missing markers: {missing_markers}")
    print(f"  UART log: {uart_log}")
    print_separator()

    if result != "PASS":
        return 1

    post_cmd = fw.get("post_ready_cmd")
    if post_cmd:
        if isinstance(post_cmd, str):
            post_cmd = shlex.split(post_cmd)
        elif not isinstance(post_cmd, list):
            print(f"\n  {fail_str()}: firmware.post_ready_cmd must be a string or list")
            return 1

        post_timeout = fw.get("post_ready_timeout_s", 30)
        env = os.environ.copy()
        env["HIL_PROJECT_DIR"] = project_dir
        print(f"\n  Running post-ready check: {' '.join(post_cmd)}")
        post_result = subprocess.run(
            post_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=post_timeout,
            text=True,
            cwd=project_dir,
            env=env,
        )
        print(post_result.stdout)
        if post_result.returncode != 0:
            print(f"  RESULT: {fail_str()} -- post-ready check failed")
            print_separator()
            return 1
        print(f"  RESULT: {pass_str()} -- post-ready check")
        print_separator()

    return 0


if __name__ == "__main__":
    sys.exit(main())
