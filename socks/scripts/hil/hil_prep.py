#!/usr/bin/env python3
"""
HIL Prep -- Auto-generate hil.json, sw/hil_test_main.c, ila_trigger_plan.json.

Reads VHDL sources and docs/TEST-INTENT.md to produce HIL artifacts.
Only generates files that don't already exist (preserves hand-written files).

Called from hil_project.py before Vivado project creation.

Usage:
    python scripts/hil/hil_prep.py --project-dir . --top usart_frame_ctrl
    python scripts/hil/hil_prep.py --project-dir . --top usart_frame_ctrl --part xc7z020clg484-1
"""

import argparse
import glob
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from socks_lib import print_header, print_separator, pass_str, fail_str, yellow


# ---------------------------------------------------------------------------
# VHDL Parsing Functions (reuse patterns from architecture.py)
# ---------------------------------------------------------------------------

def extract_entity_name(lines):
    """Extract entity name from VHDL source lines."""
    for line in lines:
        m = re.match(r'\s*entity\s+(\w+)\s+is', line, re.IGNORECASE)
        if m:
            return m.group(1)
    return None


def extract_ports(lines):
    """Extract port declarations: (name, direction, type_str).

    Returns list of (name, 'in'|'out'|'inout', type_string).
    """
    ports = []
    in_port = False
    for line in lines:
        code = _strip_comments(line).strip()
        code_lower = code.lower()

        if re.match(r'^\s*port\s*\(', code_lower):
            in_port = True
            continue

        if in_port:
            if code.strip() == ");":
                in_port = False
                continue

            m = re.match(
                r'\s*(\w+)\s*:\s*(in|out|inout)\s+(.+?)[\s;]*$',
                code, re.IGNORECASE)
            if m:
                ports.append((m.group(1), m.group(2).lower(),
                              m.group(3).strip().rstrip(";")))

    return ports


def extract_fsm_types(lines):
    """Extract FSM state type declarations (multi-line aware).

    Returns list of (type_name, [state_names]).
    """
    fsm_types = []
    text = "".join(lines)
    # Match: type <name> is (<states>);
    pattern = re.compile(
        r'type\s+(\w+)\s+is\s*\(([^)]+)\)',
        re.IGNORECASE | re.DOTALL)
    for m in pattern.finditer(text):
        type_name = m.group(1)
        states_str = m.group(2)
        states = [s.strip() for s in states_str.split(",") if s.strip()]
        if states and any(s.upper().startswith("ST_") for s in states):
            fsm_types.append((type_name, states))
    return fsm_types


def extract_port_width(type_str):
    """Extract bit width from a VHDL type string.

    'std_logic' -> 1
    'std_logic_vector(2 downto 0)' -> 3
    'std_logic_vector(6 downto 0)' -> 7
    """
    if "std_logic_vector" in type_str.lower():
        m = re.search(r'\(\s*(\d+)\s+downto\s+(\d+)\s*\)', type_str)
        if m:
            return int(m.group(1)) - int(m.group(2)) + 1
    if type_str.strip().lower() == "std_logic":
        return 1
    return None


def _strip_comments(line):
    """Strip VHDL comments from a line."""
    idx = line.find("--")
    return line[:idx] if idx >= 0 else line


# ---------------------------------------------------------------------------
# Port Classification
# ---------------------------------------------------------------------------

def classify_ports(ports):
    """Classify ports into categories.

    Returns dict with keys: infra, axi, monitor, loopback_candidates.
    Each value is a list of (name, direction, type_str) tuples.
    """
    result = {
        "infra": [],
        "axi": [],
        "monitor": [],
        "loopback_candidates": [],
    }

    for name, direction, type_str in ports:
        name_lower = name.lower()
        if name_lower in ("clk", "rst_n", "rst", "reset", "aresetn"):
            result["infra"].append((name, direction, type_str))
        elif name_lower.startswith("s_axi_"):
            result["axi"].append((name, direction, type_str))
        elif name_lower.startswith("mon_") or name_lower == "irq":
            result["monitor"].append((name, direction, type_str))
        else:
            # Skip infra-like ports
            if name_lower not in ("clk", "rst_n"):
                result["loopback_candidates"].append((name, direction, type_str))

    return result


