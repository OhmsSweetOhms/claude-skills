---
name: socks
description: "System-On-a-Chip Kit for Synthesis. Use this skill for any FPGA/SoC design task: VHDL RTL, AXI-Lite interfaces, register maps, memory-mapped peripherals, bare-metal C drivers, Python models of VHDL, synthesis checks, Xsim testbenches, VCD verification, or Zynq-7000/UltraScale fabric design. Also triggers on PLLs, DPLLs, NCOs, clock recovery, phase detectors, fractional-N architectures, PI loop filters, lock detection, DSP48E1 mapping, CDC synchronisers, and PS-PL SoC integration."
---

# SOCKS -- System-On-a-Chip Kit for Synthesis

## Pipeline Overview

The pipeline has 14 stages (0-13). Each stage catches a class of bugs the next cannot.

```
Stage 0:  Environment Setup                              AUTOMATED
Stage 1:  Architecture (RTL + TB) -> Plan Mode approval  BOTH
          +-------------------------------------------------------+
          |  DESIGN LOOP (2-9) -- model-driven                    |
          |                                                       |
          |  Stage 2:  Write/Modify RTL                GUIDANCE   |
          |  Stage 3:  VHDL Linter                     GUIDANCE   |
          |  Stage 4:  Synthesis Audit                 AUTOMATED  |
          |  Stage 5:  Python Testbench                BOTH       |
          |  Stage 6:  Bare-Metal C Driver             GUIDANCE   |
          |  Stage 7:  SV/Xsim Testbench              BOTH       |
          |  Stage 8:  VCD Verification                AUTOMATED  |
          |  Stage 9:  CSV Cross-Check                 AUTOMATED  |
          |                                                       |
          |  Claude decides re-entry on failure.                  |
          |  Exits when: architecture met + all agree.            |
          |  If stuck (same fix tried twice) -> ask user.         |
          +-------------------------------------------------------+
Stage 10: Vivado Synthesis                               AUTOMATED
Stage 11: Bash Audit                                     AUTOMATED
Stage 12: CLAUDE.md Documentation                        GUIDANCE
Stage 13: SOCKS Self-Audit                               AUTOMATED
```

---

## Stage Dispatch Table

| Stage | Name | Script / Action | Reference to Read |
|-------|------|----------------|-------------------|
| 0 | Environment Setup | `scripts/env.py` | -- |
| 1 | Architecture | `scripts/architecture.py` + guidance | `references/architecture-diagrams.md`, `references/vhdl.md` (saturation, DSP widths) |
| 2 | Write/Modify RTL | *Claude writes code* | `references/vhdl.md` |
| 3 | VHDL Linter | `node <linter>/dist/lib/cli/cli.js` | `references/linter.md` |
| 4 | Synthesis Audit | `scripts/audit.py` | `references/synthesis.md` |
| 5 | Python Testbench | `scripts/python_rerun.py` + *Claude writes code* | `references/python-testbench.md` |
| 6 | Bare-Metal C Driver | *Claude writes code* | `references/baremetal.md` |
| 7 | SV/Xsim Testbench | `scripts/xsim.py` + *Claude writes code* | `references/xsim.md` |
| 8 | VCD Verification | `scripts/vcd_verify.py` | `references/vcd-verify.md` |
| 9 | CSV Cross-Check | `scripts/csv_crosscheck.py` | -- |
| 10 | Vivado Synthesis | `scripts/synth.py` | `references/synthesis.md` |
| 11 | Bash Audit | `scripts/bash_audit.py` | -- |
| 12 | CLAUDE.md | *Claude writes docs* | `references/project-structure.md` |
| 13 | SOCKS Self-Audit | `scripts/self_audit.py` | -- |

**For DPLL/PLL/NCO/clock recovery designs:** read `references/dpll.md` before Stage 1.

---

## Design Loop (Stages 2-9)

Stages 2-9 form the **design loop**. Stage 1 establishes the architecture and plan mode approval -- this is the spec. Claude iterates through 2-9 until the RTL meets the architecture and all verification outputs agree (Python TB, Xsim, VCD, CSV).

**Re-entry on failure:** Claude reasons about the root cause and re-enters at the appropriate stage:

- **RTL bug** -> Stage 2 (modify RTL) -> 3 -> 4 -> ...
- **Python model bug** -> Stage 5 -> 7 -> 8 -> 9
- **SV testbench bug** -> Stage 7 -> 8 -> 9
- **Register map change** -> Stage 2 -> ... -> 6 (update C driver) -> 7 -> ...
- **C driver bug** -> Stage 6 -> 7 -> ...

**Propagation:** When the user asks to update a single sim stage, ask if they want to update all downstream stages too.

**Circular logic detection:** If you have tried a fix and ended up back at the same failure, or tried two different approaches and reverted to where you started -- stop and ask the user. Do not keep iterating on the same problem.

