#!/usr/bin/env python3
"""
socks_lib.py -- Shared utilities for the SOCKS pipeline.

Provides:
  - Vivado settings discovery
  - CRC-32 reference implementation
  - VCD streaming parser
  - Vivado report file parsers
  - Colored PASS/FAIL output
"""

import os
import re
import sys
import glob
import subprocess
from dataclasses import dataclass, field
from typing import List, Tuple, Dict, Optional, Iterator

# ---------------------------------------------------------------------------
# Colored output
# ---------------------------------------------------------------------------

USE_COLOR = sys.stdout.isatty()

def _color(code: str, text: str) -> str:
    if USE_COLOR:
        return f"\033[{code}m{text}\033[0m"
    return text

def green(text: str) -> str:
    return _color("32", text)

def red(text: str) -> str:
    return _color("31", text)

def yellow(text: str) -> str:
    return _color("33", text)

def bold(text: str) -> str:
    return _color("1", text)

def pass_str() -> str:
    return green("PASS")

def fail_str() -> str:
    return red("FAIL")

def print_result(name: str, passed: bool, note: str = "") -> None:
    status = pass_str() if passed else fail_str()
    suffix = f"  [{note}]" if note and not passed else ""
    print(f"  [{status}] {name}{suffix}")

def print_header(title: str) -> None:
    sep = "=" * 72
    print(sep)
    print(f"  {title}")
    print(sep)

def print_separator() -> None:
    print("=" * 72)

# ---------------------------------------------------------------------------
# Vivado discovery
# ---------------------------------------------------------------------------

VIVADO_SEARCH_PATHS = [
    "/tools/Xilinx/Vivado/*/settings64.sh",
    "/opt/Xilinx/Vivado/*/settings64.sh",
    os.path.expanduser("~/Xilinx/Vivado/*/settings64.sh"),
]

REQUIRED_TOOLS = ["xvhdl", "xvlog", "xelab", "xsim", "vivado"]


def find_vivado_settings() -> Optional[str]:
    """Search for Vivado settings64.sh and return the latest version found."""
    candidates = []
    for pattern in VIVADO_SEARCH_PATHS:
        candidates.extend(glob.glob(pattern))

    if not candidates:
        return None

    # Sort by version number (descending) to prefer latest
    candidates.sort(reverse=True)
    return candidates[0]


def verify_tools(settings_path: str) -> Dict[str, Optional[str]]:
    """Source settings64.sh and check that all required tools resolve.

    Returns dict of tool_name -> path (or None if not found).
    """
    result = {}
    for tool in REQUIRED_TOOLS:
        try:
            out = subprocess.check_output(
                ["bash", "-c", f'source "{settings_path}" && which {tool}'],
                stderr=subprocess.DEVNULL,
                text=True,
            ).strip()
            result[tool] = out
        except subprocess.CalledProcessError:
            result[tool] = None
    return result


def get_vivado_version(settings_path: str) -> Optional[str]:
    """Extract Vivado version from the settings path."""
    m = re.search(r"Vivado/(\d+\.\d+)", settings_path)
    return m.group(1) if m else None

# ---------------------------------------------------------------------------
# CRC-32 (Ethernet polynomial, reflected)
# ---------------------------------------------------------------------------

CRC32_POLY = 0xEDB88320
CRC32_INIT = 0xFFFFFFFF
CRC32_XOR  = 0xFFFFFFFF


def crc32_byte(crc: int, byte: int) -> int:
    """Update CRC-32 with one byte (reflected / LSB-first)."""
    crc ^= byte & 0xFF
    for _ in range(8):
        if crc & 1:
            crc = (crc >> 1) ^ CRC32_POLY
        else:
            crc >>= 1
    return crc


def crc32_bytes(data: bytes, init: int = CRC32_INIT) -> int:
    """Compute CRC-32 over a byte sequence."""
    crc = init
    for b in data:
        crc = crc32_byte(crc, b)
    return crc ^ CRC32_XOR

