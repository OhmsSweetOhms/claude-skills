# VCD Post-Simulation Verifier

Read this file before Stage 7 (VCD verification). Use `socks/scripts/stage7_vcd_verify.py` for the automated version, or write a project-specific verifier following this architecture.

## Three-layer architecture

Structure the verifier as three layers. Pure Python, no dependencies, handles multi-GB files.

### Layer 1 -- Streaming VCD parser

Reads VCD in chunks (128 MB), parses header for signal declarations and scope hierarchy, then yields `(timestamp, changes)` tuples from the data section. Never loads the full file into memory.

```python
# Maps logical names to VCD hierarchical path suffixes for signal discovery.
SIGNAL_MAP = {
    "output_valid": "module_tb.dut.mon_output_valid",
    "output_data":  "module_tb.dut.mon_output_data",
    "input_signal": "module_tb.input_signal",
}

CHUNK_SIZE = 128 * 1024 * 1024  # 128 MB

def parse_vcd(filepath):
    """Streaming VCD parser with chunked I/O.

    Phase 1: Read header line-by-line, build signal table from $var/$scope.
    Phase 2: Read data section in CHUNK_SIZE chunks, yield (timestamp, changes,
             id_map, signals) on each new timestamp.

    id_map:  logical_name -> vcd_id  (built by matching SIGNAL_MAP paths)
    signals: vcd_id -> {"path": "a.b.c", "width": N}
    """
    with open(filepath, "r") as f:
        # Phase 1: parse $scope/$var/$upscope until $enddefinitions
        signals = {}       # vcd_id -> {"path", "width"}
        scope_stack = []
        # ... build signals dict, then id_map from SIGNAL_MAP ...

        # Phase 2: chunked data parsing
        # For each line: '#' = timestamp, 'b' = vector, '0'/'1' = scalar
        # Yield (old_timestamp, changes, id_map, signals) on each new '#'
        ...
```

### Layer 2 -- Signal tracker

Maintains current state of all mapped signals. Provides `get()`, `get_signed()`, and `rising_edge()` methods so the verification layer reads clean values without parsing VCD IDs.

```python
class SignalTracker:
    """Tracks current values of mapped signals with edge detection."""

    def __init__(self, id_map, signals):
        self.rev_map = {vid: name for name, vid in id_map.items()}
        self.values = {}        # logical_name -> int
        self.prev_values = {}   # logical_name -> int (previous)

    def update(self, changes):
        """Apply (vcd_id, value) changes. Returns set of changed logical names."""
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
        return self.prev_values.get(name, 0) == 0 and self.values.get(name, 0) == 1
```

### Layer 3 -- Verification engine

Drives the parser, feeds changes to the tracker, and runs checks at meaningful events (not every timestamp). Collects per-segment results and prints a final pass/fail table.

```python
class SegmentResult:
    """Collects verification results for one operating segment."""
    def __init__(self, segment_id, start_ps):
        self.segment_id = segment_id
        self.start_ps = start_ps
        self.checks = {}     # check_name -> {"passed": bool, "evidence": str}

    def passed(self):
        return all(c["passed"] for c in self.checks.values())

def run_verification(filepath):
    tracker = None
    segments = []

    for timestamp, changes, id_map, signals in parse_vcd(filepath):
        if tracker is None:
            tracker = SignalTracker(id_map, signals)

        changed = tracker.update(changes)

        # Segment detection: watch for a stimulus change
        # Run checks on meaningful events, not every timestamp:
        if "output_valid" in changed and tracker.rising_edge("output_valid"):
            value = tracker.get_signed("output_data", 32)
            # Check convergence, stability, accuracy...

    # Print per-segment PASS/FAIL table
    for seg in segments:
        status = "PASS" if seg.passed() else "FAIL"
        print(f"  Segment {seg.segment_id}: {status}")

    return 0 if all(s.passed() for s in segments) else 1
```

---

## Selective VCD logging (Tcl)

Omit sys_clk from the VCD dump to reduce file size dramatically. Log only verification-relevant signals.

```tcl
# _run_vcd.tcl
open_vcd module_verify.vcd
# Log specific signals, NOT sys_clk
log_vcd /module_tb/dut/mon_signal_a
log_vcd /module_tb/dut/mon_signal_b
log_vcd /module_tb/dut/mon_flag
log_vcd /module_tb/ref_in
run -all
close_vcd
```

---

## Verification checks

Typical checks to implement per segment:

- **Convergence:** key signal reaches target within N events of stimulus
- **Stability:** key signal stays within tolerance band after convergence
- **Steady-state accuracy:** output error (e.g. ppm) below threshold
- **No glitches:** flag signal does not deassert after asserting (unless expected)
- **Input consistency:** stimulus frequency matches expected value

---

## Integration with the build pipeline

Use `scripts/stage6_xsim.py` for simulation, then `scripts/stage7_vcd_verify.py` for verification:

```bash
# Normal simulation
python scripts/stage6_xsim.py --project-dir . --top module_tb

# Simulation with VCD capture
python scripts/stage6_xsim.py --project-dir . --top module_tb --vcd

# VCD verification (after simulation)
python scripts/stage7_vcd_verify.py module_verify.vcd --signal-map signals.json
```
