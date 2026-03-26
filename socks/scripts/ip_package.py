#!/usr/bin/env python3
"""
Stage 21: IP Packaging -- Parse VHDL entity, detect bus interfaces,
generate Vivado ipx:: TCL, and produce component.xml.

Usage:
    python scripts/ip_package.py --project-dir /path/to/module
    python scripts/ip_package.py --project-dir . --settings /path/to/settings64.sh

Exit code 0 if packaging succeeds (or skipped due to unchanged hash), 1 on failure.
"""

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from socks_lib import (
    find_vivado_settings, print_header, print_separator, pass_str, fail_str,
    print_result,
)

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class VHDLGeneric:
    name: str
    vhdl_type: str      # "integer", "boolean", "natural", "positive", etc.
    default: Optional[str] = None


@dataclass
class VHDLPort:
    name: str
    direction: str      # "in" or "out"
    vhdl_type: str      # "std_logic" or "std_logic_vector"
    width: Optional[int] = None  # None for std_logic, bit count for vector
    width_expr: Optional[str] = None  # raw "(N downto 0)" for TCL


@dataclass
class DetectedInterface:
    kind: str           # "axi_lite", "axi_stream", "axi_full", "clock", "reset"
    name: str           # interface name (e.g., "s_axi", "m_axis_data")
    ports: List[str] = field(default_factory=list)
    direction: Optional[str] = None     # "slave"/"master" for AXI
    reset_polarity: Optional[str] = None  # "ACTIVE_LOW"/"ACTIVE_HIGH" for reset
    addr_width: Optional[int] = None    # for AXI-Lite/Full: awaddr width


# ---------------------------------------------------------------------------
# AXI signal sets
# ---------------------------------------------------------------------------

AXI_LITE_SIGNALS = {
    "awaddr", "awvalid", "awready", "awprot",
    "wdata", "wstrb", "wvalid", "wready",
    "bresp", "bvalid", "bready",
    "araddr", "arvalid", "arready", "arprot",
    "rdata", "rresp", "rvalid", "rready",
}

AXI_LITE_REQUIRED = {
    "awaddr", "awvalid", "awready",
    "wdata", "wvalid", "wready",
    "bresp", "bvalid", "bready",
    "araddr", "arvalid", "arready",
    "rdata", "rresp", "rvalid", "rready",
}

AXI_FULL_EXTRA = {
    "awid", "awlen", "awsize", "awburst", "awlock", "awcache", "awqos",
    "arid", "arlen", "arsize", "arburst", "arlock", "arcache", "arqos",
    "rid", "rlast",
    "wlast", "bid",
}

AXIS_SIGNALS = {
    "tdata", "tvalid", "tready", "tlast", "tkeep", "tstrb",
    "tid", "tdest", "tuser",
}

AXIS_REQUIRED = {"tdata", "tvalid"}

# ---------------------------------------------------------------------------
# VHDL Entity Parser
# ---------------------------------------------------------------------------

def strip_vhdl_comments(line: str) -> str:
    idx = line.find("--")
    return line[:idx] if idx >= 0 else line


def parse_vhdl_entity(filepath: str) -> Tuple[str, List[VHDLGeneric], List[VHDLPort]]:
    """Parse a VHDL file and extract the first entity's generics and ports.

    Returns (entity_name, generics, ports).
    Only supports std_logic and std_logic_vector port types.
    """
    with open(filepath, "r") as f:
        raw = f.read()

    # Strip comments
    lines = []
    for line in raw.split("\n"):
        lines.append(strip_vhdl_comments(line))
    text = "\n".join(lines)

    # Find entity declaration
    ent_match = re.search(
        r'entity\s+(\w+)\s+is(.*?)end\s+(?:entity\s+)?\1\s*;',
        text, re.IGNORECASE | re.DOTALL,
    )
    if not ent_match:
        print(f"ERROR: No entity found in {filepath}", file=sys.stderr)
        sys.exit(1)

    entity_name = ent_match.group(1)
    body = ent_match.group(2)

    generics = _parse_generics(body)
    ports = _parse_ports(body)
    return entity_name, generics, ports


def _parse_generics(body: str) -> List[VHDLGeneric]:
    """Extract generics from the entity body."""
    gen_match = re.search(
        r'generic\s*\((.*?)\)\s*;', body, re.IGNORECASE | re.DOTALL,
    )
    if not gen_match:
        return []

    generics = []
    gen_text = gen_match.group(1)
    for decl in _split_declarations(gen_text):
        # e.g., "SYS_CLK_HZ : integer := 100_000_000"
        m = re.match(
            r'(\w+)\s*:\s*(\w+)(?:\s*:=\s*(.+))?\s*$',
            decl.strip(), re.IGNORECASE,
        )
        if m:
            generics.append(VHDLGeneric(
                name=m.group(1),
                vhdl_type=m.group(2).lower(),
                default=m.group(3).strip() if m.group(3) else None,
            ))
    return generics


