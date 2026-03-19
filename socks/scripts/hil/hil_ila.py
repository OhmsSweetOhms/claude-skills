#!/usr/bin/env python3
"""
Stage 18: HIL ILA Capture -- Multi-capture ILA waveforms via serial pacing.

VCD required: hard-fails if VCD missing. Requires ila_trigger_plan.json and
hil_top.ltx in build/hil/.

Flow:
  1. Launch Vivado in interactive mode (programs FPGA, discovers ILA)
  2. Boot CPU via XSDB (boot_cpu.tcl)
  3. Open serial port
  4. For each capture: ARM ILA -> send go byte -> wait trigger -> read UART
  5. Print summary

Usage:
    python scripts/hil/hil_ila.py --project-dir .

Exit codes:
    0  All captures succeeded
    1  One or more captures failed (or VCD missing)
"""

import argparse
import glob
import json
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hil_lib import (
    load_hil_json, hil_build_dir, tcl_dir, find_xsdb, find_serial_port,
)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from socks_lib import (
    find_vivado_settings, print_header, print_separator,
    pass_str, fail_str, yellow,
)
from project_config import get_scope


class VivadoILA:
    """Manage Vivado as an interactive subprocess for ILA operations."""

    def __init__(self, vivado_path, ila_tcl, build_dir):
        self.vivado_path = vivado_path
        self.ila_tcl = ila_tcl
        self.build_dir = build_dir
        self.proc = None

    def start(self, timeout=90):
        """Launch Vivado in interactive mode; wait for ILA_READY."""
        cmd = [
            self.vivado_path, "-mode", "tcl", "-notrace",
            "-nojournal", "-nolog",
            "-source", self.ila_tcl,
            "-tclargs", self.build_dir, "--interactive",
        ]
        print(f"  [ILA] Launching Vivado...")
        self.proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            cwd=self.build_dir,
        )
        t0 = time.time()
        while time.time() - t0 < timeout:
            line = self.proc.stdout.readline()
            if not line:
                raise RuntimeError("Vivado exited unexpectedly during startup")
            line = line.rstrip()
            if "ILA_READY" in line:
                return
            if "ERROR:" in line and "No ILA" in line:
                raise RuntimeError(f"Vivado error: {line}")
        raise TimeoutError(f"Vivado did not become ready within {timeout}s")

    def send_arm(self, probe, value, compare, csv_path):
        """Send ARM command to Vivado (non-blocking)."""
        cmd = f"ARM {probe} {value} {compare} {csv_path}\n"
        self.proc.stdin.write(cmd)
        self.proc.stdin.flush()

    def wait_arm_result(self, timeout=30):
        """Read until ILA_DONE, ILA_TIMEOUT, or ILA_ERROR.
        Returns (success, detail)."""
        t0 = time.time()
        while time.time() - t0 < timeout:
            line = self.proc.stdout.readline()
            if not line:
                return False, "Vivado exited unexpectedly"
            line = line.rstrip()
            if line.startswith("ILA_DONE"):
                return True, line
            if line.startswith("ILA_TIMEOUT"):
                return False, "trigger timeout"
            if line.startswith("ILA_ERROR"):
                return False, line
        return False, "arm command timeout"

    def quit(self):
        """Send QUIT and wait for Vivado to exit."""
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.stdin.write("QUIT\n")
                self.proc.stdin.flush()
                self.proc.wait(timeout=30)
            except Exception:
                self.proc.kill()
                self.proc.wait()

    def __del__(self):
        if self.proc and self.proc.poll() is None:
            self.proc.kill()


