---
name: socks
description: "System-On-a-Chip Kit for Synthesis. Use this skill for any FPGA/SoC design task: VHDL RTL, AXI-Lite interfaces, register maps, memory-mapped peripherals, bare-metal C drivers, Python models of VHDL, synthesis checks, Xsim testbenches, VCD verification, or Zynq-7000/UltraScale fabric design. Also triggers on PLLs, DPLLs, NCOs, clock recovery, phase detectors, fractional-N architectures, PI loop filters, lock detection, DSP48E1 mapping, CDC synchronisers, and PS-PL SoC integration."
---

# SOCKS -- System-On-a-Chip Kit for Synthesis

## Pipeline Overview

The pipeline has 12 stages (0-11) executed in order. Each stage catches a class of bugs the next cannot.

```
Env -> Architecture -> VHDL -> Audit -> Python TB ->
C Driver -> SV/Xsim TB -> VCD verify -> CSV verify -> Vivado synth -> Bash audit -> CLAUDE.md
```

---

## Stage Dispatch Table

| Stage | Name | Script / Action | Reference to Read |
|-------|------|----------------|-------------------|
| 0 | Environment Setup | `scripts/stage0_env.py` | -- |
| 1 | Architecture | `scripts/stage1_architecture.py` | `references/vhdl.md` (saturation, DSP widths) |
| 2 | VHDL Authoring | *Claude writes code* | `references/vhdl.md` |
| 3 | Synthesis Audit | `scripts/stage4_audit.py` | `references/synthesis.md` |
| 4 | Python Testbench | *Claude writes code* | `references/python-testbench.md` |
| 5 | Bare-Metal C Driver | *Claude writes code* | `references/baremetal.md` |
| 6 | SV/Xsim Testbench | *Claude writes code* + `scripts/stage6_xsim.py` | `references/xsim.md` |
| 7 | VCD Verification | `scripts/stage7_vcd_verify.py` | `references/vcd-verify.md` |
| 8 | CSV Cross-Check | `scripts/stage8_csv_crosscheck.py` | -- |
| 9 | Vivado Synthesis | `scripts/stage9_synth.py` | `references/synthesis.md` |
| 10 | Bash Audit | `scripts/stage11_bash_audit.py` | -- |
| 11 | CLAUDE.md | *Claude writes docs* | `references/project-structure.md` |

**For DPLL/PLL/NCO/clock recovery designs:** read `references/dpll.md` before Stage 1.

---

## Failure Recovery

When a stage fails, don't restart from Stage 0. Go back to the **producing stage**, fix the issue, then carry the fix forward through all downstream stages.

| Failing Stage | Re-entry | Rationale |
|---|---|---|
| 2 VHDL | 1 if design error, 2 if coding error | Architecture mistakes need block diagram update |
| 3 Audit | 2 → 3 | Fix VHDL, re-audit |
| 4 Python TB | 4 if model bug, 2 → 3 → 4 if RTL bug | Model is the spec; RTL must match |
| 5 C Driver | 5 if driver bug, 2 → 3 if register map wrong | Register map flows from VHDL |
| 6 SV/Xsim TB | 6 if TB bug, 5 → 6 if DPI-C issue, 2 → 3 → ... if RTL bug | Root-cause determines re-entry |
| 7 VCD Verify | 6 → 7 if stimulus issue, 2 → 3 → ... if RTL bug | Independent check catches what SV self-checks miss |
| 8 CSV Cross-Check | 4 + 6 (align model and sim) | Divergence means model or RTL is wrong |
| 9 Vivado Synth | 2 → 3 if timing/resource issue | RTL restructuring needed |
| 10 Bash Audit | 10 (fix scripts in place) | No upstream impact |
| 11 CLAUDE.md | 11 (fix docs in place) | No upstream impact |

**Carry-forward rule:** every fix must propagate through all downstream stages. Fix VHDL → re-audit → re-run Python TB → update C driver if register map changed → re-run SV TB → etc.

---

## Orchestrator

Run stages individually or as a pipeline:

```bash
python scripts/socks.py --project-dir . --stages all
python scripts/socks.py --project-dir . --stages 0,4,10
python scripts/socks.py --project-dir . --stages 4 --files src/*.vhd
python scripts/socks.py --project-dir . --stages 10 --top my_module --part xc7z020clg484-1
```

---

## Project Structure

Every project follows this layout (see `references/project-structure.md`):

```
project_name/
├── src/          # VHDL source (synthesisable RTL)
├── tb/           # Python TB, SV TB, DPI-C bridge
├── sw/           # C/C++ bare-metal drivers
├── sim/          # Build scripts, TCL, synthesis reports
├── CLAUDE.md     # Project guide (Stage 11 output)
└── .gitignore    # Vivado/Xsim artifact ignores
```