**Exit criteria:** The design loop exits when the architecture from Stage 1 is met and all verification stages (5, 7, 8, 9) pass with consistent results.

---

## Failure Recovery

On failure, reason about the root cause. The producing stage is where you re-enter.

- **Architecture errors** (bad plan) -> revisit Stage 1 with the user
- **The Python model is the reference** -- when VHDL and Python disagree, fix the VHDL, except when the Python model can be shown to misrepresent VHDL timing mechanisms
- **External module audit warnings** are non-blocking (exit code 2)
- **Stages 10-13 failures** do not loop back into 2-9:
  - Stage 10 timing/resource failure -> may need RTL restructuring (revisit Stage 2 with user)
  - Stage 11 -> fix scripts in place
  - Stage 12 -> fix docs in place
  - Stage 13 -> fix skill consistency issues

**Carry-forward rule:** every fix must propagate through all downstream stages. Fix VHDL -> re-lint -> re-audit -> re-run Python TB -> update C driver if register map changed -> re-run Xsim -> VCD -> CSV.

---

## Orchestrator

Run stages individually or as a pipeline:

```bash
python scripts/socks.py --project-dir . --stages automated
python scripts/socks.py --project-dir . --stages 0,4,7
python scripts/socks.py --project-dir . --stages 4 --files src/*.vhd
python scripts/socks.py --project-dir . --stages 10 --top my_module --part xc7z020clg484-1
```

**Stage keywords:**
- `--stages automated` -- all stages with scripts (default): 0, 1, 4, 5, 7, 8, 9, 10, 11, 13
- `--stages 5,7,8` -- specific stages, comma-separated (no auto-expansion)

Guidance-only stages (2, 3, 6, 12) are driven by Claude reading SKILL.md, not by the orchestrator.

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

Before writing VHDL, produce three deliverables. This stage covers both RTL and testbench architecture, and ends with **plan mode approval** before proceeding.

**1. Architecture diagrams** -- Read `references/architecture-diagrams.md`. Write two Mermaid diagrams into `docs/ARCHITECTURE.md` and render to PNG:
- **Data Flow** -- modules/entities as subgraphs reflecting VHDL hierarchy, signal names on every edge, solid arrows for TX path, dashed for RX, loopback/external connections shown explicitly
- **Clocking** -- sys_clk (PS FCLK_CLK0) fan-out to every rate-generating process, with derivation formulas and concrete numeric examples
- **Rate Summary table** -- every clock/tick/bit-rate with its derivation and affected signals

These diagrams catch hierarchy, connectivity, and clock/rate mismatches before they become VHDL bugs. Every frequency in the design must appear with its derivation (e.g. "100 MHz / 100 = 1 MHz tick" or "freq_word x sys_clk / 2^32 = 1 MHz NCO").

**2. Resource analysis** -- answer: What are the widest intermediates? Do any overflow VHDL integer range? How many DSP48E1 does each multiply need? What is the critical path depth?

Read `references/vhdl.md` for saturation constant patterns and multiply width rules.

### Stage 2 -- Write/Modify RTL

Read `references/vhdl.md` before writing any VHDL. This stage is entered on both initial creation and design loop iteration. Key rules: architecture `rtl`, reset inside `rising_edge(clk)`, named processes `p_*`, state prefix `ST_*`, monitor ports `mon_*`, no `abs()` on signed, no `2**N` constants, no component declarations.

### Stage 3 -- VHDL Linter

Read `references/linter.md`. Run the VHDL linter on `src/` to catch style, convention, and type-checking issues. Fix all syntax errors and actionable warnings in your own code (ignore read-only external modules). This stage catches unused signals/generics before testbenches are written, reducing rework downstream.

### Stage 4 -- Synthesis Audit

Run `scripts/audit.py src/*.vhd`. 12 static checks. All must pass before proceeding. The audit catches synthesis hazards after VHDL and linting are clean -- no time wasted testing code that won't synthesise.

### Stage 5 -- Python Testbench

Read `references/python-testbench.md`. The model mirrors VHDL at register-transfer level. Commit-at-end discipline: all reads from `self.X` (old), all writes to `n_` locals, commit block at end.

### Stage 6 -- Bare-Metal C Driver

Read `references/baremetal.md`. Generate C header + source from the VHDL register map. Standard API: init, enable/disable, load/read, status, W1C clear, polling wait, IRQ enable/disable. For designs with DPLLs, include runtime parameter computation (see `references/baremetal.md`, DPLL section).

The C driver is written before the SV testbench because DPI-C lets the TB call the driver's computation functions directly, keeping formulas in one place.

### Stage 7 -- SV/Xsim Testbench

Read `references/xsim.md`. Check all 7 Xsim rules (X1-X7). Use monitor ports for signal access. `always @(negedge clk)` for reference drivers. Never `2.0 ** 32`. After writing the SV testbench, compile and simulate via `scripts/xsim.py` -- no raw bash calls needed. Stage 7 always enables `--vcd` unconditionally.

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
