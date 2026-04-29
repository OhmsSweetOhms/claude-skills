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
import os
import shlex
import subprocess
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hil_lib import (
    load_hil_json, hil_build_dir, tcl_dir, find_xsdb, find_serial_port,
    board_family, boot_init_filename,
)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from socks_lib import (
    print_header, print_separator, pass_str, fail_str, yellow, bold,
)


class UartCapture:
    """Background thread that captures UART output and scans for markers."""

    def __init__(self, port, baud=115200, pass_marker="HIL_PASS",
                 fail_marker="HIL_FAIL"):
        self.port = port
        self.baud = baud
        self.pass_marker = pass_marker
        self.fail_marker = fail_marker
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
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

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

            with self.lock:
                self.log.append(text)
                buf += text
                if len(buf) > 4096:
                    buf = buf[-2048:]

                if self.pass_marker in buf:
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

    family = board_family(hil_config)
    init_name = boot_init_filename(family)

    # Check prerequisites: bitstream + ELF + boot init
    bit_files = glob.glob(os.path.join(
        build_dir, "vivado_project", f"{project_name}.runs", "impl_1", "*.bit"))
    if not bit_files:
        bit_files = glob.glob(os.path.join(
            build_dir, "vivado_project", "*.runs", "impl_1", "*.bit"))
    elf_path = os.path.join(build_dir, "vitis_ws", "hil_app", "Debug", "hil_app.elf")
    boot_init = os.path.join(build_dir, init_name)

    missing = []
    if not bit_files:
        missing.append("bitstream (.bit)")
    if not os.path.isfile(elf_path):
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
    port = args.serial or find_serial_port(hil_config)
    if port is None:
        print(f"\n  ERROR: No serial port found (use --serial)")
        return 1

    print(f"\n  Project:   {project_dir}")
    print(f"  Bitstream: {os.path.relpath(bitstream, project_dir)}")
    print(f"  Firmware:  {os.path.relpath(elf_path, project_dir)}")
    if zynqmp_boot_elfs:
        print(f"  PMUFW:     {os.path.relpath(zynqmp_boot_elfs[0], project_dir)}")
        print(f"  FSBL:      {os.path.relpath(zynqmp_boot_elfs[1], project_dir)}")
    print(f"  Serial:    {port}")
    print(f"  XSDB:      {xsdb}")

    # Start UART capture before programming
    pass_marker = fw.get("pass_marker", "HIL_PASS")
    fail_marker = fw.get("fail_marker", "HIL_FAIL")
    timeout = fw.get("timeout_s", args.timeout)

    uart = UartCapture(port, pass_marker=pass_marker, fail_marker=fail_marker)
    uart.start()

    # Program the board via XSDB
    flash_name = "flash_ps7.tcl" if family == "zynq7000" else "flash_psu.tcl"
    flash_tcl = os.path.join(tcl_dir(), flash_name)
    cmd = [xsdb, flash_tcl, bitstream, elf_path, boot_init] + zynqmp_boot_elfs
    print(f"\n  Programming board...")
    prog_result = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=120,
        text=True,
        cwd=build_dir,
    )
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
        print(f"  RESULT: {pass_str()} -- {pass_marker}")
    elif result == "FAIL":
        print(f"  RESULT: {fail_str()} -- {fail_marker}")
    else:
        print(f"  RESULT: {fail_str()} -- TIMEOUT (no {pass_marker}/{fail_marker})")
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
