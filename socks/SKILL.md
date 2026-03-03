---
name: socks
description: "System-On-a-Chip Kit for Synthesis. Use this skill for any FPGA/SoC design task: VHDL RTL, AXI-Lite interfaces, register maps, memory-mapped peripherals, bare-metal C drivers, Python models of VHDL, synthesis checks, Xsim testbenches, VCD verification, or Zynq-7000/UltraScale fabric design. Also triggers on PLLs, DPLLs, NCOs, clock recovery, phase detectors, fractional-N architectures, PI loop filters, lock detection, DSP48E1 mapping, CDC synchronisers, and PS-PL SoC integration."
---

# SOCKS -- System-On-a-Chip Kit for Synthesis

## Pipeline Overview

The pipeline has 14 stages (0-13) executed in order. Each stage catches a class of bugs the next cannot.

```
Env -> Architecture -> VHDL -> Linter -> Audit -> Python TB ->
C Driver -> SV/Xsim TB -> VCD verify -> CSV verify -> Vivado synth -> Bash audit -> CLAUDE.md -> Self-Audit
```

---

## Stage Dispatch Table

| Stage | Name | Script / Action | Reference to Read |
|-------|------|----------------|-------------------|
| 0 | Environment Setup | `scripts/env.py` | -- |
| 1 | Architecture | `scripts/architecture.py` | `references/architecture-diagrams.md`, `references/vhdl.md` (saturation, DSP widths) |
| 2 | VHDL Authoring | *Claude writes code* | `references/vhdl.md` |
| 3 | VHDL Linter | `node <linter>/dist/lib/cli/cli.js` | `references/linter.md` |
| 4 | Synthesis Audit | `scripts/audit.py` | `references/synthesis.md` |
| 5 | Python Testbench | *Claude writes code* | `references/python-testbench.md` |
| 6 | Bare-Metal C Driver | *Claude writes code* | `references/baremetal.md` |
| 7 | SV/Xsim Testbench | *Claude writes code* + `scripts/xsim.py` | `references/xsim.md` |
| 8 | VCD Verification | `scripts/vcd_verify.py` | `references/vcd-verify.md` |
| 9 | CSV Cross-Check | `scripts/csv_crosscheck.py` | -- |
| 10 | Vivado Synthesis | `scripts/synth.py` | `references/synthesis.md` |
| 11 | Bash Audit | `scripts/bash_audit.py` | -- |
| 12 | CLAUDE.md | *Claude writes docs* | `references/project-structure.md` |
| 13 | SOCKS Self-Audit | `scripts/self_audit.py` | -- |

**For DPLL/PLL/NCO/clock recovery designs:** read `references/dpll.md` before Stage 1.

---

## Failure Recovery

When a stage fails, don't restart from Stage 0. Go back to the **producing stage**, fix the issue, then carry the fix forward through all downstream stages.

| Failing Stage | Re-entry | Rationale |
|---|---|---|
| 2 VHDL | 1 if design error, 2 if coding error | Architecture mistakes need block diagram update |
| 3 Linter | 2 → 3 | Fix style/warnings in VHDL, re-lint |
| 4 Audit | 2 → 3 → 4 | Fix VHDL, re-lint, re-audit |
| 5 Python TB | 5 if model bug, 2 → 3 → 4 → 5 if RTL bug | Model is the spec; RTL must match |
| 6 C Driver | 6 if driver bug, 2 → 3 → 4 if register map wrong | Register map flows from VHDL |
| 7 SV/Xsim TB | 7 if TB bug, 6 → 7 if DPI-C issue, 2 → 3 → ... if RTL bug | Root-cause determines re-entry |
| 8 VCD Verify | 7 → 8 if stimulus issue, 2 → 3 → ... if RTL bug | Independent check catches what SV self-checks miss |
| 9 CSV Cross-Check | 5 + 7 (align model and sim) | Divergence means model or RTL is wrong |
| 10 Vivado Synth | 2 → 3 → 4 if timing/resource issue | RTL restructuring needed |
| 11 Bash Audit | 11 (fix scripts in place) | No upstream impact |
| 12 CLAUDE.md | 12 (fix docs in place) | No upstream impact |

