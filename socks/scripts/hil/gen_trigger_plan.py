#!/usr/bin/env python3
"""
Generate ila_trigger_plan.json from VCD simulation data.

Bridges simulation and HIL by reading the VCD (ground truth for signal values)
and generating a trigger plan where every trigger value was actually observed
in simulation -- guaranteed to fire on hardware.

Requires:
- VCD file from Stage 7 (build/sim/*.vcd)
- Signal map (tb/vcd_signal_map.json)
- hil_top.vhd from Stage 14 (build/hil/hil_top.vhd) for MARK_DEBUG signal list

Usage:
    python scripts/hil/gen_trigger_plan.py --project-dir . --vcd build/sim/foo.vcd
    python scripts/hil/gen_trigger_plan.py --project-dir . --vcd build/sim/foo.vcd --max-captures 4
    python scripts/hil/gen_trigger_plan.py --project-dir . --vcd build/sim/foo.vcd --signals mon_tx_state locked
"""

import argparse
import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from validate_trigger_plan import parse_mark_debug_signals, validate_trigger_plan

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from socks_lib import parse_vcd_header, stream_vcd, pass_str, fail_str, yellow
from vcd_verify import build_id_map, SignalTracker


def collect_observed_values(vcd_path, id_map, signal_widths):
    """Stream VCD and collect observed values per mapped signal.

    Returns dict: logical_name -> {
        "observed": {int_value: transition_count},
        "first_seen": {int_value: timestamp},
        "width": int,
    }
    """
    signals, _ = parse_vcd_header(vcd_path)
    tracker = SignalTracker(id_map, signals)

    stats = {}
    for name in id_map:
        width = signal_widths.get(name, 1)
        stats[name] = {
            "observed": {},
            "first_seen": {},
            "width": width,
        }

    for ts, changes in stream_vcd(vcd_path):
        changed = tracker.update(changes)
        for name in changed:
            if name not in stats:
                continue
            val = tracker.get(name)
            s = stats[name]
            if val not in s["observed"]:
                s["observed"][val] = 0
                s["first_seen"][val] = ts
            s["observed"][val] += 1

    return stats


def select_triggers(stats, max_captures, signal_filter=None):
    """Select trigger points from observed VCD values.

    Returns list of (logical_name, value, width, count, first_ts) tuples,
    sorted by first_seen timestamp.
    """
    candidates = []

    for name, s in stats.items():
        if signal_filter and name not in signal_filter:
            continue

        width = s["width"]
        observed = s["observed"]
        first_seen = s["first_seen"]

        if not observed:
            continue

        if width == 1:
            # Scalar: only trigger on value 1 (rising edge event)
            if 1 in observed:
                candidates.append((name, 1, width, observed[1], first_seen[1]))
        else:
            # Multi-bit: one trigger per non-zero observed value
            for val, count in observed.items():
                if val == 0:
                    continue  # skip idle
                candidates.append((name, val, width, count, first_seen[val]))

    # Sort by first-seen timestamp (natural state sequence)
    candidates.sort(key=lambda x: x[4])

    if len(candidates) <= max_captures:
        return candidates

    # Over cap limit: prioritize signals with fewer observed values (FSM > counter),
    # then prefer rare states within a signal
    signal_value_counts = {}
    for name, _, _, _, _ in candidates:
        signal_value_counts[name] = signal_value_counts.get(name, 0) + 1

    candidates.sort(key=lambda x: (signal_value_counts[x[0]], x[3]))
    candidates = candidates[:max_captures]

    # Re-sort selected by first-seen for natural ordering
    candidates.sort(key=lambda x: x[4])

    return candidates