def _parse_ports(body: str) -> List[VHDLPort]:
    """Extract ports from the entity body."""
    port_match = re.search(
        r'port\s*\((.*)\)\s*;', body, re.IGNORECASE | re.DOTALL,
    )
    if not port_match:
        return []

    ports = []
    port_text = port_match.group(1)
    for decl in _split_declarations(port_text):
        decl = decl.strip()
        if not decl:
            continue

        # Match: name : direction type[(width_expr)]
        m = re.match(
            r'(\w+)\s*:\s*(in|out|inout)\s+(std_logic_vector|std_logic)\b'
            r'(?:\s*\((.+?)\))?\s*$',
            decl, re.IGNORECASE,
        )
        if not m:
            # Check for unsupported type
            m2 = re.match(r'(\w+)\s*:\s*(in|out|inout)\s+(\w+)', decl, re.IGNORECASE)
            if m2:
                ptype = m2.group(3).lower()
                if ptype not in ("std_logic", "std_logic_vector"):
                    print(f"ERROR: Port '{m2.group(1)}' has unsupported type "
                          f"'{m2.group(3)}'. Top-level IP ports must be "
                          f"std_logic or std_logic_vector.", file=sys.stderr)
                    sys.exit(1)
            continue

        name = m.group(1)
        direction = m.group(2).lower()
        vhdl_type = m.group(3).lower()
        width_expr = m.group(4)

        width = None
        if width_expr:
            # Try to extract width from "N downto 0" or "N-1 downto 0"
            w_match = re.match(r'(\d+)\s+downto\s+0', width_expr.strip(), re.IGNORECASE)
            if w_match:
                width = int(w_match.group(1)) + 1
            else:
                # Try "N-1 downto 0" — can't resolve generic, record width_expr
                w_match2 = re.match(r'(.+)\s+downto\s+0', width_expr.strip(), re.IGNORECASE)
                if w_match2:
                    width_expr = width_expr.strip()

        ports.append(VHDLPort(
            name=name,
            direction=direction,
            vhdl_type=vhdl_type,
            width=width,
            width_expr=width_expr.strip() if width_expr else None,
        ))
    return ports


def _split_declarations(text: str) -> List[str]:
    """Split a semicolon-separated declaration list, handling nested parens."""
    decls = []
    depth = 0
    current = []
    for ch in text:
        if ch == '(':
            depth += 1
            current.append(ch)
        elif ch == ')':
            depth -= 1
            current.append(ch)
        elif ch == ';' and depth == 0:
            decls.append("".join(current))
            current = []
        else:
            current.append(ch)
    if current:
        remainder = "".join(current).strip()
        if remainder:
            decls.append(remainder)
    return decls


# ---------------------------------------------------------------------------
# Interface Detection
# ---------------------------------------------------------------------------

