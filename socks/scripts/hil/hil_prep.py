#!/usr/bin/env python3
"""
HIL Prep -- Auto-generate hil.json and ila_trigger_plan.json.

Reads VHDL sources and docs/TEST-INTENT.md to produce HIL artifacts.
Only generates files that don't already exist (preserves hand-written files).

Note: sw/hil_test_main.c is Claude-authored (Stage 16 guidance), not
template-generated. See references/hil.md § "Stage 16: Firmware Authoring Guide".

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

    # Note: sw/hil_test_main.c is Claude-authored (Stage 16 guidance),
    # not template-generated. See references/hil.md § "Stage 16: Firmware
    # Authoring Guide".

    print(f"\n  HIL Prep: all artifacts ready")
    return True


# ---------------------------------------------------------------------------
# CLI Entry Point
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="HIL Prep: auto-generate hil.json and ila_trigger_plan.json")
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
