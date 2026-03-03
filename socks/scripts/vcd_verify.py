#!/usr/bin/env python3
"""
Stage 7: VCD Post-Simulation Verification -- Independent verification
from raw waveform data.

This is a framework script. Project-specific verifiers should import
from socks_lib and implement their own verification logic using the
SignalTracker pattern from references/vcd-verify.md.

Usage:
    python scripts/vcd_verify.py module_verify.vcd --signal-map map.json
    python scripts/vcd_verify.py module_verify.vcd --list-signals

Exit code 0 if all checks pass, 1 if any fail.
"""

import argparse
import json
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from socks_lib import (
    parse_vcd_header, stream_vcd,
    print_header, print_separator, pass_str, fail_str,
)


class SignalTracker:
    """Tracks current values of mapped signals with edge detection."""

    def __init__(self, id_map, signals):
        self.rev_map = {vid: name for name, vid in id_map.items()}
        self.values = {}
        self.prev_values = {}

    def update(self, changes):
        """Apply changes. Returns set of changed logical names."""
        changed = set()
        for vcd_id, val in changes:
            name = self.rev_map.get(vcd_id)
            if name is not None:
                self.prev_values[name] = self.values.get(name, 0)
                self.values[name] = val
                changed.add(name)
        return changed

    def get(self, name, default=0):
        return self.values.get(name, default)

    def get_signed(self, name, width=32):
        val = self.values.get(name, 0)
        return val - (1 << width) if val >= (1 << (width - 1)) else val

    def rising_edge(self, name):
        return (self.prev_values.get(name, 0) == 0 and
                self.values.get(name, 0) == 1)


def list_signals(filepath):
    """List all signals found in the VCD file."""
    signals, _ = parse_vcd_header(filepath)
    print(f"\n  Signals in {os.path.basename(filepath)}:")
    for vcd_id, sig in sorted(signals.items(), key=lambda x: x[1].path):
        print(f"    {sig.path:50s}  width={sig.width}  id={vcd_id}")
    print(f"\n  Total: {len(signals)} signals")


def build_id_map(signals, signal_map):
    """Build logical_name -> vcd_id mapping from signal_map dict."""
    id_map = {}
    for logical_name, path_suffix in signal_map.items():
        for vcd_id, sig in signals.items():
            if sig.path.endswith(path_suffix):
                id_map[logical_name] = vcd_id
                break
    return id_map


def run_basic_verification(filepath, signal_map):
    """Run basic verification: count events, check for signal activity."""
    signals, _ = parse_vcd_header(filepath)
    id_map = build_id_map(signals, signal_map)

    print(f"\n  Signal mapping:")
    for name, path in signal_map.items():
        vcd_id = id_map.get(name)
        status = f"-> {vcd_id}" if vcd_id else "NOT FOUND"
        print(f"    {name:30s} {status}")

    if not id_map:
        print(f"\n  ERROR: No signals mapped -- check signal_map paths")
        return 1

    tracker = SignalTracker(id_map, signals)
    event_counts = {name: 0 for name in signal_map}
    timestamps = 0

    for ts, changes in stream_vcd(filepath):
        changed = tracker.update(changes)
        timestamps += 1
        for name in changed:
            if name in event_counts:
                event_counts[name] += 1

    print(f"\n  Timestamps processed: {timestamps}")
    print(f"\n  Signal activity:")
    all_active = True
    for name, count in event_counts.items():
        active = count > 0
        if not active:
            all_active = False
        status = pass_str() if active else fail_str()
        print(f"    [{status}] {name:30s} {count} changes")

    return 0 if all_active else 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Stage 7: VCD Post-Simulation Verification")
    parser.add_argument("vcd_file", help="Path to VCD file")
    parser.add_argument("--list-signals", action="store_true",
                        help="List all signals in the VCD and exit")
    parser.add_argument("--signal-map", type=str, default=None,
                        help="JSON file mapping logical names to VCD paths")
    args = parser.parse_args()

    print_header("SOCKS Stage 7 -- VCD Verification")

    if not os.path.isfile(args.vcd_file):
        print(f"\n  ERROR: VCD file not found: {args.vcd_file}")
        print_separator()
        return 1

    file_size_mb = os.path.getsize(args.vcd_file) / (1024 * 1024)
    print(f"\n  VCD file: {args.vcd_file}")
    print(f"  File size: {file_size_mb:.1f} MB")

    if args.list_signals:
        list_signals(args.vcd_file)
        print_separator()
        return 0

    if args.signal_map:
        with open(args.signal_map) as f:
            signal_map = json.load(f)
    else:
        print("\n  No --signal-map provided. Use --list-signals to see available signals.")
        print("  Create a JSON file mapping logical names to VCD hierarchical paths.")
        print_separator()
        return 1

    result = run_basic_verification(args.vcd_file, signal_map)

    print()
    print_separator()
    if result == 0:
        print(f"  RESULT: {pass_str()} -- all mapped signals active")
    else:
        print(f"  RESULT: {fail_str()} -- verification issues found")
    print_separator()

    return result


if __name__ == "__main__":
    sys.exit(main())