def infer_loopback(loopback_candidates):
    """Infer loopback pairs from candidate ports.

    Returns (pairs, note) where pairs is list of [out_name, in_name]
    and note is a string if inference failed.
    """
    outputs = [(n, d, t) for n, d, t in loopback_candidates if d == "out"]
    inputs = [(n, d, t) for n, d, t in loopback_candidates if d == "in"]

    # Case 1: exactly 1 output + 1 input
    if len(outputs) == 1 and len(inputs) == 1:
        return [[outputs[0][0], inputs[0][0]]], None

    # Case 2: name heuristic -- strip tx/rx, out/in roots
    pairs = []
    used_inputs = set()
    for out_name, _, _ in outputs:
        # Try matching: strip common prefixes/suffixes
        out_root = (out_name.lower()
                    .replace("tx", "").replace("out", "")
                    .replace("_o", "").strip("_"))
        for in_name, _, _ in inputs:
            if in_name in used_inputs:
                continue
            in_root = (in_name.lower()
                       .replace("rx", "").replace("in", "")
                       .replace("_i", "").strip("_"))
            if out_root and in_root and out_root == in_root:
                pairs.append([out_name, in_name])
                used_inputs.add(in_name)
                break

    if pairs:
        return pairs, None

    # Case 3: can't infer
    return [], "Could not infer loopback pairs from ports. Add manually."


# ---------------------------------------------------------------------------
# hil.json Generation
# ---------------------------------------------------------------------------

def generate_hil_json(project_dir, top, part="xc7z020clg484-1"):
    """Generate hil.json from VHDL sources.

    Returns the hil_config dict, or None on error.
    """
    hil_json_path = os.path.join(project_dir, "hil.json")
    if os.path.isfile(hil_json_path):
        print(f"  hil.json already exists -- skipping generation")
        return None  # caller should load existing

    # Find and parse top entity
    vhd_files = sorted(glob.glob(os.path.join(project_dir, "src", "*.vhd")))
    if not vhd_files:
        print(f"  ERROR: No VHDL files in src/")
        return None

    top_file = None
    top_lines = None
    all_entities = []

    for vhd in vhd_files:
        with open(vhd, "r") as f:
            lines = f.readlines()
        entity = extract_entity_name(lines)
        if entity:
            all_entities.append((entity, vhd))
            if entity.lower() == top.lower():
                top_file = vhd
                top_lines = lines

    if top_lines is None:
        candidates = [e for e, _ in all_entities]
        if len(candidates) == 0:
            print(f"  ERROR: No VHDL entities found in src/")
        else:
            print(f"  ERROR: --top '{top}' did not match exactly one VHDL entity. "
                  f"Candidates: {', '.join(candidates)}.")
        return None

    # Extract ports
    ports = extract_ports(top_lines)
    classified = classify_ports(ports)

    # Build monitor lists
    monitor_names = [n for n, d, t in classified["monitor"]]
    monitor_prefixes = []
    if any(n.startswith("mon_") for n, _, _ in classified["monitor"]):
        monitor_prefixes.append("mon_")
    if any(n == "irq" for n, _, _ in classified["monitor"]):
        monitor_prefixes.append("irq")

    # Infer loopback
    loopback_pairs, loopback_note = infer_loopback(classified["loopback_candidates"])

    # Build sources list (all .vhd files)
    sources = [os.path.relpath(v, project_dir) for v in vhd_files]

    # Find firmware sources
    driver_sources = []
    test_src = None
    for ext in ["*.c", "*.h"]:
        for f in sorted(glob.glob(os.path.join(project_dir, "sw", ext))):
            rel = os.path.relpath(f, project_dir)
            basename = os.path.basename(f)
            if basename == "hil_test_main.c":
                test_src = rel
            else:
                driver_sources.append(rel)

    # Build config
    hil_config = {
        "dut": {
            "entity": top,
            "sources": sources,
        },
        "board": {
            "part": part,
            "preset": "microzed_ps7_preset.tcl",
            "serial_vid": "10c4",
            "serial_pid": "ea60",
            "serial_fallback": "/dev/ttyUSB1",
        },
        "axi": {
            "base_address": "0x43C00000",
            "range": "4K",
            "fclk_mhz": 100,
        },
        "wiring": {
            "loopback": loopback_pairs,
            "monitor": {
                "prefixes": monitor_prefixes,
                "ports": monitor_names,
            },
        },
        "firmware": {
            "test_src": test_src or "sw/hil_test_main.c",
            "driver_sources": driver_sources,
            "pass_marker": "HIL_PASS",
            "fail_marker": "HIL_FAIL",
            "timeout_s": 30,
        },
    }

    if loopback_note:
        hil_config["wiring"]["_loopback_note"] = loopback_note

    # Write
    with open(hil_json_path, "w") as f:
        json.dump(hil_config, f, indent=2)
        f.write("\n")

    print(f"  {pass_str()}: Generated hil.json")
    print(f"    Entity:   {top}")
    print(f"    Sources:  {len(sources)} files")
    print(f"    Loopback: {loopback_pairs or '(none -- add manually)'}")
    print(f"    Monitor:  {monitor_names}")
    if loopback_note:
        print(f"    NOTE: {loopback_note}")

    return hil_config


# ---------------------------------------------------------------------------
# ILA Trigger Plan Generation
# ---------------------------------------------------------------------------