def read_uart_line(ser, timeout=10):
    """Read lines from serial until a test result line or timeout."""
    t0 = time.time()
    buf = ""
    while time.time() - t0 < timeout:
        data = ser.read(256)
        if not data:
            continue
        text = data.decode("ascii", errors="replace").replace("\r", "")
        buf += text
        sys.stdout.write(text)
        sys.stdout.flush()
        for line in buf.split("\n"):
            line = line.strip()
            if line.startswith("Test ") and ": " in line:
                return line
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Stage 18: HIL ILA Capture")
    parser.add_argument("--project-dir", required=True, help="Project root")
    parser.add_argument("--serial", default=None, help="Serial port override")
    parser.add_argument("--timeout", type=int, default=120,
                        help="Overall timeout (seconds)")
    parser.add_argument("--settings", default=None,
                        help="Path to Vivado settings64.sh")
    args = parser.parse_args()

    project_dir = os.path.abspath(args.project_dir)
    print_header("Stage 18: HIL ILA Capture")

    # VCD is required for module/block scope; system scope runs capture-only
    project_scope = get_scope(project_dir)
    vcd_files = glob.glob(os.path.join(project_dir, "build", "sim", "*.vcd"))
    if not vcd_files and project_scope != "system":
        print(f"\n  ERROR: VCD not found at build/sim/*.vcd. "
              f"Run Stage 7 to generate a VCD and fix any simulation errors.")
        return 1

    # Load hil.json (hard-fail if missing)
    hil_config = load_hil_json(project_dir)
    if hil_config is None:
        print(f"\n  ERROR: hil.json not found after prep. "
              f"Create it manually or run test discovery first.")
        return 1

    build_dir = hil_build_dir(project_dir)

    # Check prerequisites
    ltx_path = os.path.join(build_dir, "hil_top.ltx")
    plan_path = os.path.join(build_dir, "ila_trigger_plan.json")
    elf_path = os.path.join(build_dir, "vitis_ws", "hil_app", "Debug", "hil_app.elf")
    ps7_init = os.path.join(build_dir, "ps7_init.tcl")

    if not os.path.isfile(ltx_path):
        print(f"\n  No hil_top.ltx -- ILA not present in build")
        print(f"  Rebuild with --debug or ensure VCD exists before Stage 14")
        return 0

    if not os.path.isfile(plan_path):
        print(f"\n  No ila_trigger_plan.json in {build_dir}")
        print(f"  Create it (hand-written or Claude-assisted from VHDL FSM states)")
        return 1

    for path, label in [(elf_path, "firmware ELF"), (ps7_init, "ps7_init.tcl")]:
        if not os.path.isfile(path):
            print(f"\n  ERROR: {label} not found: {path}")
            return 1

    # Check debug firmware build marker
    debug_marker = os.path.join(build_dir, "vitis_ws", ".debug_build")
    if not os.path.isfile(debug_marker):
        print(f"\n  Debug firmware not found (no .debug_build marker)")
        print(f"  Invoking debug rebuild through socks.py...")
        socks_py = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "socks.py")
        rebuild_cmd = [
            sys.executable, socks_py,
            "--project-dir", project_dir,
            "--stages", "16",
        ]
        # Pass --debug via environment since socks.py forwards it
        rebuild_env = os.environ.copy()
        rebuild_env["SOCKS_DEBUG_BUILD"] = "1"
        rebuild_rc = subprocess.run(rebuild_cmd, env=rebuild_env).returncode
        if rebuild_rc != 0:
            print(f"\n  ERROR: Debug firmware rebuild failed (rc={rebuild_rc})")
            return 1
        # Reprogram board after debug rebuild
        print(f"\n  Reprogramming board after debug rebuild...")
        flash_tcl = os.path.join(tcl_dir(), "flash.tcl")
        xsdb = find_xsdb()
        if xsdb and os.path.isfile(flash_tcl):
            bit_files = glob.glob(os.path.join(build_dir, "vivado_project", "*/impl_1/*.bit"))
            ps7_path = os.path.join(build_dir, "ps7_init.tcl")
            if bit_files and os.path.isfile(ps7_path):
                subprocess.run(
                    [xsdb, flash_tcl, bit_files[0], elf_path, ps7_path],
                    cwd=build_dir, timeout=60)

    # Load trigger plan
    with open(plan_path) as f:
        plan = json.load(f)
    captures = plan["captures"]

    # Validate trigger plan format (reject old probe/value fields)
    for cap in captures:
        if "probe" in cap or "value" in cap:
            print(f"\n  ERROR: ila_trigger_plan.json uses deprecated probe/value fields. "
                  f"Update to trigger_probe/trigger_value/trigger_compare.")
            return 1

    # Capture count coupling (hard-fail)
    hil_json_path = os.path.join(project_dir, "hil.json")
    if os.path.isfile(hil_json_path):
        with open(hil_json_path) as f:
            hil_full = json.load(f)
        fw_debug_iter = hil_full.get("firmware", {}).get("debug_iterations")
        if fw_debug_iter is not None:
            if len(captures) != fw_debug_iter:
                print(f"\n  ERROR: ila_trigger_plan.json has {len(captures)} captures "
                      f"but firmware.debug_iterations is {fw_debug_iter}. These must match.")
                return 1

    print(f"\n  Project:  {project_dir}")
    print(f"  Plan:     {plan_path} ({len(captures)} captures)")
    print(f"  Timeout:  {args.timeout}s")

    # Find tools
    settings = args.settings or find_vivado_settings()
    if settings is None:
        print(f"\n  ERROR: Vivado settings64.sh not found")
        return 1

    # Source settings and find vivado binary
    try:
        vivado_path = subprocess.check_output(
            ["bash", "-c", f'source "{settings}" && which vivado'],
            stderr=subprocess.DEVNULL, text=True,
        ).strip()
    except subprocess.CalledProcessError:
        print(f"\n  ERROR: vivado not found after sourcing settings")
        return 1

    xsdb = find_xsdb()
    if xsdb is None:
        print(f"\n  ERROR: XSDB not found")
        return 1

    port = args.serial or find_serial_port(hil_config)
    if port is None:
        print(f"\n  ERROR: No serial port found")
        return 1

    ila_tcl = os.path.join(tcl_dir(), "ila_capture.tcl")
    boot_tcl = os.path.join(tcl_dir(), "boot_cpu.tcl")

    # Step 1: Launch Vivado interactive
    print(f"\n  Step 1: Starting Vivado (program FPGA + setup ILA)")
    ila = VivadoILA(vivado_path, ila_tcl, build_dir)
    try:
        ila.start(timeout=90)
    except (RuntimeError, TimeoutError) as e:
        print(f"  ERROR: {e}")
        ila.quit()
        return 1
    print(f"  Vivado ready, ILA discovered")

    # Step 2: Boot CPU via XSDB
    print(f"\n  Step 2: Booting CPU via XSDB")
    xsdb_result = subprocess.run(
        [xsdb, boot_tcl, elf_path, ps7_init],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, timeout=60, cwd=build_dir,
    )
    if xsdb_result.returncode != 0:
        print(f"  WARNING: XSDB exited with code {xsdb_result.returncode}")

    # Step 3: Open serial port
    print(f"\n  Step 3: Opening serial port {port}")
    import serial as _serial
    ser = _serial.Serial(port, 115200, timeout=0.1,
                         xonxoff=False, rtscts=False, dsrdtr=False)
    ser.reset_input_buffer()

    # Step 4: Serial-paced multi-capture
    print(f"\n  Step 4: Running {len(captures)} captures")
    results = []
    t_start = time.time()

    for i, cap in enumerate(captures):
        if time.time() - t_start > args.timeout:
            print(f"\n  Overall timeout ({args.timeout}s) reached")
            break

        name = cap["name"]
        probe = cap["trigger_probe"]
        value = cap["trigger_value"]
        compare = cap.get("trigger_compare", "eq")
        output = cap["output"]
        desc = cap.get("description", "")

        print(f"\n  --- Capture {i+1}/{len(captures)}: {name} ---")
        if desc:
            print(f"      {desc}")
        print(f"      Trigger: {probe} {compare} {value}")

        csv_path = os.path.join(build_dir, output)
        ila.send_arm(probe, value, compare, csv_path)
        time.sleep(0.3)

        # Send go byte
        ser.write(b"G")
        ser.flush()

        ok, detail = ila.wait_arm_result(timeout=30)
        uart_result = read_uart_line(ser, timeout=5)

        if ok:
            size = os.path.getsize(csv_path) if os.path.exists(csv_path) else 0
            print(f"      OK -- {output} ({size} bytes)")
            results.append((name, True, output))
        else:
            print(f"      FAILED -- {detail}")
            results.append((name, False, detail))

    # Cleanup
    ser.close()
    ila.quit()

    # Summary
    ok_count = sum(1 for _, ok, _ in results if ok)
    print()
    print_separator()
    for name, ok, detail in results:
        status = pass_str() if ok else fail_str()
        print(f"  [{status}] {name:20s}  {detail}")
    print()
    print(f"  {ok_count}/{len(results)} captures successful")
    print_separator()

    return 0 if ok_count == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