def detect_interfaces(ports: List[VHDLPort]) -> Tuple[List[DetectedInterface], List[VHDLPort]]:
    """Detect AXI-Lite, AXI-Stream, AXI-Full, clock, and reset interfaces.

    Returns (detected_interfaces, remaining_scalar_ports).
    """
    interfaces: List[DetectedInterface] = []
    claimed_ports: set = set()

    # --- Group ports by prefix for AXI detection ---
    axi_groups = _group_axi_ports(ports)

    for prefix, suffix_map in axi_groups.items():
        suffixes = set(suffix_map.keys())

        # Check AXI-Full first (superset of AXI-Lite)
        if suffixes & AXI_FULL_EXTRA and suffixes >= AXI_LITE_REQUIRED:
            iface = DetectedInterface(
                kind="axi_full",
                name=prefix.rstrip("_"),
                ports=[suffix_map[s].name for s in suffixes],
            )
            # Direction from awvalid
            if "awvalid" in suffix_map:
                iface.direction = "slave" if suffix_map["awvalid"].direction == "in" else "master"
            if "awaddr" in suffix_map:
                iface.addr_width = suffix_map["awaddr"].width
            interfaces.append(iface)
            claimed_ports.update(iface.ports)

        # Check AXI-Lite
        elif suffixes >= AXI_LITE_REQUIRED:
            iface = DetectedInterface(
                kind="axi_lite",
                name=prefix.rstrip("_"),
                ports=[suffix_map[s].name for s in suffixes if s in AXI_LITE_SIGNALS],
            )
            if "awvalid" in suffix_map:
                iface.direction = "slave" if suffix_map["awvalid"].direction == "in" else "master"
            if "awaddr" in suffix_map:
                iface.addr_width = suffix_map["awaddr"].width
            interfaces.append(iface)
            claimed_ports.update(iface.ports)

    # --- Check AXI-Stream ---
    axis_groups = _group_axis_ports(ports, claimed_ports)
    for prefix, suffix_map in axis_groups.items():
        suffixes = set(suffix_map.keys())
        if suffixes >= AXIS_REQUIRED:
            iface = DetectedInterface(
                kind="axi_stream",
                name=prefix.rstrip("_"),
                ports=[suffix_map[s].name for s in suffixes if s in AXIS_SIGNALS],
            )
            if "tvalid" in suffix_map:
                iface.direction = "master" if suffix_map["tvalid"].direction == "out" else "slave"
            interfaces.append(iface)
            claimed_ports.update(iface.ports)

    # --- Clock detection ---
    port_map = {p.name.lower(): p for p in ports}
    for p in ports:
        pname = p.name.lower()
        if p.name in claimed_ports:
            continue
        if p.vhdl_type == "std_logic" and p.direction == "in":
            if pname in ("clk", "aclk") or pname.endswith("_clk") or pname.endswith("_aclk"):
                interfaces.append(DetectedInterface(
                    kind="clock", name=p.name, ports=[p.name],
                ))
                claimed_ports.add(p.name)

    # --- Reset detection ---
    for p in ports:
        pname = p.name.lower()
        if p.name in claimed_ports:
            continue
        if p.vhdl_type == "std_logic" and p.direction == "in":
            is_reset = (pname.startswith("rst") or pname.endswith("_rst") or
                        pname.endswith("_rstn") or pname.endswith("_rst_n") or
                        pname == "rst_n" or pname == "aresetn" or
                        pname.endswith("_aresetn"))
            if is_reset:
                polarity = "ACTIVE_LOW" if ("n" in pname[-2:] or "aresetn" in pname) else "ACTIVE_HIGH"
                interfaces.append(DetectedInterface(
                    kind="reset", name=p.name, ports=[p.name],
                    reset_polarity=polarity,
                ))
                claimed_ports.add(p.name)

    # Remaining ports are scalar
    scalars = [p for p in ports if p.name not in claimed_ports]
    return interfaces, scalars


def _group_axi_ports(ports: List[VHDLPort]) -> Dict[str, Dict[str, VHDLPort]]:
    """Group ports by AXI prefix → suffix mapping."""
    groups: Dict[str, Dict[str, VHDLPort]] = {}
    all_axi = AXI_LITE_SIGNALS | AXI_FULL_EXTRA
    # Sort longest-first to avoid substring ambiguity (arvalid before rvalid)
    sorted_suffixes = sorted(all_axi, key=len, reverse=True)
    for p in ports:
        pname = p.name.lower()
        for suffix in sorted_suffixes:
            if pname.endswith("_" + suffix) or pname.endswith(suffix):
                # Extract prefix
                if pname.endswith("_" + suffix):
                    prefix = pname[: -(len(suffix) + 1) + 1]  # keep trailing _
                    prefix = pname[: len(pname) - len(suffix) - 1] + "_"
                else:
                    prefix = ""
                if prefix not in groups:
                    groups[prefix] = {}
                groups[prefix][suffix] = p
                break
    return groups


def _group_axis_ports(ports: List[VHDLPort], claimed: set) -> Dict[str, Dict[str, VHDLPort]]:
    """Group ports by AXI-Stream prefix → suffix mapping."""
    groups: Dict[str, Dict[str, VHDLPort]] = {}
    sorted_suffixes = sorted(AXIS_SIGNALS, key=len, reverse=True)
    for p in ports:
        if p.name in claimed:
            continue
        pname = p.name.lower()
        for suffix in sorted_suffixes:
            if pname.endswith("_" + suffix) or pname == suffix:
                if pname.endswith("_" + suffix):
                    prefix = pname[: len(pname) - len(suffix) - 1] + "_"
                else:
                    prefix = ""
                if prefix not in groups:
                    groups[prefix] = {}
                groups[prefix][suffix] = p
                break
    return groups


# ---------------------------------------------------------------------------
# Hash-based skip logic
# ---------------------------------------------------------------------------

def compute_source_hash(project_dir: str, socks_cfg: dict) -> str:
    """Compute SHA-256 hash of VHDL sources + ip config section."""
    h = hashlib.sha256()

    # Hash source files
    sources = socks_cfg.get("dut", {}).get("sources", [])
    for src in sorted(sources):
        src_path = os.path.join(project_dir, src)
        if os.path.isfile(src_path):
            with open(src_path, "rb") as f:
                h.update(f.read())
        h.update(src.encode())

    # Hash ip config
    ip_cfg = socks_cfg.get("ip", {})
    h.update(json.dumps(ip_cfg, sort_keys=True).encode())

    return h.hexdigest()


