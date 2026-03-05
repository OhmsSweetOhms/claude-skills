---
name: socks
description: "System-On-a-Chip Kit for Synthesis. Use this skill for any FPGA/SoC design task: VHDL RTL, AXI-Lite interfaces, register maps, memory-mapped peripherals, bare-metal C drivers, Python models of VHDL, synthesis checks, Xsim testbenches, VCD verification, or Zynq-7000/UltraScale fabric design. Also triggers on PLLs, DPLLs, NCOs, clock recovery, phase detectors, fractional-N architectures, PI loop filters, lock detection, DSP48E1 mapping, CDC synchronisers, and PS-PL SoC integration."
---

# SOCKS -- System-On-a-Chip Kit for Synthesis

## Pipeline Overview

```
Stage 0:  Environment Setup                              AUTOMATED
Stage 1:  Architecture (RTL + TB) -> Plan Mode approval  BOTH
          +-------------------------------------------------------+
          |  DESIGN LOOP (2-9) -- see references/design-loop.md  |
          +-------------------------------------------------------+
Stage 10: Vivado Synthesis                               AUTOMATED
Stage 11: Bash Audit                                     AUTOMATED
Stage 12: CLAUDE.md Documentation                        GUIDANCE
Stage 13: SOCKS Self-Audit                               AUTOMATED
```

**Entering the design loop:** Read `references/design-loop.md` before Stage 2.
It contains all loop control, re-entry, failure recovery, and per-stage details
for stages 2-9.

---

## Stage Dispatch Table

| Stage | Name | Script / Action | Reference |
|-------|------|----------------|-----------|
| 0 | Environment Setup | `scripts/env.py` | -- |
| 1 | Architecture | `scripts/architecture.py` + guidance | `references/architecture-diagrams.md`, `references/vhdl.md` |
| 2-9 | **Design Loop** | *See `references/design-loop.md`* | *Per stage, in design-loop.md* |
| 10 | Vivado Synthesis | `scripts/synth.py` | `references/synthesis.md` |
| 11 | Bash Audit | `scripts/bash_audit.py` | -- |
| 12 | CLAUDE.md | *Claude writes docs* | `references/project-structure.md` |
| 13 | SOCKS Self-Audit | `scripts/self_audit.py` | -- |

**For DPLL/PLL/NCO/clock recovery designs:** read `references/dpll.md` before Stage 1.

---

## Orchestrator

```bash
python scripts/socks.py --project-dir . --stages automated
python scripts/socks.py --project-dir . --stages 0,4,7
python scripts/socks.py --project-dir . --stages 4 --files src/*.vhd
python scripts/socks.py --project-dir . --stages 10 --top my_module --part xc7z020clg484-1
```

**Stage keywords:**
- `--stages automated` -- all stages with scripts: 0, 1, 4, 5, 7, 8, 9, 10, 11, 13
- `--stages 5,7,8` -- specific stages, comma-separated (no auto-expansion)

Guidance-only stages (2, 3, 6, 12) are driven by Claude, not the orchestrator.

---

## Project Structure

```
project_name/
├── src/          # VHDL source (synthesisable RTL)
├── tb/           # Python TB, SV TB, DPI-C bridge
├── sw/           # C/C++ bare-metal drivers
├── sim/          # Build scripts, TCL, synthesis reports
├── CLAUDE.md     # Project guide (Stage 12 output)
└── .gitignore    # Vivado/Xsim artifact ignores
```

See `references/project-structure.md` for full conventions.

---

## Stage Details (0-1, 10-13)

### Stage 0 -- Environment Setup

Run `scripts/env.py` to discover Vivado and verify tools.

### Stage 1 -- Architecture

Before writing VHDL, produce three deliverables. Ends with **plan mode approval**
before entering the design loop.

**1. Architecture diagrams** -- Read `references/architecture-diagrams.md`. Write
two Mermaid diagrams into `docs/ARCHITECTURE.md`:
- **Data Flow** -- modules as subgraphs, signal names on every edge, solid arrows
  for TX, dashed for RX, loopback/external connections explicit
- **Clocking** -- sys_clk fan-out to every rate-generating process, derivation
  formulas and concrete numeric examples
- **Rate Summary table** -- every clock/tick/bit-rate with derivation and affected signals

Every frequency must appear with its derivation (e.g. "100 MHz / 100 = 1 MHz tick").

**2. Resource analysis** -- widest intermediates, integer overflow risk, DSP48E1
count per multiply, critical path depth.

Read `references/vhdl.md` for saturation constant patterns and multiply width rules.

### Stage 10 -- Vivado Synthesis

Run `scripts/synth.py`. All timing checks must show MET. Timing/resource failure
may require RTL restructuring -- revisit Stage 2 with the user.

### Stage 11 -- Bash Audit

Run `scripts/bash_audit.py --project-dir .`. Scans all shell scripts, Tcl files,
and Makefiles for raw EDA tool calls that should route through SOCKS scripts.
Also checks for `source settings64.sh` patterns and process substitution. All
checks must pass.

### Stage 12 -- CLAUDE.md

Create project documentation: What This Is, Architecture, Files table, Build &
Test, Synthesis Results (from Stage 10 reports), Vivado version, Conventions.
Written last because it documents everything including the C driver from Stage 6.

### Stage 13 -- SOCKS Self-Audit

Run `scripts/self_audit.py`. Validates internal consistency: all scripts
referenced in SKILL.md exist, all reference files exist, no stale
stage-numbered filenames, no absolute paths leaked in, orchestrator dispatch
table matches actual script files. Runs automatically after every orchestrator
invocation.