def _parse_test_intent_capture_plan(test_intent_path):
    """Parse the Capture Plan table from TEST-INTENT.md.

    Returns list of dicts with keys: trigger_signal, trigger_value, covers, description.
    """
    captures = []
    with open(test_intent_path, "r") as f:
        lines = f.readlines()

    in_table = False
    header_seen = False
    for line in lines:
        stripped = line.strip()

        # Look for the capture plan table header
        if "Trigger signal" in stripped and "Trigger value" in stripped:
            in_table = True
            header_seen = False
            continue

        if in_table:
            # Skip separator row
            if stripped.startswith("|---") or stripped.startswith("| ---"):
                header_seen = True
                continue

            if not stripped.startswith("|"):
                in_table = False
                continue

            if not header_seen:
                continue

            cells = [c.strip() for c in stripped.split("|")]
            # Remove empty first/last from leading/trailing |
            cells = [c for c in cells if c]

            if len(cells) >= 5:
                # # | Trigger signal | Trigger value | Covers | Description
                trigger_signal = cells[1].strip()
                trigger_value_raw = cells[2].strip()
                covers = cells[3].strip()
                description = cells[4].strip()

                # Extract just the binary value (strip parenthetical state name)
                trigger_value = trigger_value_raw.split("(")[0].strip()
                # Strip any Verilog-style width prefix like 3'b
                trigger_value = re.sub(r"^\d+'[bB]", "", trigger_value)

                captures.append({
                    "trigger_signal": trigger_signal,
                    "trigger_value": trigger_value,
                    "covers": covers,
                    "description": description,
                })

    return captures


def _parse_test_intent_fsm_encodings(test_intent_path):
    """Parse FSM Encodings table from TEST-INTENT.md.

    Returns dict mapping state_type_name -> {state_name: encoding_value}.
    """
    encodings = {}
    with open(test_intent_path, "r") as f:
        lines = f.readlines()

    in_table = False
    header_seen = False
    for line in lines:
        stripped = line.strip()

        if "State type" in stripped and "Encoding" in stripped:
            in_table = True
            header_seen = False
            continue

        if in_table:
            if stripped.startswith("|---") or stripped.startswith("| ---"):
                header_seen = True
                continue

            if not stripped.startswith("|"):
                in_table = False
                continue

            if not header_seen:
                continue

            cells = [c.strip() for c in stripped.split("|")]
            cells = [c for c in cells if c]

            if len(cells) >= 3:
                type_name = cells[0].strip()
                states_str = cells[1].strip()
                encoding_info = cells[2].strip()

                # Parse states like "ST_TXF_IDLE=0, ST_TXF_SYNC=1, ..."
                state_map = {}
                for part in states_str.split(","):
                    part = part.strip()
                    if "=" in part:
                        sname, sval = part.split("=", 1)
                        state_map[sname.strip()] = int(sval.strip())

                if state_map:
                    encodings[type_name] = state_map

    return encodings


def generate_ila_trigger_plan(project_dir, hil_config):
    """Generate ila_trigger_plan.json from TEST-INTENT.md.

    Returns True on success, False on error.
    """
    build_dir = os.path.join(project_dir, "build", "hil")
    plan_path = os.path.join(build_dir, "ila_trigger_plan.json")

    if os.path.isfile(plan_path):
        # Validate existing plan doesn't use old format
        with open(plan_path) as f:
            plan = json.load(f)
        for cap in plan.get("captures", []):
            if "probe" in cap or "value" in cap:
                print(f"  ERROR: ila_trigger_plan.json uses deprecated probe/value fields. "
                      f"Update to trigger_probe/trigger_value/trigger_compare.")
                return False
        print(f"  ila_trigger_plan.json already exists -- skipping generation")
        return True

    test_intent = os.path.join(project_dir, "docs", "TEST-INTENT.md")
    if not os.path.isfile(test_intent):
        print(f"  ERROR: TEST-INTENT.md not found in docs/. "
              f"Run test discovery (references/test_discovery.md) first.")
        return False

    # Parse capture plan from TEST-INTENT.md
    captures_raw = _parse_test_intent_capture_plan(test_intent)
    if not captures_raw:
        print(f"  WARNING: No capture plan table found in TEST-INTENT.md")
        print(f"  Creating empty trigger plan -- add captures manually")
        captures_raw = []

    # Build port width map from VHDL
    port_widths = {}
    vhd_files = sorted(glob.glob(os.path.join(project_dir, "src", "*.vhd")))
    top_entity = hil_config["dut"]["entity"]
    for vhd in vhd_files:
        with open(vhd, "r") as f:
            lines = f.readlines()
        entity = extract_entity_name(lines)
        if entity and entity.lower() == top_entity.lower():
            ports = extract_ports(lines)
            for name, direction, type_str in ports:
                width = extract_port_width(type_str)
                if width is not None:
                    port_widths[name] = width
            break

    # Generate captures
    captures = []
    for i, cap in enumerate(captures_raw):
        sig = cap["trigger_signal"]
        val = cap["trigger_value"]

        # Derive probe name: signal_name -> signal_name_s (gen_hil_top.tcl convention)
        # Strip _s suffix if already present to avoid double suffix
        base_sig = sig.rstrip("_s") if sig.endswith("_s") else sig
        probe_name = f"{base_sig}_s"

        # Validate width
        port_width = port_widths.get(base_sig)
        if port_width is not None and len(val) != port_width:
            print(f"  WARNING: Trigger value '{val}' width ({len(val)}) "
                  f"!= port '{base_sig}' width ({port_width})")

        # Generate a descriptive name from the signal and description
        name = f"cap_{i+1}_{base_sig}_{val}"
        name = re.sub(r'[^a-zA-Z0-9_]', '_', name)

        captures.append({
            "name": name,
            "trigger_probe": probe_name,
            "trigger_value": val,
            "trigger_compare": "eq",
            "output": f"ila_{name}.csv",
            "description": cap["description"],
        })

    # Write
    os.makedirs(build_dir, exist_ok=True)
    plan = {"captures": captures}
    with open(plan_path, "w") as f:
        json.dump(plan, f, indent=2)
        f.write("\n")

    print(f"  {pass_str()}: Generated ila_trigger_plan.json ({len(captures)} captures)")
    for cap in captures:
        print(f"    {cap['name']}: {cap['trigger_probe']} == {cap['trigger_value']}")

    return True


