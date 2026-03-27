#!/usr/bin/env python3
"""
hil_lib.py -- Shared utilities for HIL (Hardware-in-the-Loop) stages 14-19.

Provides:
  - hil.json loading and validation
  - Serial port auto-detection
  - XSDB / XSCT tool discovery
  - TCL template expansion
  - Common path helpers
"""

import json
import os
import selectors
import shutil
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from socks_lib import print_result, pass_str, fail_str, yellow, bold


# ---------------------------------------------------------------------------
# hil.json loading
# ---------------------------------------------------------------------------

REQUIRED_HIL_KEYS = ["dut", "board", "axi"]

def load_hil_json(project_dir):
    """Load and validate hil.json from project root. Returns dict or None."""
    path = os.path.join(project_dir, "hil.json")
    if not os.path.isfile(path):
        return None
    with open(path, "r") as f:
        data = json.load(f)
    # Basic validation
    missing = [k for k in REQUIRED_HIL_KEYS if k not in data]
    if missing:
        print(f"  [HIL] hil.json missing required keys: {', '.join(missing)}")
        return None
    return data


def hil_build_dir(project_dir):
    """Return the HIL build output directory."""
    return os.path.join(project_dir, "build", "hil")


# ---------------------------------------------------------------------------
# Tool discovery
# ---------------------------------------------------------------------------

XILINX_SEARCH_DIRS = [
    "/tools/Xilinx/Vivado",
    "/opt/Xilinx/Vivado",
    os.path.expanduser("~/Xilinx/Vivado"),
]

VITIS_SEARCH_DIRS = [
    "/tools/Xilinx/Vitis",
    "/opt/Xilinx/Vitis",
    os.path.expanduser("~/Xilinx/Vitis"),
]


def find_tool(name, extra_dirs=None):
    """Find executable in PATH or known Xilinx install directories."""
    path = shutil.which(name)
    if path:
        return path
    dirs = XILINX_SEARCH_DIRS + (extra_dirs or [])
    for base in dirs:
        if os.path.isdir(base):
            try:
                versions = sorted(os.listdir(base), reverse=True)
            except OSError:
                continue
            for ver in versions:
                candidate = os.path.join(base, ver, "bin", name)
                if os.path.isfile(candidate):
                    return candidate
    return None


def find_xsdb():
    """Find XSDB executable (ships with Vivado)."""
    env = os.environ.get("XSDB")
    if env:
        return env
    return find_tool("xsdb")


def find_xsct():
    """Find XSCT executable (ships with Vitis SDK)."""
    env = os.environ.get("XSCT")
    if env:
        return env
    return find_tool("xsct", extra_dirs=VITIS_SEARCH_DIRS)


def check_pyserial():
    """Check if pyserial is available. Returns True/False."""
    try:
        import serial  # noqa: F401
        return True
    except ImportError:
        return False


# ---------------------------------------------------------------------------
# Serial port discovery
# ---------------------------------------------------------------------------

def find_serial_port(hil_config=None):
    """Auto-detect board serial port from hil.json config or fallback."""
    try:
        import serial.tools.list_ports
    except ImportError:
        return None

    vid = None
    pid = None
    fallback = None

    if hil_config and "board" in hil_config:
        board = hil_config["board"]
        vid_str = board.get("serial_vid")
        pid_str = board.get("serial_pid")
        if vid_str:
            vid = int(vid_str, 16)
        if pid_str:
            pid = int(pid_str, 16)
        fallback = board.get("serial_fallback")

    # Try exact VID:PID match
    if vid and pid:
        for p in serial.tools.list_ports.comports():
            if p.vid == vid and p.pid == pid:
                return p.device

    # Try VID-only match
    if vid:
        for p in serial.tools.list_ports.comports():
            if p.vid == vid:
                return p.device

    # Fallback: first ttyUSB or ttyACM
    for p in serial.tools.list_ports.comports():
        if "ttyUSB" in p.device or "ttyACM" in p.device:
            return p.device

    return fallback


# ---------------------------------------------------------------------------
# TCL template expansion
# ---------------------------------------------------------------------------

def expand_template(template_path, output_path, replacements):
    """Read a .template.tcl, apply str.replace() for each key, write output.

    replacements: dict of {"{{KEY}}": "value", ...}
    """
    with open(template_path, "r") as f:
        content = f.read()
    for key, val in replacements.items():
        content = content.replace(key, val)
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w") as f:
        f.write(content)
    return output_path


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