---

## Stage Details

### Stage 0 -- Environment Setup

Run `scripts/stage0_env.py` to discover Vivado and verify tools.

### Stage 1 -- Architecture

Before writing VHDL, produce two deliverables:

**1. Block diagram** — ASCII art showing:
- All modules/entities and their hierarchy
- Clock domains with frequencies (label every domain boundary)
- Data flow between modules with signal names and bit widths
- Baseband / serial rates at every port (tx_clk, tx_out, sample_en, etc.)
- A **rate summary table** at the bottom listing every clock/tick/bit-rate and how it's derived

This diagram catches clock/rate mismatches before they become VHDL bugs. Every frequency in the design must appear with its derivation (e.g. "100 MHz / 100 = 1 MHz tick" or "freq_word × sys_clk / 2^32 = 1 MHz NCO").

**2. Resource analysis** — answer: What are the widest intermediates? Do any overflow VHDL integer range? How many DSP48E1 does each multiply need? What is the critical path depth?

Read `references/vhdl.md` for saturation constant patterns and multiply width rules.

### Stage 2 -- VHDL Authoring

Read `references/vhdl.md` before writing any VHDL. Key rules: architecture `rtl`, reset inside `rising_edge(clk)`, named processes `p_*`, state prefix `ST_*`, monitor ports `mon_*`, no `abs()` on signed, no `2**N` constants, no component declarations.

### Stage 3 -- Synthesis Audit

Run `scripts/stage4_audit.py src/*.vhd`. 12 static checks. All must pass before proceeding. The audit runs immediately after VHDL authoring so synthesis hazards are caught before writing testbenches — no time wasted testing code that won't synthesise.

### Stage 4 -- Python Testbench

Read `references/python-testbench.md`. The model mirrors VHDL at register-transfer level. Commit-at-end discipline: all reads from `self.X` (old), all writes to `n_` locals, commit block at end.

### Stage 5 -- Bare-Metal C Driver

Read `references/baremetal.md`. Generate C header + source from the VHDL register map. Standard API: init, enable/disable, load/read, status, W1C clear, polling wait, IRQ enable/disable. For designs with DPLLs, include runtime parameter computation (see `references/baremetal.md`, DPLL section).

The C driver is written before the SV testbench because DPI-C lets the TB call the driver's computation functions directly, keeping formulas in one place.

### Stage 6 -- SV/Xsim Testbench

Read `references/xsim.md`. Check all 7 Xsim rules (X1-X7). Use monitor ports for signal access. `always @(negedge clk)` for reference drivers. Never `2.0 ** 32`. After writing the SV testbench, compile and simulate via `scripts/stage6_xsim.py` -- no raw bash calls needed.

**DPI-C:** If the TB shares computation with the C driver (DPLL params, CRC tables, protocol encoding), place a `.c` file in `tb/`. The build script auto-discovers it, compiles with `xsc`, and links via `-sv_lib dpi`. See `references/xsim.md` for the pattern.

### Stage 7 -- VCD Verification

Independent verification from raw waveform data. Does not rely on SV self-checks. Read `references/vcd-verify.md` for the three-layer architecture.

### Stage 8 -- CSV Cross-Check

Compare SV simulation CSV against Python model output. Align by event count. Report first divergence.

### Stage 9 -- Vivado Synthesis

Run `scripts/stage9_synth.py`. Generates TCL, invokes Vivado batch mode, parses utilization/timing/DRC reports. All timing checks must show MET.

### Stage 10 -- Bash Audit

Run `scripts/stage11_bash_audit.py --project-dir .`. Scans all project shell scripts, Tcl files, and Makefiles for raw EDA tool calls (xvhdl, xvlog, xelab, xsim, vivado) that should be routed through SOCKS Python scripts instead. Also checks for `source settings64.sh` patterns, process substitution, and unsourced tool invocations. All checks must pass -- any raw tool call is a pipeline gap that needs a script.

### Stage 11 -- CLAUDE.md

Create project documentation with: What This Is, Architecture, Files table, Build & Test, Synthesis Results (from Stage 9 reports), Vivado version, Conventions. This is last because it documents everything including the C driver from Stage 5.

---

## Methodology Principles

1. **Read the full file before modifying it.** Never edit VHDL based on a summary.
2. **Audit scripts are not throwaway code.** They become regression tests.
3. **Carry every fix forward through all layers.** VHDL fix -> Python model fix -> SV TB fix.
4. **Verify comments with the same rigour as code.** Wrong constants in headers become bugs.
5. **Never use abs() on signed values.** Two-sided comparison in VHDL, Python, and SV.
6. **The Python model is the spec.** When VHDL and Python disagree, fix the VHDL (unless the Python has a demonstrable commit-order bug).
