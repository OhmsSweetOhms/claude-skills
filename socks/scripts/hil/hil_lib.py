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

import atexit
import glob
import json
import os
import re
import shutil
import signal
import socket
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

VITIS_SETTINGS_SEARCH_PATHS = [
    "/tools/Xilinx/Vitis/*/settings64.sh",
    "/opt/Xilinx/Vitis/*/settings64.sh",
    os.path.expanduser("~/Xilinx/Vitis/*/settings64.sh"),
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


def find_vitis_settings(preferred_settings=None):
    """Find Vitis settings64.sh, preserving a matching Vivado version if given."""
    if preferred_settings:
        norm = os.path.abspath(preferred_settings)
        if "/Vitis/" in norm and os.path.isfile(norm):
            return norm

        parts = norm.split(os.sep)
        if "Vivado" in parts:
            idx = parts.index("Vivado")
            if idx + 1 < len(parts):
                version = parts[idx + 1]
                root = os.sep.join(parts[:idx])
                candidate = os.path.join(root, "Vitis", version, "settings64.sh")
                if os.path.isfile(candidate):
                    return candidate

    candidates = []
    for pattern in VITIS_SETTINGS_SEARCH_PATHS:
        candidates.extend(glob.glob(pattern))
    if not candidates:
        return None
    candidates.sort(reverse=True)
    return candidates[0]


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

def _port_key(port):
    device = port.get("device") if isinstance(port, dict) else port.device
    match = re.search(r'(\d+)$', device)
    suffix = int(match.group(1)) if match else -1
    return (re.sub(r'\d+$', '', device), suffix, device)


def _interface_key(port):
    if isinstance(port, dict):
        value = port.get("interface_index")
        if value is not None:
            return value
        location = str(port.get("location") or "")
        interface = str(port.get("interface") or "")
    else:
        location = str(getattr(port, "location", "") or "")
        interface = str(getattr(port, "interface", "") or "")
    match = re.search(r'[:.](\d+)$', location)
    if match:
        return int(match.group(1))
    match = re.search(r'(\d+)$', interface)
    if match:
        return int(match.group(1))
    return _port_key(port)[1]


def _port_to_candidate(port):
    return {
        "device": port.device,
        "name": getattr(port, "name", None),
        "description": getattr(port, "description", None),
        "hwid": getattr(port, "hwid", None),
        "vid": f"{port.vid:04x}" if getattr(port, "vid", None) is not None else None,
        "pid": f"{port.pid:04x}" if getattr(port, "pid", None) is not None else None,
        "serial_number": getattr(port, "serial_number", None),
        "location": getattr(port, "location", None),
        "manufacturer": getattr(port, "manufacturer", None),
        "product": getattr(port, "product", None),
        "interface": getattr(port, "interface", None),
        "interface_index": _interface_key(port),
    }


def list_serial_candidates(hil_config=None):
    """Return all serial candidates matching the board VID/PID when present."""
    try:
        import serial.tools.list_ports
    except ImportError:
        return []

    vid = None
    pid = None
    if hil_config and "board" in hil_config:
        board = hil_config["board"]
        vid_str = board.get("serial_vid")
        pid_str = board.get("serial_pid")
        if vid_str:
            vid = int(vid_str, 16)
        if pid_str:
            pid = int(pid_str, 16)

    ports = list(serial.tools.list_ports.comports())
    if vid and pid:
        matches = [p for p in ports if p.vid == vid and p.pid == pid]
    elif vid:
        matches = [p for p in ports if p.vid == vid]
    else:
        matches = [p for p in ports
                   if "ttyUSB" in p.device or "ttyACM" in p.device]

    return [_port_to_candidate(p) for p in sorted(matches, key=_port_key)]


def firmware_uart_role(hil_config=None):
    """Infer the UART role from hil.json firmware processor settings."""
    fw = hil_config.get("firmware", {}) if hil_config else {}
    explicit = fw.get("uart_role")
    if explicit:
        return str(explicit).lower()
    processor = firmware_processor(hil_config or {})
    if "cortexa53" in processor.lower() or "a53" in processor.lower():
        return "a53"
    if "cortexr5" in processor.lower() or "r5" in processor.lower():
        return "r5"
    return "default"


def select_uart_by_role(role, candidates, hil_config=None):
    """Select a UART device from candidates for a logical firmware role.

    ZCU102 CP2108 exposes multiple UART interfaces. In the ADI/SOCKS topology
    interface 0 is the A53 no-OS console and interface 1 is the R5 streaming
    console; the ttyUSB suffix can move between hosts.
    """
    if not candidates:
        return None

    fw = hil_config.get("firmware", {}) if hil_config else {}
    board = hil_config.get("board", {}) if hil_config else {}
    overrides = fw.get("uart_ports", {}) or board.get("uart_ports", {})
    if isinstance(overrides, dict):
        override = overrides.get(role)
        if override:
            return override

    sorted_candidates = sorted(candidates, key=_port_key)
    preset = str(board.get("preset", "")).lower()
    family = str(board.get("family", "")).lower()
    is_zcu102 = preset == "zcu102" or family == "zynqmp"
    if is_zcu102:
        role_to_interface = {"a53": 0, "r5": 1}
        wanted = role_to_interface.get(str(role).lower())
        if wanted is not None:
            matches = [p for p in sorted_candidates
                       if _interface_key(p) == wanted]
            if matches:
                return matches[0]["device"] if isinstance(matches[0], dict) else matches[0].device

    first = sorted_candidates[0]
    return first["device"] if isinstance(first, dict) else first.device


def find_serial_port(hil_config=None):
    """Auto-detect board serial port from hil.json config or fallback."""
    try:
        import serial.tools.list_ports
    except ImportError:
        return None

    vid = None
    pid = None
    fallback = None
    board_preset = None
    board_family_name = None
    uart_cfg = {}

    if hil_config and "board" in hil_config:
        board = hil_config["board"]
        board_preset = board.get("preset")
        board_family_name = board.get("family")
        uart_cfg = board.get("uart", {})
        vid_str = board.get("serial_vid")
        pid_str = board.get("serial_pid")
        if vid_str:
            vid = int(vid_str, 16)
        if pid_str:
            pid = int(pid_str, 16)
        fallback = board.get("serial_fallback")

    def _select_port(matches):
        if not matches:
            return None
        candidates = [_port_to_candidate(p) for p in sorted(matches, key=_port_key)]
        role = firmware_uart_role(hil_config)
        selected = select_uart_by_role(role, candidates, hil_config)
        if selected:
            return selected
        streaming_name = str(uart_cfg.get("streaming", "")).lower()
        is_zcu102 = (
            str(board_preset).lower() == "zcu102" or
            (str(board_family_name).lower() == "zynqmp" and
             pid in (0xEA70, 0xEA71))
        )
        if is_zcu102 and ("r5" in streaming_name or "uart1" in streaming_name):
            # The ZCU102 CP2108 exposes four UART functions. ADI's AMP topology
            # routes A53 UART0 to interface 0 and R5 UART1 to interface 1. The
            # /dev/ttyUSB number itself is not stable.
            uart1_matches = [p for p in matches if _interface_key(p) == 1]
            if uart1_matches:
                return sorted(uart1_matches, key=_port_key)[0].device
            return sorted(matches, key=_interface_key)[0].device
        return matches[0].device

    # Try exact VID:PID match
    if vid and pid:
        matches = [p for p in serial.tools.list_ports.comports()
                   if p.vid == vid and p.pid == pid]
        selected = _select_port(matches)
        if selected:
            return selected

    # Try VID-only match
    if vid:
        matches = [p for p in serial.tools.list_ports.comports()
                   if p.vid == vid]
        selected = _select_port(matches)
        if selected:
            return selected

    # Fallback: first ttyUSB or ttyACM
    matches = [p for p in serial.tools.list_ports.comports()
               if "ttyUSB" in p.device or "ttyACM" in p.device]
    selected = _select_port(matches)
    if selected:
        return selected

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




def board_family(config):
    """Resolve board family for boot-init routing."""
    board = config.get("board", {}) if config else {}
    explicit = board.get("family")
    if explicit:
        family = explicit.lower()
        if family in ("zynq7000", "zynqmp"):
            return family

    preset = str(board.get("preset", "")).lower()
    if preset:
        if "zcu102" in preset or "zynqmp" in preset or "psu" in preset:
            return "zynqmp"
        if "microzed" in preset or "zynq7000" in preset or "ps7" in preset:
            return "zynq7000"

    part = str(board.get("part", "")).lower()
    if part.startswith("xczu"):
        return "zynqmp"
    if part.startswith("xc7z"):
        return "zynq7000"

    return "zynq7000"


def boot_init_filename(family):
    return "ps7_init.tcl" if family == "zynq7000" else "psu_init.tcl"


def boot_init_procs(family):
    if family == "zynq7000":
        return "ps7_init", "ps7_post_config"
    return "psu_init", "psu_post_config"


def default_processor(config):
    """Return the default standalone firmware processor for the board family."""
    return "ps7_cortexa9_0" if board_family(config) == "zynq7000" else "psu_cortexa53_0"


def firmware_processor(config):
    """Resolve the Vitis processor for Stage 16 firmware builds."""
    fw = config.get("firmware", {}) if config else {}
    board = config.get("board", {}) if config else {}
    return fw.get("processor") or board.get("processor") or default_processor(config)

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
# XSDBSession -- Interactive XSDB for ARM debug over JTAG (xsdbserver socket)
# ---------------------------------------------------------------------------

XSDB_HOST = "127.0.0.1"
XSDB_PORT = 4567
XSDB_LINE_END = "\r\n"


class XsdbServer:
    """Manages an xsdbserver subprocess.

    Spawns `xsdb -interactive -eval "xsdbserver start -port N"`, captures
    stdout/stderr to build/hil/xsdb.log, and reaps the process (and any
    children) on stop().
    """

    def __init__(self, xsdb_path, port=XSDB_PORT, log_path=None,
                 connect_timeout=5.0):
        self.port = port
        self.log_path = log_path
        self._log_fh = None
        if log_path:
            os.makedirs(os.path.dirname(log_path), exist_ok=True)
            self._log_fh = open(log_path, "w")
        self.proc = subprocess.Popen(
            [xsdb_path, "-eval", f"xsdbserver start -port {port}"],
            stdin=subprocess.DEVNULL,
            stdout=self._log_fh if self._log_fh else subprocess.DEVNULL,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        self._wait_ready(connect_timeout)

    def _wait_ready(self, timeout):
        t0 = time.time()
        last_err = None
        while time.time() - t0 < timeout:
            if self.proc.poll() is not None:
                raise RuntimeError(
                    f"xsdbserver exited during startup "
                    f"(rc={self.proc.returncode}); see {self.log_path}")
            try:
                with socket.create_connection(
                        (XSDB_HOST, self.port), timeout=0.5):
                    return
            except (ConnectionRefusedError, socket.timeout, OSError) as e:
                last_err = e
                time.sleep(0.1)
        raise RuntimeError(
            f"xsdbserver did not accept connections on "
            f"{XSDB_HOST}:{self.port} within {timeout}s (last error: "
            f"{last_err!r}); see {self.log_path}")

    def stop(self, wait=True, timeout=5.0):
        if self.proc.poll() is None:
            try:
                os.killpg(os.getpgid(self.proc.pid), signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                try:
                    self.proc.terminate()
                except Exception:
                    pass
            if wait:
                try:
                    self.proc.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    try:
                        os.killpg(os.getpgid(self.proc.pid), signal.SIGKILL)
                    except (ProcessLookupError, PermissionError):
                        self.proc.kill()
                    self.proc.wait()
        if self._log_fh:
            try:
                self._log_fh.close()
            except Exception:
                pass
            self._log_fh = None


class XSDBSession:
    """Interactive XSDB session for ARM debug over JTAG.

    Transport: TCP client to a local xsdbserver spawned on port 4567.
    Framing: one "okay <blob>\\r\\n" or "error <msg>\\r\\n" frame per
    command. Multi-line Tcl results are wrapped in a single frame --
    embedded \\n characters inside <blob> are normal, only \\r\\n ends it.
    """

    def __init__(self, xsdb_path, log_path=None):
        self._server = XsdbServer(xsdb_path, port=XSDB_PORT,
                                  log_path=log_path)
        try:
            self._sock = socket.create_connection(
                (XSDB_HOST, XSDB_PORT), timeout=10)
        except OSError:
            self._server.stop()
            raise
        self._sock.settimeout(10)
        self._buf = b""
        self._closed = False
        atexit.register(self.disconnect)

    def _recv_line(self):
        term = XSDB_LINE_END.encode()
        while True:
            idx = self._buf.find(term)
            if idx >= 0:
                line = self._buf[:idx].decode("utf-8", errors="replace")
                self._buf = self._buf[idx + len(term):]
                return line
            chunk = self._sock.recv(4096)
            if not chunk:
                raise RuntimeError(
                    "xsdbserver closed the connection unexpectedly")
            self._buf += chunk

    def _cmd(self, tcl_cmd, timeout=10):
        old_timeout = self._sock.gettimeout()
        self._sock.settimeout(timeout)
        try:
            self._sock.sendall((tcl_cmd + XSDB_LINE_END).encode())
            line = self._recv_line()
        finally:
            self._sock.settimeout(old_timeout)
        if line.startswith("okay"):
            return line[5:] if len(line) > 5 else ""
        if line.startswith("error"):
            raise RuntimeError(f"xsdb: {line[6:] if len(line) > 6 else ''}")
        raise RuntimeError(f"xsdb: unexpected framing: {line!r}")

    # --- Boot sequence ---

    def connect(self):
        return self._cmd("connect")

    def target_arm(self):
        return self._cmd(
            'targets -set -nocase -filter '
            '{name =~ "*Cortex*#0" || name =~ "*ARM*#0"}')

    def init_boot(self, family, init_path):
        """Source PS boot init Tcl and run the family-specific init procs."""
        init_proc, post_proc = boot_init_procs(family)
        self._cmd(f"source {init_path}")
        self._cmd(init_proc)
        return self._cmd(post_proc)

    def init_ps7(self, ps7_init_path):
        """Backward-compatible PS7 boot initialisation alias."""
        return self.init_boot("zynq7000", ps7_init_path)

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
        try:
            return self._cmd("con")
        except RuntimeError as e:
            if "already running" in str(e).lower():
                return ""
            raise

    def stop(self):
        try:
            return self._cmd("stop")
        except RuntimeError as e:
            if "already stopped" in str(e).lower():
                return ""
            raise

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
        def _rrd(reg):
            try:
                return self._cmd(f"rrd {reg}").strip()
            except RuntimeError as e:
                return str(e)
        return {"dfsr": _rrd("dfsr"), "dfar": _rrd("dfar"),
                "ifsr": _rrd("ifsr")}

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
        except (TimeoutError, ValueError, IndexError, RuntimeError):
            pass

        # ARMv7: check SCTLR.V bit (bit 13)
        try:
            sctlr = self._cmd("rrd sctlr").strip()
            sctlr_val = int(sctlr.split()[-1], 16)
            if sctlr_val & (1 << 13):
                return 0xFFFF0000, 0x20  # high vectors
            return 0x00000000, 0x20      # low vectors (Zynq-7000 default)
        except (TimeoutError, ValueError, IndexError, RuntimeError):
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

        pc_val = 0
        try:
            pc_val = int(pc.split()[-1], 16)
        except (ValueError, IndexError):
            pass

        # Read DFSR — Cortex-A9 uses "Data Fault Status Register"
        # Try both register names (varies by XSDB version)
        dfsr_val = 0
        dfsr_raw = ""
        for dfsr_name in ("dfsr", "cp15_dfsr", "DFSR"):
            try:
                dfsr_raw = self._cmd(f"rrd {dfsr_name}").strip()
                if "error" not in dfsr_raw.lower() and "no register" not in dfsr_raw.lower():
                    dfsr_val = int(dfsr_raw.split()[-1], 16)
                    break
            except (TimeoutError, ValueError, IndexError, RuntimeError):
                continue

        vbase, vsize = self._get_vector_base()
        if vbase is not None:
            in_vector = vbase <= pc_val < (vbase + vsize)
        else:
            in_vector = ((0x00000000 <= pc_val <= 0x0000001C) or
                         (0xFFFF0000 <= pc_val <= 0xFFFF001C))

        healthy = True
        detail = f"PC=0x{pc_val:08x} DFSR=0x{dfsr_val:08x}"

        # Only flag as fault if PC is in vector table AND DFSR shows
        # an actual fault (non-zero). PC=0 after main() returns is
        # normal — the C runtime loops at the reset vector.
        if in_vector and dfsr_val != 0:
            healthy = False
            detail = f"CPU in exception vector: PC=0x{pc_val:08x} DFSR=0x{dfsr_val:08x}"

        if was_running and healthy:
            self.resume()

        return healthy, detail

    # --- Lifecycle ---

    def close(self):
        """Close the socket. Idempotent."""
        try:
            self._sock.close()
        except Exception:
            pass

    def disconnect(self):
        if self._closed:
            return
        self._closed = True
        try:
            self._cmd("disconnect", timeout=5)
        except Exception:
            pass
        self.close()
        self._server.stop()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.disconnect()