# ---------------------------------------------------------------------------
# Firmware Generation
# ---------------------------------------------------------------------------

def _parse_driver_header(project_dir):
    """Parse sw/*.h to find driver struct, init function, and key functions.

    Returns dict with keys: header, struct_type, init_func, tx_funcs, rx_funcs,
    status_funcs, enable_funcs, all_funcs.
    """
    info = {
        "header": None,
        "struct_type": None,
        "init_func": None,
        "all_funcs": [],
    }

    headers = sorted(glob.glob(os.path.join(project_dir, "sw", "*.h")))
    for h in headers:
        basename = os.path.basename(h)
        if basename == "hil_test_main.h":
            continue

        with open(h, "r") as f:
            content = f.read()

        # Find struct typedef: } xxx_t;
        m = re.search(r'\}\s*(\w+_t)\s*;', content)
        if m:
            info["header"] = basename
            info["struct_type"] = m.group(1)

        # Find all function prototypes
        func_pat = re.compile(
            r'(?:int|void|uint32_t)\s+(\w+)\s*\(',
            re.MULTILINE)
        for fm in func_pat.finditer(content):
            func_name = fm.group(1)
            if func_name.startswith("usart_reg_") or func_name.startswith("static"):
                continue
            info["all_funcs"].append(func_name)

            if "_init" in func_name:
                info["init_func"] = func_name

    return info


def _parse_test_intent_firmware(test_intent_path):
    """Parse Firmware Structure section from TEST-INTENT.md.

    Returns dict with keys from the firmware structure section.
    """
    fw = {
        "init_params": "",
        "test_pattern": "",
        "loopback": "",
        "verification": "",
        "debug_mode": "",
        "iteration_normal": 5,
        "iteration_debug": 10000,
    }

    with open(test_intent_path, "r") as f:
        lines = f.readlines()

    in_fw_section = False
    current_key = None

    for line in lines:
        stripped = line.strip()

        if stripped == "## Firmware Structure":
            in_fw_section = True
            continue

        if in_fw_section and stripped.startswith("## "):
            break

        if not in_fw_section:
            continue

        # Parse key-value pairs like "- **Init params:** {value}"
        m = re.match(r'-\s+\*\*(.+?)\*\*:?\s*(.*)', stripped)
        if m:
            key_raw = m.group(1).lower().strip(":").strip()
            value = m.group(2).strip()

            if "init" in key_raw:
                fw["init_params"] = value
            elif "pattern" in key_raw:
                fw["test_pattern"] = value
            elif "loopback" in key_raw:
                fw["loopback"] = value
            elif "verif" in key_raw:
                fw["verification"] = value
            elif "debug" in key_raw:
                fw["debug_mode"] = value
            elif "iteration" in key_raw:
                # Parse "normal=5, debug=10000"
                nm = re.search(r'normal\s*=\s*(\d+)', value)
                dm = re.search(r'debug\s*=\s*(\d+)', value)
                if nm:
                    fw["iteration_normal"] = int(nm.group(1))
                if dm:
                    fw["iteration_debug"] = int(dm.group(1))

    return fw