# ---------------------------------------------------------------------------
# VCD streaming parser
# ---------------------------------------------------------------------------

VCD_CHUNK_SIZE = 128 * 1024 * 1024  # 128 MB


@dataclass
class VcdSignal:
    path: str
    width: int
    vcd_id: str


def parse_vcd_header(filepath: str) -> Tuple[Dict[str, VcdSignal], List[str]]:
    """Parse VCD header, return (signals_by_id, scope_warnings).

    signals_by_id: vcd_id -> VcdSignal
    """
    signals: Dict[str, VcdSignal] = {}
    scope_stack: List[str] = []
    warnings: List[str] = []

    with open(filepath, "r") as f:
        for line in f:
            line = line.strip()

            if line.startswith("$scope"):
                parts = line.split()
                if len(parts) >= 3:
                    scope_stack.append(parts[2])

            elif line.startswith("$upscope"):
                if scope_stack:
                    scope_stack.pop()

            elif line.startswith("$var"):
                parts = line.split()
                if len(parts) >= 5:
                    width = int(parts[2])
                    vcd_id = parts[3]
                    name = parts[4]
                    path = ".".join(scope_stack + [name])
                    signals[vcd_id] = VcdSignal(path=path, width=width,
                                                 vcd_id=vcd_id)

            elif line.startswith("$enddefinitions"):
                break

    return signals, warnings


def stream_vcd(filepath: str) -> Iterator[Tuple[int, List[Tuple[str, int]]]]:
    """Streaming VCD data parser. Yields (timestamp, [(vcd_id, value), ...]).

    Reads in chunks for memory efficiency on large files.
    """
    current_ts = 0
    changes: List[Tuple[str, int]] = []
    in_header = True
    leftover = ""

    with open(filepath, "r") as f:
        while True:
            chunk = f.read(VCD_CHUNK_SIZE)
            if not chunk:
                break

            data = leftover + chunk
            lines = data.split("\n")
            leftover = lines[-1]  # may be incomplete
            lines = lines[:-1]

            for line in lines:
                line = line.strip()
                if not line:
                    continue

                if in_header:
                    if line.startswith("$enddefinitions"):
                        in_header = False
                    continue

                if line.startswith("$"):
                    continue  # skip $dumpvars etc.

                if line.startswith("#"):
                    # New timestamp -- yield previous batch
                    if changes:
                        yield (current_ts, changes)
                        changes = []
                    current_ts = int(line[1:])

                elif line.startswith("b") or line.startswith("B"):
                    # Vector value: bVALUE ID
                    parts = line.split()
                    if len(parts) == 2:
                        val_str = parts[0][1:]  # strip 'b'
                        vcd_id = parts[1]
                        try:
                            val = int(val_str, 2)
                        except ValueError:
                            val = 0  # x/z
                        changes.append((vcd_id, val))

                elif line[0] in "01xXzZ":
                    # Scalar value: VALUE_ID (single char + id)
                    val = 1 if line[0] == "1" else 0
                    vcd_id = line[1:]
                    changes.append((vcd_id, val))

    # Yield final batch
    if changes:
        yield (current_ts, changes)

# ---------------------------------------------------------------------------
# Vivado report parsers
# ---------------------------------------------------------------------------

@dataclass
class UtilizationRow:
    resource: str
    used: int
    available: int
    util_pct: float