def tcl_dir():
    """Return absolute path to scripts/hil/tcl/."""
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "tcl")


def presets_dir():
    """Return absolute path to scripts/hil/presets/ (legacy location)."""
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "presets")


def boards_dir():
    """Return absolute path to references/boards/."""
    skill_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(skill_dir, "references", "boards")


def find_preset(preset_name, board_name=None):
    """Find a board preset TCL file.

    Search order:
    1. references/boards/<board_name>/ (if board_name provided)
    2. scripts/hil/presets/ (legacy location)
    """
    if board_name:
        board_path = os.path.join(boards_dir(), board_name)
        if os.path.isdir(board_path):
            # Search for any *_preset.tcl
            for f in os.listdir(board_path):
                if f.endswith("_preset.tcl"):
                    return os.path.join(board_path, f)
            # Try exact name
            candidate = os.path.join(board_path, preset_name)
            if os.path.isfile(candidate):
                return candidate

    # Legacy fallback
    candidate = os.path.join(presets_dir(), os.path.basename(preset_name))
    if os.path.isfile(candidate):
        return candidate
    return None


def xdc_dir():
    """Return absolute path to scripts/hil/xdc/."""
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "xdc")


def resolve_sources(project_dir, source_list):
    """Resolve source file paths relative to project_dir. Returns abs paths."""
    resolved = []
    for src in source_list:
        p = os.path.join(project_dir, src)
        if os.path.isfile(p):
            resolved.append(os.path.abspath(p))
        else:
            print(f"  [HIL] WARNING: Source not found: {src}")
    return resolved


# ---------------------------------------------------------------------------
# XSDBSession -- Interactive XSDB for ARM debug over JTAG
# ---------------------------------------------------------------------------

XSDB_MARKER = "===XSDB_DONE==="


