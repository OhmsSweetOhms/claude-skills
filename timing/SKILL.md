---
name: timing
description: "Timing closure assistant for Vivado synthesis. Use when a design has timing violations (negative WNS/WHS), when the user asks about critical paths, or when they want to fix setup/hold failures. Parses Vivado timing reports, identifies the critical path, diagnoses root causes (long carry chains, deep logic levels, high fan-out), and recommends RTL fixes (pipeline registers, retiming, DSP inference). Can re-run constrained synthesis to verify fixes."
---

# Timing Closure Assistant

Diagnose and fix Vivado timing violations in FPGA designs.

## When to Use

- User mentions timing violations, negative WNS, setup failures, hold failures
- User asks to fix critical path or close timing
- After a `/socks` Stage 10 run that shows VIOLATED timing
- User asks "why is timing failing" or similar

## Step 1: Gather Reports

Find and read the timing reports. Look in `sim/` and `build/` for:
- `*_timing_constrained.txt` -- constrained timing summary (WNS/WHS/WPWS)
- `*_timing_paths.txt` -- top N worst paths with full detail
- `*_timing.txt` -- unconstrained timing summary
- `*_utilization.txt` -- resource usage (relevant for DSP/BRAM inference)

If no constrained reports exist, tell the user they need to run constrained
synthesis first:
```bash
python3 scripts/stage9_synth.py --top <module> --src-dir src --out-dir sim \
    --async-ports <async_pins...>
```

## Step 2: Parse the Critical Path

From the timing paths report, extract for each violating path:

1. **Slack** -- how much it misses by (e.g., -6.862 ns)
2. **Source** -- starting register/DSP (cell name and clock)
3. **Destination** -- ending register (cell name and clock)
4. **Data path delay** -- total, split into logic vs route
5. **Logic levels** -- count and breakdown (CARRY4, LUT, DSP48E1, etc.)
6. **Fan-out** -- check for high fan-out nets (fo=N in the report)
7. **Path group** -- intra-clock or inter-clock

Present a summary table:

```
Critical Path Analysis
======================
  Slack:           -6.862 ns
  Path:            u_dpll_wrap/u_core/integ_v3 -> freq_corr_reg[0]
  Data path delay: 16.125 ns (logic 11.109 ns, route 5.016 ns)
  Logic levels:    27 (CARRY4=21, DSP48E1=1, LUT2=3, LUT5=1, LUT6=1)
  Worst fan-out:   97 (integ_v2[47]), 96 (integ_v10_in)
  Bottleneck:      Logic-bound (69% logic, 31% route)
```

## Step 3: Diagnose Root Cause

Classify the violation into one or more categories:

### Logic-bound (logic delay > 60% of data path)
- **Long carry chains**: Count consecutive CARRY4 cells. Zynq-7000 has
  4-bit carry per slice; a 48-bit add = 12 CARRY4 levels. If chained
  operations share a carry path, the depth multiplies.
- **Deep combinational logic**: Count LUT levels between registers.
  Target <= 6 LUT levels for 100 MHz on -1 speed grade.
- **Missing DSP inference**: Multiplies implemented in LUTs instead of
  DSP48E1. Check if `USE_DSP48` attribute is needed.
- **Cascaded DSP**: DSP48E1 PCOUT->PCIN cascading adds ~1.5 ns per stage.
  More than 2 cascaded DSPs in one cycle is problematic at 100 MHz.

### Route-bound (route delay > 50% of data path)
- **High fan-out**: Nets with fo > 50 cause long routing. Add register
  duplication or `MAX_FANOUT` attribute.
- **Placement spread**: Unplaced design shows estimated routing; real
  routing after P&R may differ. If already placed, check physical distance.

### Clock-related
- **Clock skew**: Large negative skew eats into timing margin.
- **Multi-clock paths**: Missing false_path or multicycle constraints.

## Step 4: Recommend Fixes

Based on the diagnosis, recommend specific RTL changes. **Important:** if the
critical path is inside a read-only external module (symlink), flag that the
fix must be made upstream and discuss alternatives (constraint relaxation,
pipelining at the boundary, multicycle path if applicable).

### Common Fix Patterns

**Pipeline register insertion:**
```
Before: result <= A + B + C + D;  -- 3 add levels
After:  sum_ab <= A + B;          -- cycle 1
        sum_cd <= C + D;          -- cycle 1
        result <= sum_ab + sum_cd; -- cycle 2 (2 add levels)
```
Note: adding pipeline stages changes latency. Check if downstream logic
needs adjustment (e.g., delayed valid signals, adjusted counter targets).

**Multicycle path constraint** (when a slow path is known to be stable for
multiple cycles):
```tcl
set_multicycle_path 2 -setup -from [get_cells source_reg] -to [get_cells dest_reg]
set_multicycle_path 1 -hold  -from [get_cells source_reg] -to [get_cells dest_reg]
```

**Register retiming** (let Vivado move registers across combinational logic):
```tcl
set_property REGISTER_BALANCING yes [get_cells instance_path]
```
or in VHDL:
```vhdl
attribute REGISTER_BALANCING : string;
attribute REGISTER_BALANCING of my_signal : signal is "yes";
```

**Fan-out reduction:**
```vhdl
attribute MAX_FANOUT : integer;
attribute MAX_FANOUT of high_fanout_sig : signal is 32;
```

**Force DSP inference:**
```vhdl
attribute USE_DSP48 : string;
attribute USE_DSP48 of my_mult : signal is "yes";
```

## Step 5: Implement and Verify

After the user approves a fix:

1. Read the full VHDL file before modifying (never edit from summary)
2. Make the RTL change
3. If the fix changes latency, update the Python model and SV testbench
4. Re-run constrained synthesis:
   ```bash
   python3 scripts/stage9_synth.py --top <module> --src-dir src --out-dir sim \
       --async-ports <async_pins...>
   ```
5. Parse the new timing report and verify WNS >= 0
6. If still failing, return to Step 2 with the new critical path

## Step 6: Update Documentation

Once timing is clean:
- Update CLAUDE.md timing status section (remove TODO if present)
- Update docs/README.md synthesis results table with new slack values
- Commit the timing reports

## Notes

- **Zynq-7000 -1 speed grade targets:** At 100 MHz (10 ns period), aim for
  WNS >= +0.5 ns margin after place-and-route. Post-synthesis WNS >= +1.0 ns
  is a good target since P&R typically adds 0.5-1.0 ns of routing delay.
- **Unconstrained vs constrained:** Unconstrained synthesis reports are
  misleading for timing analysis. Always use constrained reports.
- **OOC synthesis vs full design:** Out-of-context synthesis may show
  different timing than in-context due to I/O delay assumptions and clock
  tree differences. The false_path constraints on async ports are essential.