def generate_hil_test_main(project_dir, hil_config):
    """Generate sw/hil_test_main.c from TEST-INTENT.md and driver API.

    Returns True on success, False on error.
    """
    test_main_path = os.path.join(project_dir, "sw", "hil_test_main.c")
    if os.path.isfile(test_main_path):
        print(f"  sw/hil_test_main.c already exists -- skipping generation")
        return True

    test_intent = os.path.join(project_dir, "docs", "TEST-INTENT.md")
    if not os.path.isfile(test_intent):
        print(f"  ERROR: TEST-INTENT.md not found in docs/. "
              f"Run test discovery (references/test_discovery.md) first.")
        return False

    # Parse driver info
    drv = _parse_driver_header(project_dir)
    if drv["header"] is None:
        print(f"  WARNING: No driver header found in sw/ -- generating skeleton with TODOs")

    # Parse firmware structure from TEST-INTENT
    fw = _parse_test_intent_firmware(test_intent)

    # Get config values
    dut_entity = hil_config["dut"]["entity"]
    base_addr = hil_config["axi"]["base_address"]
    fclk_mhz = hil_config["axi"].get("fclk_mhz", 100)
    clk_hz = fclk_mhz * 1000000

    # Extract init params from TEST-INTENT
    # Try to parse baud, sync_word, num_words from init_params string
    baud = 1000000
    sync_word = "0xDEADBEEF"
    num_words = 16

    init_str = fw["init_params"]
    bm = re.search(r'baud\s*[=:]\s*(\d+)', init_str, re.IGNORECASE)
    if bm:
        baud = int(bm.group(1))
    sm = re.search(r'sync[_\s]*word\s*[=:]\s*(0x[0-9A-Fa-f]+)', init_str, re.IGNORECASE)
    if sm:
        sync_word = sm.group(1).upper().replace("0X", "0x")
    nm = re.search(r'num[_\s]*words\s*[=:]\s*(\d+)', init_str, re.IGNORECASE)
    if nm:
        num_words = int(nm.group(1))

    num_tests = fw["iteration_normal"]
    num_tests_dbg = fw["iteration_debug"]

    # Derive function names
    header_include = drv["header"] or f"{dut_entity}.h"
    struct_type = drv["struct_type"] or f"{dut_entity}_t"
    init_func = drv["init_func"] or f"{dut_entity}_init"
    var_name = dut_entity.split("_")[0] if "_" in dut_entity else "dev"

    # Find specific functions from driver
    all_funcs = drv["all_funcs"]
    load_tx = _find_func(all_funcs, ["load_tx", "set_tx", "write_tx"]) or f"// TODO: {dut_entity}_load_tx_frame"
    read_rx = _find_func(all_funcs, ["read_rx", "get_rx"]) or f"// TODO: {dut_entity}_read_rx_frame"
    tx_enable = _find_func(all_funcs, ["tx_enable"]) or f"// TODO: {dut_entity}_tx_enable"
    tx_disable = _find_func(all_funcs, ["tx_disable"]) or f"// TODO: {dut_entity}_tx_disable"
    rx_enable = _find_func(all_funcs, ["rx_enable"]) or f"// TODO: {dut_entity}_rx_enable"
    rx_disable = _find_func(all_funcs, ["rx_disable"]) or f"// TODO: {dut_entity}_rx_disable"
    get_status = _find_func(all_funcs, ["get_status"]) or f"// TODO: {dut_entity}_get_status"
    clear_status = _find_func(all_funcs, ["clear_status"]) or f"// TODO: {dut_entity}_clear_status"
    get_tx_count = _find_func(all_funcs, ["get_tx_count", "tx_count"]) or None
    get_rx_count = _find_func(all_funcs, ["get_rx_count", "rx_count"]) or None
    irq_enable = _find_func(all_funcs, ["irq_enable"]) or None
    wait_rx = _find_func(all_funcs, ["wait_rx"]) or None

    # Determine if we have a polling wait function or need timeout helper
    need_timeout_helper = wait_rx is None

    # Generate the C source
    os.makedirs(os.path.dirname(test_main_path), exist_ok=True)

    lines = []
    lines.append(f'/**')
    lines.append(f' * @file    hil_test_main.c')
    lines.append(f' * @brief   HIL loopback test for {dut_entity}')
    lines.append(f' *')
    lines.append(f' * Auto-generated by hil_prep.py from docs/TEST-INTENT.md.')
    lines.append(f' * Sends {num_tests} frames through loopback, verifies each.')
    lines.append(f' * Prints HIL_PASS or HIL_FAIL over PS UART1.')
    lines.append(f' */')
    lines.append(f'')
    lines.append(f'#include <stdio.h>')
    lines.append(f'#include <stdint.h>')
    lines.append(f'#include "xtime_l.h"')
    lines.append(f'#include "xparameters.h"')
    lines.append(f'#include "{header_include}"')
    lines.append(f'')

    # Base address define
    macro_prefix = dut_entity.upper()
    lines.append(f'#ifndef XPAR_{macro_prefix}_0_BASEADDR')
    lines.append(f'#define XPAR_{macro_prefix}_0_BASEADDR {base_addr}U')
    lines.append(f'#endif')
    lines.append(f'')

    # Constants
    lines.append(f'#define TEST_BASE     XPAR_{macro_prefix}_0_BASEADDR')
    lines.append(f'#define SYS_CLK_HZ   {clk_hz}U')
    lines.append(f'#define BAUD          {baud}U')
    lines.append(f'#define SYNC          {sync_word}U')
    lines.append(f'#define NUM_WORDS     {num_words}U')
    lines.append(f'#define NUM_TESTS     {num_tests}U')
    lines.append(f'#define NUM_TESTS_DBG {num_tests_dbg}U')
    lines.append(f'#define TIMEOUT_SEC   2U')
    lines.append(f'#define INTER_FRAME_US 5000U')
    lines.append(f'')

    # Debug mode block
    lines.append(f'#ifdef HIL_DEBUG_MODE')
    lines.append(f'#define UART1_BASE       0xE0001000U')
    lines.append(f'#define UART1_SR         (*(volatile uint32_t *)(UART1_BASE + 0x2CU))')
    lines.append(f'#define UART1_FIFO       (*(volatile uint32_t *)(UART1_BASE + 0x30U))')
    lines.append(f'#define UART_SR_RXEMPTY  (1U << 1)')
    lines.append(f'')
    lines.append(f'static void wait_for_go(void) {{')
    lines.append(f'    while (UART1_SR & UART_SR_RXEMPTY) {{}}')
    lines.append(f'    (void)UART1_FIFO;')
    lines.append(f'}}')
    lines.append(f'#endif')
    lines.append(f'')

    # XTime counter
    lines.append(f'#define COUNTS_PER_SEC (COUNTS_PER_SECOND)')
    lines.append(f'')

    # Timeout wait helper
    if need_timeout_helper:
        status_valid_bit = _find_status_bit(project_dir, "rx_frame_valid")
        status_crc_bit = _find_status_bit(project_dir, "rx_crc_err")
        valid_macro = status_valid_bit or "0x02U"
        crc_macro = status_crc_bit or "0x04U"

        lines.append(f'static int wait_rx_frame_timeout(const {struct_type} *dev,')
        lines.append(f'                                 uint32_t *status_out,')
        lines.append(f'                                 uint32_t timeout_sec)')
        lines.append(f'{{')
        lines.append(f'    XTime t_start, t_now;')
        lines.append(f'    XTime_GetTime(&t_start);')
        lines.append(f'    XTime deadline = t_start + (XTime)timeout_sec * COUNTS_PER_SEC;')
        lines.append(f'')
        lines.append(f'    while (1) {{')
        lines.append(f'        uint32_t st = {get_status}(dev);')
        lines.append(f'        if (st & {valid_macro}) {{')
        lines.append(f'            {clear_status}(dev, {valid_macro} | {crc_macro});')
        lines.append(f'            *status_out = st;')
        lines.append(f'            return 0;')
        lines.append(f'        }}')
        lines.append(f'        XTime_GetTime(&t_now);')
        lines.append(f'        if (t_now >= deadline) {{')
        lines.append(f'            *status_out = st;')
        lines.append(f'            return -1;')
        lines.append(f'        }}')
        lines.append(f'    }}')
        lines.append(f'}}')
        lines.append(f'')

    # busy_wait_us
    lines.append(f'static void busy_wait_us(uint32_t us)')
    lines.append(f'{{')
    lines.append(f'    XTime t_start, t_now;')
    lines.append(f'    XTime_GetTime(&t_start);')
    lines.append(f'    XTime deadline = t_start + (XTime)us * (COUNTS_PER_SEC / 1000000U);')
    lines.append(f'    do {{')
    lines.append(f'        XTime_GetTime(&t_now);')
    lines.append(f'    }} while (t_now < deadline);')
    lines.append(f'}}')
    lines.append(f'')

    # main()
    lines.append(f'int main(void)')
    lines.append(f'{{')
    lines.append(f'    {struct_type} {var_name};')
    lines.append(f'    uint32_t tx_buf[NUM_WORDS];')
    lines.append(f'    uint32_t rx_buf[NUM_WORDS];')
    lines.append(f'    int pass_count = 0;')
    lines.append(f'')
    lines.append(f'#ifdef HIL_DEBUG_MODE')
    lines.append(f'    uint32_t num_tests = NUM_TESTS_DBG;')
    lines.append(f'#else')
    lines.append(f'    uint32_t num_tests = NUM_TESTS;')
    lines.append(f'#endif')
    lines.append(f'')
    lines.append(f'    printf("\\r\\n=== {dut_entity} HIL Loopback Test ===\\r\\n");')
    lines.append(f'    printf("Base: 0x%08lX  Baud: %lu  Tests: %lu\\r\\n",')
    lines.append(f'           (unsigned long)TEST_BASE, (unsigned long)BAUD,')
    lines.append(f'           (unsigned long)num_tests);')
    lines.append(f'')

    # Init call
    lines.append(f'    if ({init_func}(&{var_name}, TEST_BASE, NUM_WORDS,')
    lines.append(f'                     SYS_CLK_HZ, BAUD, SYNC) != 0) {{')
    lines.append(f'        printf("ERROR: {init_func} failed\\r\\n");')
    lines.append(f'        printf("HIL_FAIL\\r\\n");')
    lines.append(f'        return 1;')
    lines.append(f'    }}')
    lines.append(f'')

    # Debug mode IRQ enable
    if irq_enable:
        lines.append(f'#ifdef HIL_DEBUG_MODE')
        lines.append(f'    {irq_enable}(&{var_name}, 0x01);')
        lines.append(f'#endif')
        lines.append(f'')

    # Test loop
    lines.append(f'    for (uint32_t t = 0; t < num_tests; t++) {{')
    lines.append(f'#ifdef HIL_DEBUG_MODE')
    lines.append(f'        wait_for_go();')
    lines.append(f'#endif')
    lines.append(f'        /* Generate test pattern */')
    lines.append(f'        for (uint32_t i = 0; i < NUM_WORDS; i++) {{')
    lines.append(f'            tx_buf[i] = ((t + 1) << 24) | (i << 16) | (0xA500U + t * NUM_WORDS + i);')
    lines.append(f'        }}')
    lines.append(f'')

    # Load TX data
    if load_tx.startswith("//"):
        lines.append(f'        {load_tx}(&{var_name}, tx_buf);')
    else:
        lines.append(f'        {load_tx}(&{var_name}, tx_buf);')

    lines.append(f'')
    lines.append(f'        /* Enable RX first, then TX */')
    if rx_enable.startswith("//"):
        lines.append(f'        {rx_enable}(&{var_name});')
    else:
        lines.append(f'        {rx_enable}(&{var_name});')
    if tx_enable.startswith("//"):
        lines.append(f'        {tx_enable}(&{var_name});')
    else:
        lines.append(f'        {tx_enable}(&{var_name});')

    lines.append(f'')
    lines.append(f'        /* Wait for RX frame with timeout */')
    lines.append(f'        uint32_t status;')

    if need_timeout_helper:
        lines.append(f'        int rc = wait_rx_frame_timeout(&{var_name}, &status, TIMEOUT_SEC);')
    else:
        lines.append(f'        status = {wait_rx}(&{var_name});')
        lines.append(f'        int rc = 0;')

    lines.append(f'')
    lines.append(f'        /* Disable both */')
    if tx_disable.startswith("//"):
        lines.append(f'        {tx_disable}(&{var_name});')
    else:
        lines.append(f'        {tx_disable}(&{var_name});')
    if rx_disable.startswith("//"):
        lines.append(f'        {rx_disable}(&{var_name});')
    else:
        lines.append(f'        {rx_disable}(&{var_name});')

    lines.append(f'')
    lines.append(f'        if (rc != 0) {{')
    lines.append(f'            printf("Test %lu: TIMEOUT\\r\\n", (unsigned long)(t + 1));')
    lines.append(f'            continue;')
    lines.append(f'        }}')
    lines.append(f'')

    # CRC error check - find CRC error status bit
    crc_bit = _find_status_bit(project_dir, "rx_crc_err")
    if crc_bit:
        lines.append(f'        if (status & {crc_bit}) {{')
        lines.append(f'            printf("Test %lu: CRC ERROR\\r\\n", (unsigned long)(t + 1));')
        lines.append(f'            continue;')
        lines.append(f'        }}')
        lines.append(f'')

    # Read and compare
    if read_rx.startswith("//"):
        lines.append(f'        {read_rx}(&{var_name}, rx_buf);')
    else:
        lines.append(f'        {read_rx}(&{var_name}, rx_buf);')
    lines.append(f'        int match = 1;')
    lines.append(f'        for (uint32_t i = 0; i < NUM_WORDS; i++) {{')
    lines.append(f'            if (rx_buf[i] != tx_buf[i]) {{')
    lines.append(f'                printf("Test %lu: MISMATCH word[%lu] tx=0x%08lX rx=0x%08lX\\r\\n",')
    lines.append(f'                       (unsigned long)(t + 1), (unsigned long)i,')
    lines.append(f'                       (unsigned long)tx_buf[i], (unsigned long)rx_buf[i]);')
    lines.append(f'                match = 0;')
    lines.append(f'                break;')
    lines.append(f'            }}')
    lines.append(f'        }}')
    lines.append(f'')

    # Print result
    count_str = ""
    if get_tx_count and get_rx_count:
        count_str = (f'                   (unsigned long){get_tx_count}(&{var_name}),\n'
                     f'                   (unsigned long){get_rx_count}(&{var_name})')
        lines.append(f'        if (match) {{')
        lines.append(f'            printf("Test %lu: OK  (tx_cnt=%lu rx_cnt=%lu)\\r\\n",')
        lines.append(f'                   (unsigned long)(t + 1),')
        lines.append(f'{count_str});')
    else:
        lines.append(f'        if (match) {{')
        lines.append(f'            printf("Test %lu: OK\\r\\n", (unsigned long)(t + 1));')
    lines.append(f'            pass_count++;')
    lines.append(f'        }}')
    lines.append(f'')

    # Debug mode inter-frame gap
    lines.append(f'#ifdef HIL_DEBUG_MODE')
    lines.append(f'        busy_wait_us(INTER_FRAME_US);')
    lines.append(f'#endif')
    lines.append(f'    }}')
    lines.append(f'')

    # Final result
    lines.append(f'    printf("Results: %d/%lu passed\\r\\n", pass_count, (unsigned long)num_tests);')
    lines.append(f'')
    lines.append(f'    if (pass_count == (int)num_tests) {{')
    lines.append(f'        printf("HIL_PASS\\r\\n");')
    lines.append(f'    }} else {{')
    lines.append(f'        printf("HIL_FAIL\\r\\n");')
    lines.append(f'    }}')
    lines.append(f'')
    lines.append(f'    return 0;')
    lines.append(f'}}')

    with open(test_main_path, "w") as f:
        f.write("\n".join(lines) + "\n")

    print(f"  {pass_str()}: Generated sw/hil_test_main.c")
    print(f"    Driver header: {header_include}")
    print(f"    Struct type:   {struct_type}")
    print(f"    Init function: {init_func}")

    return True