def check_hash(ip_dir: str, current_hash: str) -> bool:
    """Check if stored hash matches current. Returns True if unchanged."""
    hash_file = os.path.join(ip_dir, ".ip_hash")
    if not os.path.isfile(hash_file):
        return False
    with open(hash_file, "r") as f:
        stored = f.read().strip()
    return stored == current_hash


def store_hash(ip_dir: str, current_hash: str) -> None:
    """Store hash to file."""
    hash_file = os.path.join(ip_dir, ".ip_hash")
    with open(hash_file, "w") as f:
        f.write(current_hash + "\n")


# ---------------------------------------------------------------------------
# TCL Generation
# ---------------------------------------------------------------------------

def generic_to_ipx_type(vhdl_type: str) -> str:
    """Map VHDL generic type to IP-XACT parameter type."""
    mapping = {
        "integer": "long",
        "natural": "long",
        "positive": "long",
        "boolean": "bool",
        "real": "float",
        "string": "string",
    }
    return mapping.get(vhdl_type, "long")


def generate_package_tcl(
    ip_dir: str,
    project_dir: str,
    entity_name: str,
    sources: List[str],
    part: str,
    ip_cfg: dict,
    generics: List[VHDLGeneric],
    interfaces: List[DetectedInterface],
    scalars: List[VHDLPort],
) -> str:
    """Generate package_ip.tcl and return its path."""
    tcl_path = os.path.join(ip_dir, "package_ip.tcl")
    vlnv = f"{ip_cfg['vendor']}:{ip_cfg['library']}:{entity_name}:{ip_cfg['version']}"

    lines = [
        "# Auto-generated by SOCKS ip_package.py -- do not edit",
        f"# VLNV: {vlnv}",
        "",
        "set proj_dir [pwd]",
        f"create_project -in_memory -part {part}",
        "",
    ]

    # Add source files
    for src in sources:
        rel = os.path.relpath(os.path.join(project_dir, src), ip_dir)
        fname = os.path.basename(src)
        lines.append(f'add_files [file join $proj_dir {rel}]')
        lines.append(f'set_property file_type {{VHDL 2008}} [get_files {fname}]')
    lines.append("")

    # Package project
    lines.extend([
        f'ipx::package_project -root_dir $proj_dir -vendor {ip_cfg["vendor"]} '
        f'-library {ip_cfg["library"]} -taxonomy {ip_cfg["taxonomy"]}',
        "",
        "# Set core properties",
        f'set_property vendor              {ip_cfg["vendor"]}              [ipx::current_core]',
        f'set_property library             {ip_cfg["library"]}             [ipx::current_core]',
        f'set_property name                {entity_name}                   [ipx::current_core]',
        f'set_property version             {ip_cfg["version"]}             [ipx::current_core]',
        f'set_property display_name        {{{ip_cfg["display_name"]}}}    [ipx::current_core]',
        f'set_property description         {{{ip_cfg["description"]}}}     [ipx::current_core]',
        f'set_property vendor_display_name {{{ip_cfg["vendor_display_name"]}}} [ipx::current_core]',
        f'set_property company_url         {{{ip_cfg["company_url"]}}}     [ipx::current_core]',
        "",
    ])

    # Remove all auto-inferred bus interfaces so we can re-add explicitly
    lines.extend([
        "# Remove auto-inferred bus interfaces",
        "foreach bus [ipx::get_bus_interfaces -of_objects [ipx::current_core]] {",
        "    ipx::remove_bus_interface [get_property NAME $bus] [ipx::current_core]",
        "}",
        "",
    ])

    # Re-add detected interfaces
    axi_bus_names = []
    clock_ifaces = [i for i in interfaces if i.kind == "clock"]
    reset_ifaces = [i for i in interfaces if i.kind == "reset"]

    for iface in interfaces:
        if iface.kind == "axi_lite":
            _emit_axi_lite_tcl(lines, iface, entity_name)
            axi_bus_names.append(iface.name)
        elif iface.kind == "axi_full":
            _emit_axi_full_tcl(lines, iface, entity_name)
            axi_bus_names.append(iface.name)
        elif iface.kind == "axi_stream":
            _emit_axi_stream_tcl(lines, iface)
            axi_bus_names.append(iface.name)
        elif iface.kind == "clock":
            _emit_clock_tcl(lines, iface, axi_bus_names, reset_ifaces)
        elif iface.kind == "reset":
            _emit_reset_tcl(lines, iface)

    # Finalize
    lines.extend([
        "",
        "# Finalize",
        "ipx::create_xgui_files [ipx::current_core]",
        "ipx::update_checksums  [ipx::current_core]",
        "ipx::save_core         [ipx::current_core]",
        "",
        f'puts "IP packaged: {vlnv}"',
    ])

    with open(tcl_path, "w") as f:
        f.write("\n".join(lines) + "\n")
    return tcl_path


