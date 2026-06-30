#!/usr/bin/env python3
"""
assert_intf_integrity.py -- Static netlist interface-integrity gate.

The STATIC half of the IP-Boundary Handshake Equivalence Gate
(references/hil.md). Opens a routed checkpoint and asserts that each declared
critical data-path interface is still wired source -> sink in the IMPLEMENTED
netlist.

Catches the failure mode a bit-exact sim and the dynamic (ILA-cadence) half of
the gate cannot: a BD edit or ILA insertion that silently DETACHES an AXIS
interface member. `connect_bd_net` on an interface member pin (the BD 41-1306
class) reroutes that member to the new target (e.g. a debug probe) and strips it
from the source->sink interface net -- so the data path goes to the logic
analyzer instead of its real sink, while synthesis, routing, and the golden sim
all still pass.

Unlike the dynamic check, this adds NO probes, so it cannot itself perturb the
interface it measures. Run it on EVERY build that adds/modifies RTL or BD, or
inserts an ILA.

Provenance: gps_design txm8l4 plan-09 -- the slow offload->DMAC AXIS interface
was severed by `txm8l4_debug_tap` member taps; offload m_axis_tvalid drove only
the ILA, DMAC s_axis valid/ready tied off -> slow ring 0 MiB. Found only by a
routed-netlist trace; no warning, no sim failure flagged it (2026-06).

Allowlist JSON (project-supplied -- critical data-path interfaces only):

    {
      "critical_interfaces": [
        {
          "name": "slow_offload -> decim_dma (AXIS)",
          "asserts": [
            {"driver": "*decim_data_offload*m_axis_tvalid*",
             "sink":   "*rx_decim_dma*"},
            {"driver": "*rx_decim_dma*s_axis*ready*",
             "sink":   "*decim_data_offload*i_data_offload*"}
          ]
        }
      ]
    }

Each assert means: in the routed netlist, the net driven by `driver` must fan
out to a leaf load whose pin path matches `sink`. A missing driver net, or a
load list that does not reach `sink` (e.g. it only reaches an ILA), is a FAIL.

IMPORTANT -- `sink` is the DESTINATION INSTANCE hierarchy, NOT the boundary port
name. After implementation the AXIS boundary pins (`s_axis_valid`, `m_axis_tready`,
...) are optimized into the consuming logic, so the leaf-load names reflect that
logic (e.g. `.../i_data_mover/beat_counter...`), not the port. Matching a literal
port name (`*s_axis*valid*`) yields a FALSE POSITIVE on a correctly-wired build.
Use the destination IP/instance (`*rx_decim_dma*`, `*decim_data_offload*i_data_offload*`)
-- the question is "does the source reach the destination IP at all," which a
sever (source reaches only the ILA) fails and a correct wire passes. The `driver`
boundary net survives because it is a MARK_DEBUG/boundary net.

Usage:
    assert_intf_integrity.py --checkpoint <routed.dcp> --interfaces <allowlist.json>
    assert_intf_integrity.py --build-dir <dir>         --interfaces <allowlist.json>

Exit: 0 = all critical interfaces intact; 1 = a sever detected (FAIL the build);
      2 = usage / tool / no-checkpoint error.
"""

import argparse
import glob
import json
import os
import shutil
import subprocess
import sys
import tempfile


def find_vivado():
    exe = shutil.which("vivado")
    if exe:
        return exe
    for base in sorted(glob.glob("/tools/Xilinx/Vivado/*/bin/vivado"), reverse=True):
        return base
    for base in sorted(glob.glob("/opt/Xilinx/Vivado/*/bin/vivado"), reverse=True):
        return base
    return None


def resolve_checkpoint(args):
    if args.checkpoint:
        return args.checkpoint
    # Best-effort discovery under a build dir (SOCKS or ADI layouts).
    pats = [
        os.path.join(args.build_dir, "**", "impl_1", "*_routed.dcp"),
        os.path.join(args.build_dir, "**", "impl_1", "system_top_routed.dcp"),
        os.path.join(args.build_dir, "**", "*_routed.dcp"),
    ]
    for p in pats:
        hits = sorted(glob.glob(p, recursive=True), key=os.path.getmtime, reverse=True)
        if hits:
            return hits[0]
    return None


