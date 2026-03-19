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
import shutil
import sys

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