**Carry-forward rule:** every fix must propagate through all downstream stages. Fix VHDL → re-lint → re-audit → re-run Python TB → update C driver if register map changed → re-run SV TB → etc.

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
├── CLAUDE.md     # Project guide (Stage 12 output)
└── .gitignore    # Vivado/Xsim artifact ignores
```

---

## Stage Details

### Stage 0 -- Environment Setup

Run `scripts/env.py` to discover Vivado and verify tools.

### Stage 1 -- Architecture

Before writing VHDL, produce three deliverables:

**1. Architecture diagrams** — Read `references/architecture-diagrams.md`. Write two Mermaid diagrams into `docs/ARCHITECTURE.md` and render to PNG:
- **Data Flow** — modules/entities as subgraphs reflecting VHDL hierarchy, signal names on every edge, solid arrows for TX path, dashed for RX, loopback/external connections shown explicitly
- **Clocking** — sys_clk (PS FCLK_CLK0) fan-out to every rate-generating process, with derivation formulas and concrete numeric examples
- **Rate Summary table** — every clock/tick/bit-rate with its derivation and affected signals

These diagrams catch hierarchy, connectivity, and clock/rate mismatches before they become VHDL bugs. Every frequency in the design must appear with its derivation (e.g. "100 MHz / 100 = 1 MHz tick" or "freq_word × sys_clk / 2^32 = 1 MHz NCO").

**2. Resource analysis** — answer: What are the widest intermediates? Do any overflow VHDL integer range? How many DSP48E1 does each multiply need? What is the critical path depth?

Read `references/vhdl.md` for saturation constant patterns and multiply width rules.

### Stage 2 -- VHDL Authoring

Read `references/vhdl.md` before writing any VHDL. Key rules: architecture `rtl`, reset inside `rising_edge(clk)`, named processes `p_*`, state prefix `ST_*`, monitor ports `mon_*`, no `abs()` on signed, no `2**N` constants, no component declarations.

### Stage 3 -- VHDL Linter

Read `references/linter.md`. Run the VHDL linter on `src/` to catch style, convention, and type-checking issues. Fix all syntax errors and actionable warnings in your own code (ignore read-only external modules). This stage catches unused signals/generics before testbenches are written, reducing rework downstream.

### Stage 4 -- Synthesis Audit

Run `scripts/audit.py src/*.vhd`. 12 static checks. All must pass before proceeding. The audit catches synthesis hazards after VHDL and linting are clean — no time wasted testing code that won't synthesise.

### Stage 5 -- Python Testbench

Read `references/python-testbench.md`. The model mirrors VHDL at register-transfer level. Commit-at-end discipline: all reads from `self.X` (old), all writes to `n_` locals, commit block at end.

### Stage 6 -- Bare-Metal C Driver

Read `references/baremetal.md`. Generate C header + source from the VHDL register map. Standard API: init, enable/disable, load/read, status, W1C clear, polling wait, IRQ enable/disable. For designs with DPLLs, include runtime parameter computation (see `references/baremetal.md`, DPLL section).

The C driver is written before the SV testbench because DPI-C lets the TB call the driver's computation functions directly, keeping formulas in one place.

### Stage 7 -- SV/Xsim Testbench

Read `references/xsim.md`. Check all 7 Xsim rules (X1-X7). Use monitor ports for signal access. `always @(negedge clk)` for reference drivers. Never `2.0 ** 32`. After writing the SV testbench, compile and simulate via `scripts/xsim.py` -- no raw bash calls needed.

**DPI-C:** If the TB shares computation with the C driver (DPLL params, CRC tables, protocol encoding), place a `.c` file in `tb/`. The build script auto-discovers it, compiles with `xsc`, and links via `-sv_lib dpi`. See `references/xsim.md` for the pattern.

### Stage 8 -- VCD Verification

Independent verification from raw waveform data. Does not rely on SV self-checks. Read `references/vcd-verify.md` for the three-layer architecture.

### Stage 9 -- CSV Cross-Check

Compare SV simulation CSV against Python model output. Align by event count. Report first divergence.

### Stage 10 -- Vivado Synthesis

Run `scripts/synth.py`. Generates TCL, invokes Vivado batch mode, parses utilization/timing/DRC reports. All timing checks must show MET.

### Stage 11 -- Bash Audit

Run `scripts/bash_audit.py --project-dir .`. Scans all project shell scripts, Tcl files, and Makefiles for raw EDA tool calls (xvhdl, xvlog, xelab, xsim, vivado) that should be routed through SOCKS Python scripts instead. Also checks for `source settings64.sh` patterns, process substitution, and unsourced tool invocations. All checks must pass -- any raw tool call is a pipeline gap that needs a script.

### Stage 12 -- CLAUDE.md

Create project documentation with: What This Is, Architecture, Files table, Build & Test, Synthesis Results (from Stage 10 reports), Vivado version, Conventions. This is last because it documents everything including the C driver from Stage 6.

### Stage 13 -- SOCKS Self-Audit

Run `scripts/self_audit.py`. Validates internal consistency of the SOCKS skill itself: checks that all scripts referenced in SKILL.md exist, all reference files exist, no stale stage-numbered filenames remain, no absolute/user-specific paths leaked in, and the orchestrator dispatch table matches actual script files. This stage also runs automatically as a post-check after every orchestrator invocation, even for single-stage runs.

---

## Methodology Principles

1. **Read the full file before modifying it.** Never edit VHDL based on a summary.
2. **Audit scripts are not throwaway code.** They become regression tests.
3. **Carry every fix forward through all layers.** VHDL fix -> Python model fix -> SV TB fix.
4. **Verify comments with the same rigour as code.** Wrong constants in headers become bugs.
5. **Never use abs() on signed values.** Two-sided comparison in VHDL, Python, and SV.
6. **The Python model is the spec.** When VHDL and Python disagree, fix the VHDL (unless the Python has a demonstrable commit-order bug).