def emit_tcl(dcp, asserts, tcl_path):
    """Write a read-only Vivado tcl that opens the DCP and checks each assert."""
    lines = [
        "set ::failures 0",
        "open_checkpoint {%s}" % dcp,
        "proc _intf_assert {name driver sink} {",
        "  set nets [get_nets -hier -quiet -filter \"NAME =~ $driver\"]",
        "  if {[llength $nets] == 0} {",
        "    puts \"INTF_FAIL\\t$name\\tno net matches driver '$driver'\"",
        "    incr ::failures; return",
        "  }",
        "  set hit 0",
        "  foreach n $nets {",
        "    set lds [get_pins -leaf -quiet -of_objects $n -filter {DIRECTION==IN}]",
        "    foreach l $lds {",
        "      if {[string match $sink $l]} { set hit 1 }",
        "    }",
        "  }",
        "  if {$hit} {",
        "    puts \"INTF_PASS\\t$name\\t$driver -> $sink\"",
        "  } else {",
        "    set first [lindex $nets 0]",
        "    set lds [get_pins -leaf -quiet -of_objects $first -filter {DIRECTION==IN}]",
        "    puts \"INTF_FAIL\\t$name\\tdriver '$driver' reaches no '$sink' (loads: $lds)\"",
        "    incr ::failures",
        "  }",
        "}",
    ]
    for name, drv, snk in asserts:
        # tcl-escape braces/quotes minimally; globs here are simple.
        lines.append("_intf_assert {%s} {%s} {%s}" % (name, drv, snk))
    lines += [
        "puts \"INTF_INTEGRITY_FAILURES\\t$::failures\"",
        "close_project",
        "exit 0",
    ]
    with open(tcl_path, "w") as f:
        f.write("\n".join(lines) + "\n")


def main():
    ap = argparse.ArgumentParser(description="Static netlist interface-integrity gate")
    ap.add_argument("--checkpoint", help="Path to routed .dcp")
    ap.add_argument("--build-dir", default=".", help="Build dir to discover the routed .dcp under")
    ap.add_argument("--interfaces", required=True, help="Critical-interface allowlist JSON")
    ap.add_argument("--vivado", help="vivado executable (default: PATH / common install dirs)")
    args = ap.parse_args()

    with open(args.interfaces) as f:
        spec = json.load(f)
    asserts = []
    for ci in spec.get("critical_interfaces", []):
        name = ci.get("name", "?")
        for a in ci.get("asserts", []):
            asserts.append((name, a["driver"], a["sink"]))
    if not asserts:
        print("interface-integrity: no critical interfaces declared -- nothing to check")
        return 0

    dcp = resolve_checkpoint(args)
    if not dcp or not os.path.exists(dcp):
        print("ERROR: routed checkpoint not found (use --checkpoint or --build-dir)", file=sys.stderr)
        return 2
    vivado = args.vivado or find_vivado()
    if not vivado:
        print("ERROR: vivado not found (use --vivado)", file=sys.stderr)
        return 2

    print(f"interface-integrity: checking {len(asserts)} assertion(s) against {os.path.basename(dcp)}")
    workdir = tempfile.mkdtemp(prefix="intf_integrity_")
    tcl = os.path.join(workdir, "assert_intf_integrity.tcl")
    emit_tcl(os.path.abspath(dcp), asserts, tcl)

    cmd = [vivado, "-mode", "batch", "-nojournal", "-nolog", "-notrace", "-source", tcl]
    proc = subprocess.run(cmd, cwd=workdir, capture_output=True, text=True)
    out = proc.stdout + proc.stderr

    failures, checked = 0, 0
    for line in out.splitlines():
        if line.startswith("INTF_PASS\t"):
            checked += 1
            print("  PASS  " + line.split("\t", 1)[1])
        elif line.startswith("INTF_FAIL\t"):
            checked += 1
            print("  FAIL  " + line.split("\t", 1)[1])
        elif line.startswith("INTF_INTEGRITY_FAILURES\t"):
            failures = int(line.split("\t")[1])

    if checked == 0:
        print("ERROR: Vivado produced no assertion results -- checkpoint open likely failed", file=sys.stderr)
        print(out[-2000:], file=sys.stderr)
        return 2
    if failures:
        print(f"\nINTERFACE-INTEGRITY GATE: FAIL ({failures} severed) -- a critical data-path "
              f"interface is detached in the routed netlist (BD-edit / ILA-insertion regression).")
        return 1
    print(f"\nINTERFACE-INTEGRITY GATE: PASS ({checked} critical interface assertions intact)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
