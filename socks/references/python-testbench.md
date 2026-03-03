# Python Testbench Model Skeleton

Read this file before Stage 5 (cycle-accurate Python testbench).

## Model structure

Each VHDL process becomes a section in `clock()`. All reads use `self.X` (the old value); all writes go to `n_` local variables. The commit block at the end mirrors VHDL signal assignment semantics. If you write `self.X = value` mid-function and then read `self.X` later in the same `clock()` call, you have a delta-cycle bug.

```python
# Example: edge-triggered pulse counter with synchroniser, edge detect,
# and saturating accumulator -- 3 VHDL processes in one clock() method.

DATA_W   = 16
COUNT_MAX = (1 << DATA_W) - 1

class PulseCounter:
    def __init__(self):
        # Match VHDL reset values exactly. Use the same signal names.
        # p_sync
        self.sync1 = 0
        self.sync2 = 0
        # p_edge
        self.prev  = 0
        self.rise  = 0
        # p_count
        self.count = 0

    def clock(self, pulse_in):
        """One rising_edge(clk). Mirrors 3 VHDL processes."""

        # p_sync -- 2-FF CDC synchroniser -------------------------
        n_sync1 = pulse_in
        n_sync2 = self.sync1          # reads OLD self.sync1

        # p_edge -- rising-edge detector --------------------------
        n_prev = self.sync2           # reads OLD self.sync2
        n_rise = 1 if (self.sync2 == 1 and self.prev == 0) else 0

        # p_count -- saturating accumulator -----------------------
        n_count = self.count
        if self.rise == 1:            # reads OLD self.rise
            if self.count < COUNT_MAX:
                n_count = self.count + 1

        # Commit -- all writes happen here, never above -----------
        self.sync1 = n_sync1
        self.sync2 = n_sync2
        self.prev  = n_prev
        self.rise  = n_rise
        self.count = n_count
```

The pipeline latency (sync1 -> sync2 -> prev/rise -> count) naturally falls out of the commit-at-end discipline: each stage reads the previous stage's old value, producing exactly the same 1-cycle-per-stage delay as the VHDL.

---

## Arithmetic helpers

Mirror VHDL bit-width semantics exactly in Python.

```python
MOD32  = 1 << 32
MASK32 = MOD32 - 1
MSB32  = 1 << 31

def as_signed32(v):
    """VHDL signed() cast on a 32-bit unsigned value."""
    v = v & MASK32
    return v - MOD32 if v >= MSB32 else v

def clamp(v, lo, hi):
    return lo if v < lo else (hi if v > hi else v)

def shift_right_arithmetic(v, n, width):
    """VHDL shift_right(signed, n) - arithmetic (sign-extending)."""
    return v >> n   # Python >> on negative int is arithmetic
```

---

## What to verify

- Steady-state output error (e.g. frequency ppm): must converge for all operating points.
- Lock/convergence time: must be finite and bounded by expected gain/bandwidth.
- Post-lock stability: output must stay within tolerance band for all inputs.
- Corner cases: maximum step input, minimum gain, boundary conditions.
- Constants: verify every numeric constant in the VHDL header comment by computing it independently in the Python script.

---

## Plots

Always generate at minimum the key state variables vs time, with pass/fail bands overlaid. Use matplotlib with the Agg backend (no display required):

```python
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
```