def _emit_axi_lite_tcl(lines: list, iface: DetectedInterface, entity_name: str):
    """Emit TCL for an AXI-Lite bus interface."""
    prefix = iface.name + "_" if iface.name else ""
    mode = "slave" if iface.direction == "slave" else "master"

    lines.extend([
        f"# AXI-Lite interface: {iface.name}",
        f"ipx::add_bus_interface {iface.name} [ipx::current_core]",
        f'set_property abstraction_type_vlnv xilinx.com:interface:aximm_rtl:1.0 '
        f'[ipx::get_bus_interfaces {iface.name} -of_objects [ipx::current_core]]',
        f'set_property bus_type_vlnv xilinx.com:interface:aximm:1.0 '
        f'[ipx::get_bus_interfaces {iface.name} -of_objects [ipx::current_core]]',
        f'set_property interface_mode {mode} '
        f'[ipx::get_bus_interfaces {iface.name} -of_objects [ipx::current_core]]',
        "",
    ])

    # Map port names to AXI signal names
    axi_port_map = {
        "awaddr": "AWADDR", "awvalid": "AWVALID", "awready": "AWREADY", "awprot": "AWPROT",
        "wdata": "WDATA", "wstrb": "WSTRB", "wvalid": "WVALID", "wready": "WREADY",
        "bresp": "BRESP", "bvalid": "BVALID", "bready": "BREADY",
        "araddr": "ARADDR", "arvalid": "ARVALID", "arready": "ARREADY", "arprot": "ARPROT",
        "rdata": "RDATA", "rresp": "RRESP", "rvalid": "RVALID", "rready": "RREADY",
    }

    for port_obj_name in iface.ports:
        pname_lower = port_obj_name.lower()
        for suffix, axi_signal in axi_port_map.items():
            if pname_lower.endswith(suffix):
                lines.append(
                    f"ipx::add_port_map {axi_signal} "
                    f"[ipx::get_bus_interfaces {iface.name} -of_objects [ipx::current_core]]"
                )
                lines.append(
                    f'set_property physical_name {port_obj_name} '
                    f'[ipx::get_port_maps {axi_signal} '
                    f'-of_objects [ipx::get_bus_interfaces {iface.name} '
                    f'-of_objects [ipx::current_core]]]'
                )
                break

    # Memory map for slave interfaces
    if mode == "slave" and iface.addr_width:
        addr_range = 2 ** iface.addr_width
        lines.extend([
            "",
            f"# Memory map for {iface.name}",
            f"ipx::add_memory_map {iface.name} [ipx::current_core]",
            f'set_property slave_memory_map_ref {iface.name} '
            f'[ipx::get_bus_interfaces {iface.name} -of_objects [ipx::current_core]]',
            f"ipx::add_address_block reg0 "
            f"[ipx::get_memory_maps {iface.name} -of_objects [ipx::current_core]]",
            f'set_property range {addr_range} '
            f'[ipx::get_address_blocks reg0 '
            f'-of_objects [ipx::get_memory_maps {iface.name} '
            f'-of_objects [ipx::current_core]]]',
        ])
    lines.append("")


