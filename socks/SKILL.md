---
name: socks
description: "System-On-a-Chip Kit for Synthesis. Use this skill for any FPGA/SoC design task: VHDL RTL, AXI-Lite interfaces, register maps, memory-mapped peripherals, bare-metal C drivers, Python models of VHDL, synthesis checks, Xsim testbenches, VCD verification, or Zynq-7000/UltraScale fabric design. Also triggers on PLLs, DPLLs, NCOs, clock recovery, phase detectors, fractional-N architectures, PI loop filters, lock detection, DSP48E1 mapping, CDC synchronisers, and PS-PL SoC integration."
---

# SOCKS -- System-On-a-Chip Kit for Synthesis

## Pipeline Overview

The pipeline has 13 stages executed in order. Never skip a stage or reorder them -- each catches a class of bugs the next cannot.

```
Env -> Architecture -> VHDL -> Python TB -> Audit -> Python rerun ->
SV/Xsim TB -> VCD verify -> CSV verify -> Vivado synth -> C Driver -> Bash audit -> CLAUDE.md
```

**Implementation model:** After Stage 0 and planning, delegate Stages 1-12 to a Sonnet agent. Opus handles planning and review; Sonnet handles implementation.

---

## Stage Dispatch Table

| Stage | Name | Script / Action | Reference to Read |
|-------|------|----------------|-------------------|
| 0 | Environment Setup | `scripts/stage0_env.py` | -- |
| 1 | Architecture | `scripts/stage1_architecture.py` | `references/vhdl.md` (saturation, DSP widths) |
| 2 | VHDL Authoring | *Claude writes code* | `references/vhdl.md` |
| 3 | Python Testbench | *Claude writes code* | `references/python-testbench.md` |
| 4 | Synthesis Audit | `scripts/stage4_audit.py` | `references/synthesis.md` |
| 5 | Python Re-run | `scripts/stage5_python_rerun.py` | -- |
| 6 | SV/Xsim Testbench | *Claude writes code* + `scripts/stage6_xsim.py` | `references/xsim.md` |
| 7 | VCD Verification | `scripts/stage7_vcd_verify.py` | `references/vcd-verify.md` |
| 8 | CSV Cross-Check | `scripts/stage8_csv_crosscheck.py` | -- |
| 9 | Vivado Synthesis | `scripts/stage9_synth.py` | `references/synthesis.md` |
| 10 | C Driver | *Claude writes code* | `references/baremetal.md` |
| 11 | Bash Audit | `scripts/stage11_bash_audit.py` | -- |
| 12 | CLAUDE.md | *Claude writes docs* | `references/project-structure.md` |

**For DPLL/PLL/NCO/clock recovery designs:** read `references/dpll.md` before Stage 1.

---

## Orchestrator

Run stages individually or as a pipeline:

```bash
python scripts/socks.py --project-dir . --stages all
python scripts/socks.py --project-dir . --stages 0,4,9
python scripts/socks.py --project-dir . --stages 4 --files src/*.vhd
python scripts/socks.py --project-dir . --stages 9 --top my_module --part xc7z020clg484-1
```

---

## Project Structure

Every project follows this layout (see `references/project-structure.md`):

```
project_name/
├── src/          # VHDL source (synthesisable RTL)
├── tb/           # Python TB, SV TB, audit scripts
├── sw/           # C/C++ bare-metal drivers
├── sim/          # Build scripts, TCL, synthesis reports
├── CLAUDE.md     # Project guide (Stage 10 output)
└── .gitignore    # Vivado/Xsim artifact ignores
```

---

## Stage Details

### Stage 0 -- Environment Setup

Run `scripts/stage0_env.py` to discover Vivado and verify tools.

### Stage 1 -- Architecture

Before writing VHDL, answer: What are the widest intermediates? Do any overflow VHDL integer range? How many DSP48E1 does each multiply need? What is the critical path depth?

Read `references/vhdl.md` for saturation constant patterns and multiply width rules.

### Stage 2 -- VHDL Authoring

Read `references/vhdl.md` before writing any VHDL. Key rules: architecture `rtl`, reset inside `rising_edge(clk)`, named processes `p_*`, state prefix `ST_*`, monitor ports `mon_*`, no `abs()` on signed, no `2**N` constants, no component declarations.

### Stage 3 -- Python Testbench

Read `references/python-testbench.md`. The model mirrors VHDL at register-transfer level. Commit-at-end discipline: all reads from `self.X` (old), all writes to `n_` locals, commit block at end.

### Stage 4 -- Synthesis Audit

Run `scripts/stage4_audit.py src/*.vhd`. 12 static checks. All must pass before proceeding.

### Stage 5 -- Python Re-run

Run `scripts/stage5_python_rerun.py tb/*_tb.py` after audit fixes. Confirms no functional regression.

### Stage 6 -- SV/Xsim Testbench

Read `references/xsim.md`. Check all 7 Xsim rules (X1-X7). Use monitor ports for signal access. `always @(negedge clk)` for reference drivers. Never `2.0 ** 32`. After writing the SV testbench, compile and simulate via `scripts/stage6_xsim.py` -- no raw bash calls needed.

### Stage 7 -- VCD Verification

Independent verification from raw waveform data. Does not rely on SV self-checks. Read `references/vcd-verify.md` for the three-layer architecture.

### Stage 8 -- CSV Cross-Check

Compare SV simulation CSV against Python model output. Align by event count. Report first divergence.

### Stage 9 -- Vivado Synthesis

Run `scripts/stage9_synth.py`. Generates TCL, invokes Vivado batch mode, parses utilization/timing/DRC reports. All timing checks must show MET.

### Stage 10 -- Bare-Metal C Driver

Read `references/baremetal.md`. Generate C header + source from the VHDL register map. Standard API: init, enable/disable, load/read, status, W1C clear, polling wait, IRQ enable/disable.

### Stage 11 -- Bash Audit

Run `scripts/stage11_bash_audit.py --project-dir .`. Scans all project shell scripts, Tcl files, and Makefiles for raw EDA tool calls (xvhdl, xvlog, xelab, xsim, vivado) that should be routed through SOCKS Python scripts instead. Also checks for `source settings64.sh` patterns, process substitution, and unsourced tool invocations. All checks must pass -- any raw tool call is a pipeline gap that needs a script.

### Stage 12 -- CLAUDE.md

Create project documentation with: What This Is, Architecture, Files table, Build & Test, Synthesis Results (from Stage 9 reports), Vivado version, Conventions. This is last because it documents everything including the C driver from Stage 10.

---

## Methodology Principles

1. **Read the full file before modifying it.** Never edit VHDL based on a summary.
2. **Audit scripts are not throwaway code.** They become regression tests.
3. **Carry every fix forward through all layers.** VHDL fix -> Python model fix -> SV TB fix.
4. **Verify comments with the same rigour as code.** Wrong constants in headers become bugs.
5. **Never use abs() on signed values.** Two-sided comparison in VHDL, Python, and SV.
6. **The Python model is the spec.** When VHDL and Python disagree, fix the VHDL (unless the Python has a demonstrable commit-order bug).