def parse_utilization_report(filepath: str) -> List[UtilizationRow]:
    """Parse Vivado utilization report for key resource rows."""
    rows = []
    resources_of_interest = {
        "Slice LUTs", "LUT as Logic", "Slice Registers",
        "Register as Flip Flop", "F7 Muxes", "F8 Muxes",
        "Block RAM Tile", "RAMB36/FIFO", "RAMB18",
        "DSPs", "DSP48E1", "BUFG",
    }

    try:
        with open(filepath, "r") as f:
            for line in f:
                line = line.strip()
                if "|" not in line:
                    continue
                parts = [p.strip() for p in line.split("|")]
                # Vivado format: | Site Type | Used | Fixed | Prohibited | Available | Util% |
                # After split on '|': ['', 'Site Type', 'Used', 'Fixed', 'Prohibited', 'Available', 'Util%', '']
                if len(parts) < 7:
                    continue
                name = parts[1].rstrip("*")  # strip footnote markers
                if name in resources_of_interest:
                    try:
                        used = int(parts[2])
                        avail = int(parts[5]) if parts[5] else 0
                        pct = float(parts[6]) if parts[6] else 0.0
                        rows.append(UtilizationRow(name, used, avail, pct))
                    except (ValueError, IndexError):
                        pass
    except FileNotFoundError:
        pass

    return rows


@dataclass
class TimingResult:
    check: str
    slack_ns: float
    met: bool


def parse_timing_report(filepath: str) -> List[TimingResult]:
    """Parse Vivado timing summary for WNS, WHS, WPWS.

    Vivado 2023.2 format (Design Timing Summary section):
        WNS(ns)      TNS(ns)  ...  WHS(ns)      THS(ns)  ...  WPWS(ns)  ...
        -------      -------  ...  -------      -------  ...  --------  ...
          4.960        0.000  ...    0.045        0.000  ...    4.500   ...
    """
    results = []
    try:
        with open(filepath, "r") as f:
            lines = f.readlines()

        # Find the Design Timing Summary header row and parse the data row
        for i, line in enumerate(lines):
            if "WNS(ns)" in line and "WHS(ns)" in line and "WPWS(ns)" in line:
                # Find the header columns to determine field positions
                header = line
                # Skip the dashes line, then read the data line
                data_line = None
                for j in range(i + 1, min(i + 4, len(lines))):
                    stripped = lines[j].strip()
                    # Skip empty lines and dashes-only separator lines
                    # (but not negative numbers like -6.862)
                    if not stripped:
                        continue
                    if all(c in '- ' for c in stripped):
                        continue
                    data_line = stripped
                    break

                if data_line:
                    # Extract column positions from header
                    wns_col = header.index("WNS(ns)")
                    whs_col = header.index("WHS(ns)")
                    wpws_col = header.index("WPWS(ns)")

                    # Parse values by splitting the data line on whitespace
                    # The columns align: WNS is first numeric, WHS is 5th, WPWS is 9th
                    vals = data_line.split()
                    if len(vals) >= 9:
                        for label, idx in [("Setup (WNS)", 0),
                                           ("Hold (WHS)", 4),
                                           ("Pulse Width (WPWS)", 8)]:
                            try:
                                slack = float(vals[idx])
                                results.append(TimingResult(label, slack, slack >= 0))
                            except (ValueError, IndexError):
                                pass
                    break  # Only parse the first (top-level) summary
    except FileNotFoundError:
        pass

    return results


def parse_drc_report(filepath: str) -> Tuple[int, int, List[str]]:
    """Parse Vivado DRC report. Returns (errors, warnings, critical_msgs)."""
    errors = 0
    warnings = 0
    critical: List[str] = []

    try:
        with open(filepath, "r") as f:
            for line in f:
                if "ERROR" in line.upper():
                    errors += 1
                    critical.append(line.strip())
                elif "WARNING" in line.upper():
                    warnings += 1
    except FileNotFoundError:
        pass

    return errors, warnings, critical

# ---------------------------------------------------------------------------
# VHDL helpers
# ---------------------------------------------------------------------------

def strip_vhdl_comments(line: str) -> str:
    """Remove VHDL single-line comments (everything after --)."""
    idx = line.find("--")
    if idx >= 0:
        return line[:idx]
    return line


def is_in_comment(line: str, match_start: int) -> bool:
    """Return True if the match position falls inside a -- comment."""
    comment_pos = line.find("--")
    return comment_pos >= 0 and match_start >= comment_pos