def _emit_axi_full_tcl(lines: list, iface: DetectedInterface, entity_name: str):
    """Emit TCL for an AXI-Full bus interface."""
    prefix = iface.name + "_" if iface.name else ""
    mode = "slave" if iface.direction == "slave" else "master"

    lines.extend([
        f"# AXI-Full interface: {iface.name}",
        f"ipx::add_bus_interface {iface.name} [ipx::current_core]",
        f'set_property abstraction_type_vlnv xilinx.com:interface:aximm_rtl:1.0 '
        f'[ipx::get_bus_interfaces {iface.name} -of_objects [ipx::current_core]]',
        f'set_property bus_type_vlnv xilinx.com:interface:aximm:1.0 '
        f'[ipx::get_bus_interfaces {iface.name} -of_objects [ipx::current_core]]',
        f'set_property interface_mode {mode} '
        f'[ipx::get_bus_interfaces {iface.name} -of_objects [ipx::current_core]]',
        "",
    ])

    # Full AXI port map (superset of AXI-Lite)
    axi_port_map = {
        "awid": "AWID", "awaddr": "AWADDR", "awlen": "AWLEN", "awsize": "AWSIZE",
        "awburst": "AWBURST", "awlock": "AWLOCK", "awcache": "AWCACHE",
        "awprot": "AWPROT", "awqos": "AWQOS", "awvalid": "AWVALID", "awready": "AWREADY",
        "wdata": "WDATA", "wstrb": "WSTRB", "wlast": "WLAST",
        "wvalid": "WVALID", "wready": "WREADY",
        "bid": "BID", "bresp": "BRESP", "bvalid": "BVALID", "bready": "BREADY",
        "arid": "ARID", "araddr": "ARADDR", "arlen": "ARLEN", "arsize": "ARSIZE",
        "arburst": "ARBURST", "arlock": "ARLOCK", "arcache": "ARCACHE",
        "arprot": "ARPROT", "arqos": "ARQOS", "arvalid": "ARVALID", "arready": "ARREADY",
        "rid": "RID", "rdata": "RDATA", "rresp": "RRESP", "rlast": "RLAST",
        "rvalid": "RVALID", "rready": "RREADY",
    }

    for port_obj_name in iface.ports:
        pname_lower = port_obj_name.lower()
        for suffix, axi_signal in axi_port_map.items():
            if pname_lower.endswith(suffix):
                lines.append(
                    f"ipx::add_port_map {axi_signal} "
                    f"[ipx::get_bus_interfaces {iface.name} -of_objects [ipx::current_core]]"
                )
                lines.append(
                    f'set_property physical_name {port_obj_name} '
                    f'[ipx::get_port_maps {axi_signal} '
                    f'-of_objects [ipx::get_bus_interfaces {iface.name} '
                    f'-of_objects [ipx::current_core]]]'
                )
                break

    if mode == "slave" and iface.addr_width:
        addr_range = 2 ** iface.addr_width
        lines.extend([
            "",
            f"ipx::add_memory_map {iface.name} [ipx::current_core]",
            f'set_property slave_memory_map_ref {iface.name} '
            f'[ipx::get_bus_interfaces {iface.name} -of_objects [ipx::current_core]]',
            f"ipx::add_address_block reg0 "
            f"[ipx::get_memory_maps {iface.name} -of_objects [ipx::current_core]]",
            f'set_property range {addr_range} '
            f'[ipx::get_address_blocks reg0 '
            f'-of_objects [ipx::get_memory_maps {iface.name} '
            f'-of_objects [ipx::current_core]]]',
        ])
    lines.append("")


def _emit_axi_stream_tcl(lines: list, iface: DetectedInterface):
    """Emit TCL for an AXI-Stream bus interface."""
    mode = "master" if iface.direction == "master" else "slave"

    lines.extend([
        f"# AXI-Stream interface: {iface.name}",
        f"ipx::add_bus_interface {iface.name} [ipx::current_core]",
        f'set_property abstraction_type_vlnv xilinx.com:interface:axis_rtl:1.0 '
        f'[ipx::get_bus_interfaces {iface.name} -of_objects [ipx::current_core]]',
        f'set_property bus_type_vlnv xilinx.com:interface:axis:1.0 '
        f'[ipx::get_bus_interfaces {iface.name} -of_objects [ipx::current_core]]',
        f'set_property interface_mode {mode} '
        f'[ipx::get_bus_interfaces {iface.name} -of_objects [ipx::current_core]]',
        "",
    ])

    axis_port_map = {
        "tdata": "TDATA", "tvalid": "TVALID", "tready": "TREADY",
        "tlast": "TLAST", "tkeep": "TKEEP", "tstrb": "TSTRB",
        "tid": "TID", "tdest": "TDEST", "tuser": "TUSER",
    }

    for port_obj_name in iface.ports:
        pname_lower = port_obj_name.lower()
        for suffix, axis_signal in axis_port_map.items():
            if pname_lower.endswith(suffix):
                lines.append(
                    f"ipx::add_port_map {axis_signal} "
                    f"[ipx::get_bus_interfaces {iface.name} -of_objects [ipx::current_core]]"
                )
                lines.append(
                    f'set_property physical_name {port_obj_name} '
                    f'[ipx::get_port_maps {axis_signal} '
                    f'-of_objects [ipx::get_bus_interfaces {iface.name} '
                    f'-of_objects [ipx::current_core]]]'
                )
                break
    lines.append("")


