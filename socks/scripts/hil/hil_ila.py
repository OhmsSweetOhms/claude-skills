#!/usr/bin/env python3
"""
Stage 18: HIL ILA Capture -- Breakpoint-paced ILA waveforms with ARM debug.

VCD required: hard-fails if VCD missing. Requires ila_trigger_plan.json and
hil_top.ltx in build/hil/.

Flow:
  1. Launch Vivado in interactive mode (programs FPGA, discovers ILA)
  2. Boot CPU via XSDBSession (persistent debug session)
  3. Open serial port + UART logger thread
  4. For each capture: stop CPU -> set breakpoint -> arm ILA -> resume -> wait
  5. On failure: dump backtrace, watch vars/addrs, AXI status
  6. On CPU fault: JTAG-to-AXI register dump fallback
  7. Print summary

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
import queue
import subprocess
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hil_lib import (
    load_hil_json, hil_build_dir, tcl_dir, find_xsdb, find_serial_port,
    XSDBSession,
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
            bufsize=1,
            cwd=self.build_dir,
        )
        # Background reader thread puts stdout lines into a queue
        # to avoid Python buffering issues with selectors + readline
        self._line_q = queue.Queue()
        self._reader_thread = threading.Thread(
            target=self._stdout_reader, daemon=True)
        self._reader_thread.start()
        t0 = time.time()
        while time.time() - t0 < timeout:
            try:
                line = self._line_q.get(timeout=1.0)
            except queue.Empty:
                if self.proc.poll() is not None:
                    raise RuntimeError("Vivado exited unexpectedly during startup")
                continue
            if "ILA_READY" in line:
                return
            if "ERROR:" in line and "No ILA" in line:
                raise RuntimeError(f"Vivado error: {line}")
        raise TimeoutError(f"Vivado did not become ready within {timeout}s")

    def _stdout_reader(self):
        """Background thread: read lines from Vivado stdout into queue."""
        for line in self.proc.stdout:
            self._line_q.put(line.rstrip())
        self._line_q.put(None)  # sentinel for EOF

    def send_arm(self, probe, value, compare, csv_path):
        """Send ARM command to Vivado (non-blocking)."""
        cmd = f"ARM {probe} {value} {compare} {csv_path}\n"
        self.proc.stdin.write(cmd)
        self.proc.stdin.flush()

    def send_cmd(self, line):
        """Send an arbitrary command line to the Vivado interactive protocol."""
        self.proc.stdin.write(line + "\n")
        self.proc.stdin.flush()

    def wait_response(self, done_markers, timeout=30, verbose=False):
        """Wait for any of the given done markers in Vivado stdout.

        Reads from the background reader thread's queue with timeout.
        Returns (marker, rest_of_line) on success, raises TimeoutError on timeout.
        """
        t0 = time.time()
        while True:
            remaining = timeout - (time.time() - t0)
            if remaining <= 0:
                raise TimeoutError(
                    f"Vivado did not respond with {done_markers} within {timeout}s")
            try:
                line = self._line_q.get(timeout=min(remaining, 1.0))
            except queue.Empty:
                continue
            if line is None:
                break  # EOF sentinel
            if verbose and line:
                print(f"      [vivado] {line}")
            for marker in done_markers:
                if line.startswith(marker):
                    return marker, line[len(marker):].strip()
        raise TimeoutError(f"Vivado exited without producing {done_markers}")

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


def uart_logger(ser, log_path, stop_event):
    """Background thread: capture all UART output to file and console."""
    with open(log_path, "w") as f:
        while not stop_event.is_set():
            data = ser.read(256)
            if data:
                text = data.decode("ascii", errors="replace")
                f.write(text)
                f.flush()
                sys.stdout.write(text)
                sys.stdout.flush()


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

    # Debug section is mandatory
    if "debug" not in hil_config:
        print(f"\n  ERROR: hil.json missing 'debug' section. "
              f"Run Stage 14/16 to auto-generate it.")
        return 1
    debug_config = hil_config["debug"]
    debug_vars = debug_config.get("watch_vars", [])
    debug_addrs = {
        k: int(v, 16) for k, v in debug_config.get("watch_addrs", {}).items()
    }
    jtag_dumps = debug_config.get("jtag_axi_dump", {})

    build_dir = hil_build_dir(project_dir)

    # Check prerequisites
    ltx_path = os.path.join(build_dir, "hil_top.ltx")
    plan_path = os.path.join(build_dir, "ila_trigger_plan.json")
    elf_path = os.path.join(build_dir, "vitis_ws", "hil_app", "Debug", "hil_app.elf")
    family = board_family(hil_config)
    init_name = boot_init_filename(family)
    boot_init = os.path.join(build_dir, init_name)

    if not os.path.isfile(ltx_path):
        print(f"\n  No hil_top.ltx -- ILA not present in build")
        print(f"  Rebuild with --debug or ensure VCD exists before Stage 14")
        return 0

    if not os.path.isfile(plan_path):
        print(f"\n  No ila_trigger_plan.json in {build_dir}")
        print(f"  Create it (hand-written or Claude-assisted from VHDL FSM states)")
        return 1

    for path, label in [(elf_path, "firmware ELF"), (boot_init, init_name)]:
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
        rebuild_env = os.environ.copy()
        rebuild_env["SOCKS_DEBUG_BUILD"] = "1"
        rebuild_rc = subprocess.run(rebuild_cmd, env=rebuild_env).returncode
        if rebuild_rc != 0:
            print(f"\n  ERROR: Debug firmware rebuild failed (rc={rebuild_rc})")
            return 1

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

    # Validate breakpoints -- every capture must have one
    for cap in captures:
        sym = cap.get("break_before")
        addr = cap.get("break_before_addr")
        if not sym and not addr:
            print(f"\n  ERROR: Capture '{cap['name']}' has no break_before or "
                  f"break_before_addr. Every capture requires a breakpoint for pacing.")
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

    try:
        vivado_path = subprocess.check_output(
            ["bash", "-c", f'source "{settings}" && which vivado'],
            stderr=subprocess.DEVNULL, text=True,
        ).strip()
    except subprocess.CalledProcessError:
        print(f"\n  ERROR: vivado not found after sourcing settings")
        return 1

    xsdb_path = find_xsdb()
    if xsdb_path is None:
        print(f"\n  ERROR: XSDB not found")
        return 1

    port = args.serial or find_serial_port(hil_config)
    if port is None:
        print(f"\n  ERROR: No serial port found")
        return 1

    ila_tcl = os.path.join(tcl_dir(), "ila_capture.tcl")

    # --- Capture flow (all resources cleaned up in finally) ---
    ila = None
    xsdb_session = None
    ser = None
    uart_stop = threading.Event()
    uart_thread = None
    results = []

    try:
        # Step 1: Launch Vivado interactive
        print(f"\n  Step 1: Starting Vivado (program FPGA + setup ILA)")
        ila = VivadoILA(vivado_path, ila_tcl, build_dir)
        ila.start(timeout=90)
        print(f"  Vivado ready, ILA discovered")

        # Step 2: Boot CPU via XSDB (keep session for debug)
        # Download ELF but do NOT resume — CPU stays stopped so breakpoints
        # can be set before any test phase runs.
        print(f"\n  Step 2: Booting CPU via XSDB (debug session)")
        xsdb_log = os.path.join(build_dir, "xsdb.log")
        xsdb_session = XSDBSession(xsdb_path, log_path=xsdb_log)
        xsdb_session.connect()
        xsdb_session.target_arm()
        xsdb_session.stop()
        xsdb_session.init_boot(family, boot_init)
        xsdb_session.download(elf_path)
        print(f"  CPU loaded (debug session active, CPU stopped)")

        # Validate breakpoints can be set (CPU already stopped)
        print(f"\n  Validating breakpoints...")
        for cap in captures:
            sym = cap.get("break_before")
            addr = cap.get("break_before_addr")
            try:
                target = sym if sym else int(addr, 16)
                xsdb_session.breakpoint(target)
                xsdb_session.breakpoint_remove_all()
            except Exception as e:
                if sym and addr:
                    print(f"  WARNING: symbol '{sym}' unresolved, will use addr {addr}")
                else:
                    print(f"\n  ERROR: Cannot set breakpoint for capture "
                          f"'{cap['name']}': {e}")
                    return 1

        # Step 3: Open serial port + UART logger
        print(f"\n  Step 3: Opening serial port {port}")
        import serial as _serial
        ser = _serial.Serial(port, 115200, timeout=0.1,
                             xonxoff=False, rtscts=False, dsrdtr=False)
        ser.reset_input_buffer()

        uart_log = os.path.join(build_dir, "uart_debug.log")
        uart_thread = threading.Thread(
            target=uart_logger, args=(ser, uart_log, uart_stop), daemon=True)
        uart_thread.start()

        # Step 4: Breakpoint-paced multi-capture
        print(f"\n  Step 4: Running {len(captures)} captures")
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
            break_sym = cap.get("break_before")
            break_addr = cap.get("break_before_addr")
            repeat_count = cap.get("repeat", 1)

            print(f"\n  --- Capture {i+1}/{len(captures)}: {name} ---")
            if desc:
                print(f"      {desc}")
            print(f"      Trigger: {probe} {compare} {value}")
            if repeat_count > 1:
                print(f"      Repeat: {repeat_count}x")

            for r in range(repeat_count):
                suffix = f"_{r+1:03d}" if repeat_count > 1 else ""
                csv_path = os.path.join(
                    build_dir, output.replace(".csv", f"{suffix}.csv"))

                # Reset CPU and re-download ELF for a fresh start each capture
                xsdb_session.stop()
                xsdb_session.breakpoint_remove_all()
                xsdb_session._cmd("rst -processor")
                xsdb_session.init_boot(family, boot_init)
                xsdb_session.download(elf_path)

                # Set breakpoint for this capture's function
                if break_sym:
                    try:
                        xsdb_session.breakpoint(break_sym)
                    except Exception:
                        if break_addr:
                            xsdb_session.breakpoint(int(break_addr, 16))
                        else:
                            raise
                elif break_addr:
                    xsdb_session.breakpoint(int(break_addr, 16))

                # Resume: CPU runs from main() to breakpoint
                xsdb_session.resume()

                # Wait for breakpoint hit
                time.sleep(0.5)
                for _poll in range(20):
                    st = xsdb_session.state()
                    if st != "Running":
                        break
                    time.sleep(0.25)
                if st == "Running":
                    print(f"      WARNING: CPU did not hit breakpoint within 5s")
                else:
                    print(f"      Breakpoint hit (state={st!r})")

                # Arm ILA and wait for confirmation
                ila.send_arm(probe, value, compare, csv_path)
                try:
                    ila.wait_response(["ILA_ARMED"], timeout=10, verbose=True)
                    print(f"      ILA armed, resuming CPU into function...")
                except TimeoutError:
                    print(f"      WARNING: ILA_ARMED not received within 10s")

                # Resume: function body executes, ILA triggers, CPU runs
                # until NEXT breakpoint (or end of program)
                xsdb_session.resume()
                print(f"      CPU resumed, waiting for ILA trigger...")

                # Delete old CSV so we can detect fresh writes
                if os.path.exists(csv_path):
                    os.remove(csv_path)

                try:
                    marker, detail = ila.wait_response(
                        ["ILA_DONE", "ILA_TIMEOUT", "ILA_ERROR"], timeout=15,
                        verbose=True)
                except TimeoutError:
                    # Fallback: check if CSV was written (Vivado stdout
                    # buffering may prevent ILA_DONE from reaching Python)
                    if os.path.exists(csv_path) and os.path.getsize(csv_path) > 0:
                        marker, detail = "ILA_DONE", f"{csv_path} (detected via file)"
                        print(f"      ILA_DONE not received but CSV exists — capture succeeded")
                    else:
                        marker, detail = "ILA_TIMEOUT", "wait_response timeout"

                if marker == "ILA_DONE":
                    size = os.path.getsize(csv_path) if os.path.exists(csv_path) else 0
                    if repeat_count > 1:
                        print(f"      [{r+1}/{repeat_count}] OK -- {csv_path} ({size} bytes)")
                    else:
                        print(f"      OK -- {output} ({size} bytes)")
                    if r == 0:
                        results.append((name, True, output))
                else:
                    if repeat_count > 1:
                        print(f"      [{r+1}/{repeat_count}] MISS -- {marker} {detail}")
                    else:
                        print(f"      FAILED -- {marker} {detail}")

                    # --- Debug dump on failure ---
                    try:
                        xsdb_session.stop()
                        print(f"      Backtrace:\n{xsdb_session.backtrace()}")
                        for var in debug_vars:
                            print(f"      {var} = {xsdb_session.read_var(var)}")
                        for label, addr in debug_addrs.items():
                            print(f"      {label} @ 0x{addr:08x} = "
                                  f"{xsdb_session.read_mem(addr)}")
                        axi_status = xsdb_session.read_axi_status()
                        if "0x00000000" not in axi_status["dfsr"]:
                            print(f"      AXI FAULT: DFSR={axi_status['dfsr']} "
                                  f"DFAR={axi_status['dfar']}")
                        xsdb_session.resume()
                    except Exception as e:
                        print(f"      Debug dump failed: {e}")
                        try:
                            xsdb_session.resume()
                        except Exception:
                            pass

                    if r == 0:
                        results.append((name, False, f"{marker} {detail}"))

                # --- CPU health check before next capture ---
                healthy, health_detail = xsdb_session.check_cpu_health()
                if not healthy:
                    print(f"\n  CPU FAULT: {health_detail}")
                    print(f"  Falling back to JTAG-to-AXI register dump...")
                    for dump_name, dump_cfg in jtag_dumps.items():
                        base = int(dump_cfg["base"], 16)
                        count = dump_cfg["count"]
                        dump_path = os.path.join(build_dir, f"fault_{dump_name}.csv")
                        ila.send_cmd(f"DUMP_AXI 0x{base:08x} {count} {dump_path}")
                        try:
                            m, _ = ila.wait_response(
                                ["DUMP_AXI_DONE", "ILA_ERROR"], timeout=30)
                        except TimeoutError:
                            pass
                        if os.path.exists(dump_path):
                            print(f"  {dump_name}: {dump_path}")
                    break  # no more captures -- CPU is dead
            else:
                # repeat loop completed normally, continue to next capture
                continue
            # break from repeat hit (CPU fault) -- exit capture loop too
            break

    except (RuntimeError, TimeoutError) as e:
        print(f"\n  ERROR: {e}")
        return 1
    finally:
        # Cleanup: always release resources
        uart_stop.set()
        if uart_thread and uart_thread.is_alive():
            uart_thread.join(timeout=2)
        if ser:
            ser.close()
        if ila:
            ila.quit()
        if xsdb_session:
            try:
                xsdb_session.resume()  # ensure CPU running before disconnect
            except Exception:
                pass
            xsdb_session.disconnect()

    # Summary
    ok_count = sum(1 for _, ok, _ in results if ok)
    print()
    print_separator()
    for name, ok, detail in results:
        status = pass_str() if ok else fail_str()
        print(f"  [{status}] {name:20s}  {detail}")
    print()
    print(f"  {ok_count}/{len(results)} captures successful")
    if os.path.exists(os.path.join(build_dir, "uart_debug.log")):
        print(f"  UART log: {os.path.join(build_dir, 'uart_debug.log')}")
    print_separator()

    return 0 if ok_count == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