def generate_plan(triggers, probe_suffix="_s"):
    """Generate ila_trigger_plan.json captures from selected triggers."""
    captures = []
    for i, (name, val, width, count, _) in enumerate(triggers):
        probe_name = f"{name}{probe_suffix}"
        # Zero-pad binary value to signal width
        val_bin = format(val, f'0{width}b')
        cap_name = f"cap_{i+1}_{name}_{val_bin}"

        captures.append({
            "name": cap_name,
            "trigger_probe": probe_name,
            "trigger_value": val_bin,
            "trigger_compare": "eq",
            "output": f"ila_{cap_name}.csv",
            "description": f"VCD-observed: {name} == {val_bin} ({count} transitions in sim)",
        })

    return {"captures": captures}


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate ILA trigger plan from VCD simulation data")
    parser.add_argument("--project-dir", required=True, help="Project root")
    parser.add_argument("--vcd", default=None,
                        help="Path to VCD file (default: auto-detect from build/sim/)")
    parser.add_argument("--max-captures", type=int, default=8,
                        help="Maximum number of ILA captures (default: 8)")
    parser.add_argument("--signals", nargs="*", default=None,
                        help="Only generate triggers for these signal names")
    args = parser.parse_args()

    project_dir = os.path.abspath(args.project_dir)

    # Resolve hil_top.vhd
    hil_top_path = os.path.join(project_dir, "build", "hil", "hil_top.vhd")
    if not os.path.isfile(hil_top_path):
        print(f"  {fail_str()}: hil_top.vhd not found at {hil_top_path}")
        print(f"  Run Stage 14 (hil_project.py) first to generate hil_top.vhd")
        return 1

    # Resolve VCD
    if args.vcd:
        vcd_path = args.vcd if os.path.isabs(args.vcd) else os.path.join(project_dir, args.vcd)
    else:
        vcd_files = sorted(glob.glob(os.path.join(project_dir, "build", "sim", "*.vcd")))
        if not vcd_files:
            print(f"  {fail_str()}: No VCD files found in build/sim/")
            print(f"  Run Stage 7 (xsim) first to generate simulation VCD")
            return 1
        vcd_path = vcd_files[-1]  # most recent

    if not os.path.isfile(vcd_path):
        print(f"  {fail_str()}: VCD file not found: {vcd_path}")
        return 1

    # Resolve signal map
    signal_map_path = os.path.join(project_dir, "tb", "vcd_signal_map.json")
    if not os.path.isfile(signal_map_path):
        print(f"  {fail_str()}: Signal map not found at {signal_map_path}")
        print(f"  Create tb/vcd_signal_map.json mapping logical names to VCD paths")
        return 1

    with open(signal_map_path) as f:
        signal_map = json.load(f)

    # Parse MARK_DEBUG signals from hil_top.vhd
    debug_signals = parse_mark_debug_signals(hil_top_path)
    if not debug_signals:
        print(f"  {fail_str()}: No MARK_DEBUG signals found in hil_top.vhd")
        return 1

    print(f"  Project:       {project_dir}")
    print(f"  VCD:           {os.path.basename(vcd_path)} ({os.path.getsize(vcd_path) / 1024 / 1024:.1f} MB)")
    print(f"  MARK_DEBUG:    {len(debug_signals)} signals")
    print(f"  Signal map:    {len(signal_map)} entries")
    print(f"  Max captures:  {args.max_captures}")

    # Build VCD ID map from signal map
    vcd_signals, _ = parse_vcd_header(vcd_path)
    id_map = build_id_map(vcd_signals, signal_map)

    if not id_map:
        print(f"\n  {fail_str()}: No signal map entries matched VCD paths")
        print(f"  Check that tb/vcd_signal_map.json paths match VCD hierarchy")
        return 1

    # Intersect with MARK_DEBUG signals: strip _s suffix to find signal map names
    debug_to_logical = {}  # probe_name_s -> logical_name
    for probe_name, width in debug_signals.items():
        logical = probe_name[:-2] if probe_name.endswith("_s") else probe_name
        if logical in id_map:
            debug_to_logical[probe_name] = logical

    if not debug_to_logical:
        print(f"\n  {fail_str()}: No MARK_DEBUG signals have matching signal map entries")
        print(f"  MARK_DEBUG signals: {', '.join(sorted(debug_signals.keys()))}")
        print(f"  Signal map names:   {', '.join(sorted(id_map.keys()))}")
        return 1

    # Build width map using logical names
    signal_widths = {}
    for probe_name, logical in debug_to_logical.items():
        signal_widths[logical] = debug_signals[probe_name]

    # Filter id_map to only MARK_DEBUG signals
    filtered_id_map = {name: id_map[name] for name in debug_to_logical.values() if name in id_map}

    print(f"  Matched:       {len(filtered_id_map)} MARK_DEBUG signals in VCD")
    for logical in sorted(filtered_id_map.keys()):
        w = signal_widths[logical]
        print(f"    {logical} ({w}-bit)")

    # Stream VCD and collect observed values
    print(f"\n  Streaming VCD...")
    stats = collect_observed_values(vcd_path, filtered_id_map, signal_widths)

    # Report observations
    total_values = 0
    for name in sorted(stats.keys()):
        s = stats[name]
        n_values = len(s["observed"])
        total_values += n_values
        if n_values == 0:
            print(f"    {yellow('WARN')} {name}: no value changes observed")
        else:
            vals = sorted(s["observed"].keys())
            val_strs = [format(v, f'0{s["width"]}b') for v in vals]
            print(f"    {name}: {n_values} unique values ({', '.join(val_strs)})")

    if total_values == 0:
        print(f"\n  {fail_str()}: No signal activity observed in VCD")
        return 1

    # Select triggers
    signal_filter = set(args.signals) if args.signals else None
    triggers = select_triggers(stats, args.max_captures, signal_filter)

    if not triggers:
        print(f"\n  {fail_str()}: No trigger points selected (all signals idle or filtered out)")
        return 1

    print(f"\n  Selected {len(triggers)} trigger points:")
    for name, val, width, count, _ in triggers:
        val_bin = format(val, f'0{width}b')
        print(f"    {name}{('_s'):4s} == {val_bin} ({count} transitions)")

    # Generate plan
    plan = generate_plan(triggers)

    # Self-validate before writing
    output_path = os.path.join(project_dir, "build", "hil", "ila_trigger_plan.json")
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    # Write to temp, validate, then move to final location
    import tempfile
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False,
                                      dir=os.path.dirname(output_path)) as tmp:
        json.dump(plan, tmp, indent=2)
        tmp.write("\n")
        tmp_path = tmp.name

    if not validate_trigger_plan(hil_top_path, tmp_path):
        os.unlink(tmp_path)
        print(f"\n  {fail_str()}: Generated plan failed self-validation")
        return 1

    os.replace(tmp_path, output_path)

    print(f"\n  {pass_str()}: Generated {output_path}")
    print(f"    {len(plan['captures'])} captures from VCD ground truth")
    return 0


if __name__ == "__main__":
    sys.exit(main())