def _emit_clock_tcl(lines: list, iface: DetectedInterface,
                     axi_bus_names: List[str], reset_ifaces: List[DetectedInterface]):
    """Emit TCL for a clock interface with ASSOCIATED_BUSIF and ASSOCIATED_RESET."""
    port_name = iface.ports[0]
    lines.extend([
        f"# Clock interface: {port_name}",
        f"ipx::add_bus_interface {port_name} [ipx::current_core]",
        f'set_property abstraction_type_vlnv xilinx.com:signal:clock_rtl:1.0 '
        f'[ipx::get_bus_interfaces {port_name} -of_objects [ipx::current_core]]',
        f'set_property bus_type_vlnv xilinx.com:signal:clock:1.0 '
        f'[ipx::get_bus_interfaces {port_name} -of_objects [ipx::current_core]]',
        f'set_property interface_mode slave '
        f'[ipx::get_bus_interfaces {port_name} -of_objects [ipx::current_core]]',
        f"ipx::add_port_map CLK "
        f"[ipx::get_bus_interfaces {port_name} -of_objects [ipx::current_core]]",
        f'set_property physical_name {port_name} '
        f'[ipx::get_port_maps CLK '
        f'-of_objects [ipx::get_bus_interfaces {port_name} '
        f'-of_objects [ipx::current_core]]]',
    ])

    # ASSOCIATED_BUSIF — list all AXI bus interfaces
    if axi_bus_names:
        busif_str = ":".join(axi_bus_names)
        lines.extend([
            f"ipx::add_bus_parameter ASSOCIATED_BUSIF "
            f"[ipx::get_bus_interfaces {port_name} -of_objects [ipx::current_core]]",
            f'set_property value {busif_str} '
            f'[ipx::get_bus_parameters ASSOCIATED_BUSIF '
            f'-of_objects [ipx::get_bus_interfaces {port_name} '
            f'-of_objects [ipx::current_core]]]',
        ])

    # ASSOCIATED_RESET
    if reset_ifaces:
        reset_name = reset_ifaces[0].ports[0]
        lines.extend([
            f"ipx::add_bus_parameter ASSOCIATED_RESET "
            f"[ipx::get_bus_interfaces {port_name} -of_objects [ipx::current_core]]",
            f'set_property value {reset_name} '
            f'[ipx::get_bus_parameters ASSOCIATED_RESET '
            f'-of_objects [ipx::get_bus_interfaces {port_name} '
            f'-of_objects [ipx::current_core]]]',
        ])
    lines.append("")


