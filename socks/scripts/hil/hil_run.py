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
    if fw.get("flow") == "no_os_make":
        return True
    return _load_state_json(project_dir, "no-os-make.json") is not None


def _select_serial_port(hil_config, override=None):
    if override:
        return override
    candidates = list_serial_candidates(hil_config)
    role = firmware_uart_role(hil_config)
    selected = select_uart_by_role(role, candidates, hil_config)
    return selected or find_serial_port(hil_config)


def _pass_marker_config(hil_config, project_dir=None):
    fw = hil_config.get("firmware", {}) if hil_config else {}
    markers = None
    match_mode = None
    if project_dir:
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

    family = board_family(hil_config)
    init_name = boot_init_filename(family)

    # Check prerequisites: bitstream + ELF + boot init
    bit_files = glob.glob(os.path.join(
        build_dir, "vivado_project", f"{project_name}.runs", "impl_1", "*.bit"))
    if not bit_files:
        bit_files = glob.glob(os.path.join(
            build_dir, "vivado_project", "*.runs", "impl_1", "*.bit"))
    if no_os_flow:
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
    if not elf_path or not os.path.isfile(elf_path):
        missing.append("firmware (hil_app.elf)")
    if not os.path.isfile(boot_init):
        missing.append(init_name)

    if missing:
        print(f"\n  ERROR: Missing: {', '.join(missing)}")
        print(f"  Run Stages 15-16 first.")
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

    # Find serial port
    port = _select_serial_port(hil_config, args.serial)
    if port is None:
        print(f"\n  ERROR: No serial port found (use --serial)")
        return 1

    processor = firmware_processor(hil_config)
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
