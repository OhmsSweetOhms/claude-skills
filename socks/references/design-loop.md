# SOCKS Design Loop -- Stages 2-9

This file contains the agent logic for the design loop. It is read by Claude when
entering or re-entering the loop. All loop control, re-entry, and exit decisions
live here.

> **Future extraction note:** This file is intentionally self-contained so it can
> become an agent system prompt without restructuring.

---

## What the Design Loop Is

Stages 2-9 are a closed verification loop driven by a Python model as the ground
truth. The loop iterates until the RTL meets the architecture from Stage 1 and all
verification layers agree.

```
Stage 1 approval
      │
      ▼
  ┌─────────────────────────────────┐
  │  2: Write/Modify RTL            │
  │     /regmap if reg map changed  │
  │  3: VHDL Linter                 │
  │  4: Synthesis Audit             │
  │  5: Python Testbench            │
  │  6: Bare-Metal C Driver         │
  │  7: SV/Xsim Testbench          │
  │  8: VCD Verification            │
  │  9: CSV Cross-Check             │
  │                                 │
  │  Claude decides re-entry point  │
  └─────────────────────────────────┘
      │
      ▼ (all stages pass + architecture met)
  Stage 10+
```

---

## Loop Control

### Re-entry on Failure

Reason about the root cause. Re-enter at the producing stage:

| Root cause | Re-entry | Propagates through |
|---|---|---|
| RTL bug | Stage 2 | 3 → 4 → 5 → 6 (if reg map changed) → 7 → 8 → 9 |
| Python model bug | Stage 5 | 7 → 8 → 9 |
| SV testbench bug | Stage 7 | 8 → 9 |
| Register map change | Stage 2 | **`/regmap`** → 3 → 4 → 5 → 6 → 7 → 8 → 9 |
| C driver bug | Stage 6 | 7 → 8 → 9 |

**Carry-forward rule:** Every fix must propagate through all downstream stages.
Never skip a downstream stage after a fix.

### Circular Logic Detection

If you have tried a fix and ended up at the same failure, or tried two different
approaches and reverted to where you started — **stop and ask the user.** Do not
keep iterating on the same problem.

### Downstream Propagation Prompt

When the user asks to update a single sim stage, ask if they want to update all
downstream stages too.

### Exit Criteria

The design loop exits when:
1. The architecture from Stage 1 is met, **and**
2. All verification stages (5, 7, 8, 9) pass with consistent results

---

## The Python Model Is the Spec

When VHDL and Python disagree, fix the VHDL — except when the Python model can be
shown to misrepresent VHDL timing mechanisms (e.g. a demonstrable commit-order bug
in the Python).

---

## Stage Details (2-9)

**Running automated stages:** Always run stages through the orchestrator, never
call stage scripts directly:
```bash
python3 scripts/socks.py --project-dir . --stages 4,5,7,8,9
```
This creates a timestamped pipeline log in `build/logs/`, handles stage
sequencing, and ensures all artifacts are captured. On design-loop re-entry,
run from the re-entry stage through Stage 9:
```bash
# Example: RTL fix, re-enter at Stage 4 and run through 9
python3 scripts/socks.py --project-dir . --stages 4,5,7,8,9
```

### Stage 2 -- Write/Modify RTL

Read `references/vhdl.md` before writing any VHDL.

Key rules:
- Architecture identifier: `rtl`
- Reset inside `rising_edge(clk)`
- Named processes: `p_*`
- State prefix: `ST_*`
- Monitor ports: `mon_*`
- No `abs()` on signed
- No `2**N` constants
- No component declarations

Read the full file before modifying it. Never edit VHDL based on a summary.

**Register map checkpoint:** If this Stage 2 pass adds, removes, or modifies
any register (address, bit field, access type, or reset value), invoke the
`/regmap` skill immediately after the VHDL edit and before proceeding to
Stage 3. The `/regmap` skill will diff all layers (Python TB, SV TB, C driver,
docs) against the updated VHDL and report what needs updating. Fix all
mismatches before continuing -- this prevents the most common class of
propagation bugs in the design loop.

### Stage 3 -- VHDL Linter

Read `references/linter.md`.

Run the linter on `src/`. Fix all syntax errors and actionable warnings in your
own code. Ignore read-only external module warnings.

### Stage 4 -- Synthesis Audit

Run `scripts/audit.py src/*.vhd`. 13 static checks. All must pass before
proceeding. External module audit warnings are non-blocking (exit code 2).

### Stage 5 -- Python Testbench

Read `references/python-testbench.md`.

The model mirrors VHDL at register-transfer level.

**Commit discipline:** all reads from `self.X` (old values), all writes to
`n_` locals, commit block at end of each cycle.

### Stage 6 -- Bare-Metal C Driver

Read `references/baremetal.md`.

Generate C header + source from the VHDL register map. Standard API: init,
enable/disable, load/read, status, W1C clear, polling wait, IRQ enable/disable.

For designs with DPLLs, include runtime parameter computation (see baremetal.md,
DPLL section).

The C driver is written before the SV testbench so DPI-C lets the TB call the
driver's computation functions directly, keeping formulas in one place.

### Stage 7 -- SV/Xsim Testbench

Read `references/xsim.md`. Verify all 7 Xsim rules (X1-X7).

Key rules:
- Use monitor ports for signal access
- `always @(negedge clk)` for reference drivers
- Never `2.0 ** 32`
- Always enable `--vcd` unconditionally
- Compile and simulate via `scripts/xsim.py` — no raw bash calls

**DPI-C:** If the TB shares computation with the C driver, place a `.c` file in
`tb/`. The build script auto-discovers it, compiles with `xsc`, and links via
`-sv_lib dpi`. See `references/xsim.md` for the pattern.

### Stage 8 -- VCD Verification

Read `references/vcd-verify.md`.

Independent verification from raw waveform data. Does not rely on SV
self-checks. Uses the three-layer architecture described in the reference.

### Stage 9 -- CSV Cross-Check

Run `scripts/csv_crosscheck.py`.

Compare SV simulation CSV against Python model output. Align by event count.
Report first divergence.

---

## Methodology Principles (Loop-Specific)

1. **Read the full file before modifying it.** Never edit VHDL based on a summary.
2. **The Python model is the spec.** When VHDL and Python disagree, fix the VHDL.
3. **Carry every fix forward through all layers.** VHDL fix → Python model fix → SV TB fix.
4. **Verify comments with the same rigour as code.** Wrong constants in headers become bugs.
5. **Never use `abs()` on signed values.** Two-sided comparison in VHDL, Python, and SV.
6. **Audit scripts are not throwaway code.** They become regression tests.