def _find_func(func_list, patterns):
    """Find a function name matching any of the given substrings.

    Patterns are tried in priority order -- earlier patterns preferred.
    """
    for pat in patterns:
        for func in func_list:
            if pat in func.lower():
                return func
    return None


def _find_status_bit(project_dir, bit_name):
    """Search driver headers for a status bit define matching bit_name.

    Returns the macro name (e.g. 'USART_STATUS_RX_FRAME_VALID') or None.
    """
    headers = sorted(glob.glob(os.path.join(project_dir, "sw", "*.h")))
    for h in headers:
        with open(h, "r") as f:
            for line in f:
                if bit_name.upper().replace("_", "") in line.upper().replace("_", ""):
                    m = re.match(r'#define\s+(\w+)', line)
                    if m:
                        return m.group(1)
    return None


# ---------------------------------------------------------------------------
# Public Entry Point
# ---------------------------------------------------------------------------

def maybe_generate_artifacts(project_dir, top, part="xc7z020clg484-1"):
    """Generate any missing HIL artifacts. Called from hil_project.py.

    Returns True if all artifacts are present (existing or generated),
    False if generation failed.
    """
    print(f"\n  HIL Prep: checking artifacts for --top {top}")

    # Step 1: Generate hil.json if missing
    hil_json_path = os.path.join(project_dir, "hil.json")
    generated_config = generate_hil_json(project_dir, top, part)

    # Load the config (either existing or just-generated)
    if os.path.isfile(hil_json_path):
        with open(hil_json_path) as f:
            hil_config = json.load(f)
    else:
        print(f"  ERROR: hil.json not found after prep. "
              f"Create it manually or run test discovery first.")
        return False

    # Step 2: Generate ila_trigger_plan.json if missing
    if not generate_ila_trigger_plan(project_dir, hil_config):
        return False

    # Step 3: Generate sw/hil_test_main.c if missing
    if not generate_hil_test_main(project_dir, hil_config):
        return False

    print(f"\n  HIL Prep: all artifacts ready")
    return True


# ---------------------------------------------------------------------------
# CLI Entry Point
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="HIL Prep: auto-generate hil.json, ila_trigger_plan.json, hil_test_main.c")
    parser.add_argument("--project-dir", required=True, help="Project root")
    parser.add_argument("--top", required=True,
                        help="Top-level VHDL entity name")
    parser.add_argument("--part", default="xc7z020clg484-1",
                        help="FPGA part number")
    args = parser.parse_args()

    project_dir = os.path.abspath(args.project_dir)
    print_header("HIL Prep: Artifact Generation")

    ok = maybe_generate_artifacts(project_dir, args.top, args.part)

    print_separator()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