class XSDBSession:
    """Interactive XSDB session for ARM debug over JTAG.

    Mirrors the VivadoILA pattern: subprocess held open, structured
    communication over pipes with marker-based framing.
    """

    def __init__(self, xsdb_path):
        self.proc = subprocess.Popen(
            [xsdb_path],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        self._sel = selectors.DefaultSelector()
        self._sel.register(self.proc.stdout, selectors.EVENT_READ)
        # XSDB suppresses the prompt when stdin is piped, so send a
        # known command with our marker to synchronize.
        self.proc.stdin.write(f"puts {XSDB_MARKER}\n")
        self.proc.stdin.flush()
        self._read_until(XSDB_MARKER)  # consume startup + marker

    def _read_until(self, sentinel, timeout=10):
        """Read stdout lines until sentinel appears. Returns accumulated text.

        Uses selectors to enforce the timeout — select() returns when
        data is available, then readline() gets one line without blocking
        indefinitely.
        """
        buf = []
        t0 = time.time()
        while True:
            remaining = timeout - (time.time() - t0)
            if remaining <= 0:
                raise TimeoutError(
                    f"XSDB did not produce '{sentinel}' within {timeout}s")
            events = self._sel.select(timeout=remaining)
            if not events:
                raise TimeoutError(
                    f"XSDB did not produce '{sentinel}' within {timeout}s")
            line = self.proc.stdout.readline()
            if not line:
                break
            line = line.rstrip()
            if sentinel in line:
                return "\n".join(buf)
            buf.append(line)
        raise TimeoutError(
            f"XSDB process ended without producing '{sentinel}'")

    def _cmd(self, tcl_cmd, timeout=10):
        """Send a Tcl command with marker framing, return output.

        Appends `puts XSDB_MARKER` after every command and waits for the
        marker instead of the xsdb% prompt.
        """
        self.proc.stdin.write(f"{tcl_cmd}\nputs {XSDB_MARKER}\n")
        self.proc.stdin.flush()
        return self._read_until(XSDB_MARKER, timeout=timeout)

    # --- Boot sequence ---

    def connect(self):
        return self._cmd("connect")

    def target_arm(self):
        return self._cmd(
            'targets -set -nocase -filter '
            '{name =~ "*Cortex*#0" || name =~ "*ARM*#0"}')

    def init_ps7(self, ps7_init_path):
        """Source ps7_init.tcl and run PS7 initialisation."""
        self._cmd(f"source {ps7_init_path}")
        self._cmd("ps7_init")
        return self._cmd("ps7_post_config")

    def download(self, elf_path):
        return self._cmd(f"dow {elf_path}", timeout=30)

    # --- Debug commands ---

    def read_mem(self, addr, count=1):
        return self._cmd(f"mrd 0x{addr:08x} {count}")

    def write_mem(self, addr, value):
        return self._cmd(f"mwr 0x{addr:08x} 0x{value:08x}")

    def read_var(self, name):
        return self._cmd(f"print {name}")

    def breakpoint(self, addr_or_sym):
        """Set a breakpoint by symbol name (str) or address (int)."""
        if isinstance(addr_or_sym, int):
            return self._cmd(f"bpadd -addr 0x{addr_or_sym:08x}")
        return self._cmd(f"bpadd -addr &{addr_or_sym}")

    def breakpoint_remove_all(self):
        return self._cmd("bpremove -all")

    def step(self):
        return self._cmd("stp")

    def next(self):
        return self._cmd("nxt")

    def resume(self):
        return self._cmd("con")

    def stop(self):
        return self._cmd("stop")

    def state(self):
        """Returns 'Running', 'Stopped', 'Lockup', etc."""
        raw = self._cmd("state").strip()
        if ":" in raw:
            return raw.split(":", 1)[1].strip()
        return raw

    def backtrace(self):
        return self._cmd("bt")

    def reg_read(self, name=""):
        """Read registers. Empty name = all, or specify e.g. 'r0', 'pc'."""
        if name:
            return self._cmd(f"rrd {name}")
        return self._cmd("rrd")

    # --- AXI / fault diagnostics ---

    def read_axi_status(self):
        """Check for AXI bus faults via ARM fault status registers.

        DFSR encodes the fault type -- external abort (0x01008) means
        AXI SLVERR/DECERR. DFAR gives the exact faulting address.
        """
        dfsr = self._cmd("rrd dfsr").strip()
        dfar = self._cmd("rrd dfar").strip()
        ifsr = self._cmd("rrd ifsr").strip()
        return {"dfsr": dfsr, "dfar": dfar, "ifsr": ifsr}

    def _get_vector_base(self):
        """Determine the active exception vector table base address.

        Portable across Zynq-7000 (ARMv7 Cortex-A9) and future
        UltraScale+ (ARMv8 Cortex-A53/R5) targets.
        """
        # Try AArch64 VBAR first (UltraScale+ Cortex-A53)
        try:
            vbar = self._cmd("rrd vbar_el1").strip()
            vbar_val = int(vbar.split()[-1], 16)
            return vbar_val, 0x800
        except (TimeoutError, ValueError, IndexError):
            pass

        # ARMv7: check SCTLR.V bit (bit 13)
        try:
            sctlr = self._cmd("rrd sctlr").strip()
            sctlr_val = int(sctlr.split()[-1], 16)
            if sctlr_val & (1 << 13):
                return 0xFFFF0000, 0x20  # high vectors
            return 0x00000000, 0x20      # low vectors (Zynq-7000 default)
        except (TimeoutError, ValueError, IndexError):
            return None, None

    def check_cpu_health(self):
        """Detect if CPU has faulted. Returns (healthy, detail).

        Checks for lockup state, exception vector PC, and data fault
        status. Leaves CPU in original running/stopped state if healthy.
        """
        state = self.state()
        if state == "Lockup":
            return False, "CPU in lockup (hard fault, needs reset)"

        was_running = (state == "Running")
        if was_running:
            self.stop()

        pc = self._cmd("rrd pc").strip()
        dfsr = self._cmd("rrd dfsr").strip()

        pc_val = 0
        try:
            pc_val = int(pc.split()[-1], 16)
        except (ValueError, IndexError):
            pass

        vbase, vsize = self._get_vector_base()
        if vbase is not None:
            in_vector = vbase <= pc_val < (vbase + vsize)
        else:
            in_vector = ((0x00000000 <= pc_val <= 0x0000001C) or
                         (0xFFFF0000 <= pc_val <= 0xFFFF001C))

        healthy = True
        detail = f"PC=0x{pc_val:08x} DFSR={dfsr}"
        if in_vector:
            healthy = False
            detail = f"CPU in exception vector: PC=0x{pc_val:08x} DFSR={dfsr}"

        if was_running and healthy:
            self.resume()

        return healthy, detail

    # --- Lifecycle ---

    def close(self):
        """Release the selectors resources."""
        self._sel.close()

    def disconnect(self):
        try:
            self._cmd("disconnect")
            self.proc.stdin.close()
            self.proc.wait(timeout=10)
        except Exception:
            self.proc.kill()
            self.proc.wait()
        finally:
            self.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.disconnect()
