---
name: status
description: "Project health dashboard for FPGA/SoC designs. Use when the user asks for project status, health check, overview, or summary. Shows test counts, synthesis results, timing pass/fail, open TODOs, and report freshness in a compact dashboard format."
---

# Project Status Dashboard

Quick-glance project health for FPGA/SoC designs.

## When to Use

- User asks "what's the status" or "how's the project"
- User asks for a summary or overview
- Before starting work on a project to get context
- After returning to a project after time away

## What to Check

Scan the project directory and gather data from these sources:

### 1. Git Status
- Current branch
- Uncommitted changes (modified, untracked, staged)
- Last commit message and date

### 2. Test Counts

**Python testbench** -- look in `tb/*_tb.py` for test functions/methods.
Count functions matching `test_*` or classes with test methods. Also look
for a summary line like "87 tests" in recent run output or CLAUDE.md.

**Xsim testbench** -- look in `tb/*_tb.sv` for test tasks or test case
counts. Check CLAUDE.md or docs/README.md for reported test counts.

**Last run results** -- check `build/logs/` for recent pipeline logs. Parse the
last log for pass/fail counts.

### 3. Synthesis Results

Read the latest reports from `build/synth/`:

- `*_utilization.txt` -- parse key resources (LUTs, Registers, DSP, BRAM)
- `*_timing_constrained.txt` -- parse WNS, WHS, WPWS
- `*_timing.txt` -- unconstrained timing (fallback if no constrained report)
- `*_drc.txt` -- error and warning counts

Check file modification times against RTL source. If reports are older than
the latest `src/*.vhd` modification, flag them as **STALE**.

### 4. TODOs and Known Issues

Scan CLAUDE.md for:
- Lines containing `TODO`, `FIXME`, `HACK`, `XXX`
- Sections titled "Timing Status" or similar with open items

Also check docs/README.md for constraint/limitation sections.

### 5. Project Structure

Verify expected directories exist: `src/`, `tb/`, `sw/`, `build/`, `docs/`

Check for expected files:
- At least one `.vhd` in `src/`
- At least one testbench in `tb/`
- CLAUDE.md exists
- .gitignore exists

## Output Format

Present as a compact dashboard:

```
Project Status: SDLC_AXI
=========================

  Git:        master, 2 modified, 3 untracked
  Last commit: d887532 "Add quick-start README" (2026-03-05)

  Tests:
    Python TB:  87 tests (tb/sdlc_axi_tb.py)
    Xsim TB:    71 tests (tb/sdlc_axi_tb.sv)

  Synthesis:                              [STALE -- reports older than RTL]
    LUTs:       1225 / 53200  (2.3%)
    Registers:  2359 / 106400 (2.2%)
    DSP48E1:    4 / 220       (1.8%)
    BRAM:       0 / 140       (0.0%)

  Timing (10.0 ns constraint):
    Setup (WNS): -6.862 ns    VIOLATED
    Hold (WHS):  +0.045 ns    MET
    Pulse Width: +4.500 ns    MET

  TODOs:
    - CLAUDE.md: "Constrained run shows setup violation (WNS = -6.862 ns)"

  Structure:  src/ tb/ sw/ build/ docs/  [OK]
```

## Colour Coding

When outputting to the user (not to a file), use markdown formatting:
- **PASS/MET** items: just state the value
- **FAIL/VIOLATED** items: bold with emphasis
- **STALE** reports: note the staleness
- **TODOs**: list each one

## Staleness Detection

A report is stale if any `.vhd` file in `src/` has a modification time
newer than the report file. Use file modification times to determine this.

Report staleness clearly:
```
  Synthesis:  [STALE -- src/sdlc_axi.vhd modified after reports]
```

## Notes

- This skill is read-only -- it never modifies files.
- If the project doesn't have synthesis reports yet, say so rather than
  showing empty tables.
- If CLAUDE.md doesn't exist, note it as a gap but still gather what's
  available from other sources.
- For test counts, prefer the number from docs/README.md or CLAUDE.md
  over counting functions (the docs should reflect the actual verified count).