def _emit_reset_tcl(lines: list, iface: DetectedInterface):
    """Emit TCL for a reset interface."""
    port_name = iface.ports[0]
    polarity = iface.reset_polarity or "ACTIVE_LOW"

    lines.extend([
        f"# Reset interface: {port_name}",
        f"ipx::add_bus_interface {port_name} [ipx::current_core]",
        f'set_property abstraction_type_vlnv xilinx.com:signal:reset_rtl:1.0 '
        f'[ipx::get_bus_interfaces {port_name} -of_objects [ipx::current_core]]',
        f'set_property bus_type_vlnv xilinx.com:signal:reset:1.0 '
        f'[ipx::get_bus_interfaces {port_name} -of_objects [ipx::current_core]]',
        f'set_property interface_mode slave '
        f'[ipx::get_bus_interfaces {port_name} -of_objects [ipx::current_core]]',
        f"ipx::add_port_map RST "
        f"[ipx::get_bus_interfaces {port_name} -of_objects [ipx::current_core]]",
        f'set_property physical_name {port_name} '
        f'[ipx::get_port_maps RST '
        f'-of_objects [ipx::get_bus_interfaces {port_name} '
        f'-of_objects [ipx::current_core]]]',
        f"ipx::add_bus_parameter POLARITY "
        f"[ipx::get_bus_interfaces {port_name} -of_objects [ipx::current_core]]",
        f'set_property value {polarity} '
        f'[ipx::get_bus_parameters POLARITY '
        f'-of_objects [ipx::get_bus_interfaces {port_name} '
        f'-of_objects [ipx::current_core]]]',
        "",
    ])


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Stage 21: IP Packaging")
    parser.add_argument("--project-dir", required=True, help="Module project directory")
    parser.add_argument("--settings", default=None, help="Vivado settings64.sh path")
    args = parser.parse_args()

    project_dir = os.path.abspath(args.project_dir)
    print_header("Stage 21: IP Packaging")

    # Load socks.json
    socks_path = os.path.join(project_dir, "socks.json")
    if not os.path.isfile(socks_path):
        print(f"ERROR: {socks_path} not found.", file=sys.stderr)
        sys.exit(1)

    with open(socks_path, "r") as f:
        socks_cfg = json.load(f)

    # System scope: skip
    scope = socks_cfg.get("scope", "module")
    if scope == "system":
        print("  System-scope project — IP packaging not applicable. Skipping.")
        sys.exit(0)

    # Validate ip section
    ip_cfg = socks_cfg.get("ip")
    if not ip_cfg:
        print("ERROR: socks.json is missing the mandatory 'ip' section.", file=sys.stderr)
        print("", file=sys.stderr)
        print("Add an 'ip' section to socks.json:", file=sys.stderr)
        print('  "ip": {', file=sys.stderr)
        print('      "vendor": "socks",', file=sys.stderr)
        print('      "library": "socks",', file=sys.stderr)
        print('      "version": "1.0",', file=sys.stderr)
        print('      "display_name": "My IP Name",', file=sys.stderr)
        print('      "description": "Description of the IP",', file=sys.stderr)
        print('      "vendor_display_name": "SOCKS",', file=sys.stderr)
        print('      "company_url": "",', file=sys.stderr)
        print('      "taxonomy": "/SOCKS"', file=sys.stderr)
        print("  }", file=sys.stderr)
        sys.exit(1)

    required_ip_fields = ["vendor", "library", "version", "display_name",
                          "description", "vendor_display_name", "taxonomy"]
    for fld in required_ip_fields:
        if fld not in ip_cfg:
            print(f"ERROR: ip section missing required field '{fld}'.", file=sys.stderr)
            sys.exit(1)

    dut_cfg = socks_cfg.get("dut", {})
    entity = dut_cfg.get("entity")
    sources = dut_cfg.get("sources", [])
    part = socks_cfg.get("board", {}).get("part")

    if not entity:
        print("ERROR: socks.json dut.entity is required.", file=sys.stderr)
        sys.exit(1)
    if not sources:
        print("ERROR: socks.json dut.sources is required.", file=sys.stderr)
        sys.exit(1)
    if not part:
        print("ERROR: socks.json board.part is required.", file=sys.stderr)
        sys.exit(1)

    # Output directory
    ip_dir = os.path.join(project_dir, "build", "ip")
    os.makedirs(ip_dir, exist_ok=True)

    # Hash-based skip
    current_hash = compute_source_hash(project_dir, socks_cfg)
    component_xml = os.path.join(ip_dir, "component.xml")
    if check_hash(ip_dir, current_hash) and os.path.isfile(component_xml):
        print(f"  Sources unchanged (hash: {current_hash[:12]}...). Skipping Vivado.")
        print(f"  [{pass_str()}] IP packaging: unchanged, skipping")
        sys.exit(0)

    print(f"  Entity:  {entity}")
    print(f"  Part:    {part}")
    print(f"  Sources: {len(sources)} files")
    print(f"  Hash:    {current_hash[:12]}...")
    print()

    # Parse VHDL entity
    top_src = os.path.join(project_dir, sources[0])
    if not os.path.isfile(top_src):
        print(f"ERROR: Top source file not found: {top_src}", file=sys.stderr)
        sys.exit(1)

    entity_name, generics, ports = parse_vhdl_entity(top_src)
    print(f"  Parsed entity: {entity_name}")
    print(f"    Generics: {len(generics)}")
    print(f"    Ports:    {len(ports)}")

    # Detect interfaces
    interfaces, scalars = detect_interfaces(ports)
    print()
    print("  Detected interfaces:")
    for iface in interfaces:
        detail = ""
        if iface.direction:
            detail = f" ({iface.direction})"
        if iface.addr_width:
            detail += f" addr_width={iface.addr_width}"
        print(f"    {iface.kind:12s}  {iface.name}{detail}")
    if scalars:
        print(f"    {'scalar':12s}  {len(scalars)} ports: {', '.join(p.name for p in scalars)}")
    print()

    # Generate TCL
    tcl_path = generate_package_tcl(
        ip_dir, project_dir, entity_name, sources, part,
        ip_cfg, generics, interfaces, scalars,
    )
    print(f"  Generated: {os.path.relpath(tcl_path, project_dir)}")

    # Find Vivado
    settings = args.settings
    if not settings:
        settings = find_vivado_settings()
    if not settings:
        print("ERROR: Cannot find Vivado settings64.sh. "
              "Pass --settings or set VIVADO_SETTINGS.", file=sys.stderr)
        sys.exit(1)

    # Run Vivado batch
    print(f"  Running Vivado batch...")
    log_path = os.path.join(ip_dir, "package_ip.log")
    jou_path = os.path.join(ip_dir, "package_ip.jou")

    cmd = (
        f'bash -c "source {settings} && '
        f'cd {ip_dir} && '
        f'vivado -mode batch -source {tcl_path} '
        f'-log {log_path} -journal {jou_path} -nojournal 2>&1"'
    )
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)

    if result.returncode != 0:
        print(f"  [{fail_str()}] Vivado IP packaging failed (exit {result.returncode})")
        print()
        # Show last 30 lines of output
        out_lines = (result.stdout + result.stderr).strip().split("\n")
        for line in out_lines[-30:]:
            print(f"    {line}")
        sys.exit(1)

    # Verify output
    if not os.path.isfile(component_xml):
        print(f"  [{fail_str()}] component.xml not generated")
        sys.exit(1)

    # Store hash
    store_hash(ip_dir, current_hash)

    # Summary
    vlnv = f"{ip_cfg['vendor']}:{ip_cfg['library']}:{entity_name}:{ip_cfg['version']}"
    print(f"  [{pass_str()}] IP packaged successfully")
    print(f"    VLNV:          {vlnv}")
    print(f"    component.xml: {os.path.relpath(component_xml, project_dir)}")
    axi_count = sum(1 for i in interfaces if i.kind in ("axi_lite", "axi_full", "axi_stream"))
    print(f"    Bus interfaces: {axi_count}")
    print(f"    Scalar ports:   {len(scalars)}")
    print_separator()


if __name__ == "__main__":
    main()
