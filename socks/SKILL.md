---
name: socks
description: "System-On-a-Chip Kit for Synthesis. Use this skill for any FPGA/SoC design task: VHDL RTL, AXI-Lite interfaces, register maps, memory-mapped peripherals, bare-metal C drivers, Python models of VHDL, synthesis checks, Xsim testbenches, VCD verification, or Zynq-7000/UltraScale fabric design. Also triggers on PLLs, DPLLs, NCOs, clock recovery, phase detectors, fractional-N architectures, PI loop filters, lock detection, DSP48E1 mapping, CDC synchronisers, and PS-PL SoC integration."
---

# SOCKS -- System-On-a-Chip Kit for Synthesis

## Workflows

Six entry points. Parse the user's `/socks` message for flags:

| Invocation | What happens |
|---|---|
| `/socks --design [scope]` | Discovery conversation, then full pipeline (0-13) |
| `/socks --test [scope]` | Ask what to test/enhance, then sim stages (4,5,7,8,9) |
| `/socks --architecture [scope]` | Ask what architecture changes, then full pipeline (0-13) |
| `/socks --bughunt [scope]` | Ask what bug, then sim+synth stages (3-10) |
| `/socks --hil --top <entity>` | Hardware-in-the-loop: build, program, test on Zynq-7000 (0,10,14-19) |
| `/socks --migrate` | Claude-driven project migration (legacy SOCKS or flat/3rd-party) |
| `/socks` *(no flags)* | Ask the user which workflow they want |

**Scope** is `module`, `block`, or `project`. If the user doesn't specify
scope, ask: "What scope? (module / block / project)". Scope definitions:
- **module** -- single VHDL entity (e.g. CRC engine, edge detector)
- **block** -- multi-module subsystem (e.g. UART controller with TX, RX, FIFO, reg map)
- **project** -- full SoC or multi-block integration (e.g. Zynq PS-PL)

**Bare `/socks`:** When the user types `/socks` with no flags, present:
> Which workflow?
> 1. `--design` -- New design from scratch (includes discovery phase)
> 2. `--test` -- Edit and run testbenches
> 3. `--architecture` -- Change architecture, re-run full pipeline
> 4. `--bughunt` -- Fix a bug, verify with sim + synthesis
> 5. `--hil` -- Hardware-in-the-loop test on FPGA board (requires `hil.json`)
> 6. `--migrate` -- Migrate a project to SOCKS layout (legacy or flat/3rd-party)

Then proceed with their choice.

### Discovery Phase (`--design` only)

Before Stage 1, run a discovery conversation to produce `docs/DESIGN-INTENT.md`.

1. Read `references/discovery.md` for the core questions (scope-specific)
2. Ask each core question, one at a time or in small batches
3. Analyze the user's answers and ask generative follow-up questions
   (clarifications triggered by their specific answers)
4. When the design space is sufficiently constrained, synthesize all answers
   into `docs/DESIGN-INTENT.md` using the template in `references/discovery.md`
5. Present the intent document to the user for approval
6. If the user requests changes, iterate
7. Once approved, run: `python scripts/socks.py --project-dir . --design --scope {scope}`

**Important:** Discovery is a conversation, not a script. Claude drives the
questions and synthesis. The pipeline only starts after the user approves
the design intent.

### Other Workflows

- **`--test`:** Ask the user what they want to test or enhance. Help them edit
  `tb/` files. Then run: `python scripts/socks.py --project-dir . --test`
- **`--architecture`:** Ask what architecture changes. Help edit
  `docs/ARCHITECTURE.md` and `src/` files. Then run:
  `python scripts/socks.py --project-dir . --architecture`
- **`--bughunt`:** Ask what bug they're hunting. Analyze, help edit `src/` files.
  Then run: `python scripts/socks.py --project-dir . --bughunt`
- **`--hil`:** Read `references/hil.md` for prerequisites and `hil.json` schema.
  Requires board connected via JTAG + UART, `hil.json` in project root. Then run:
  `python scripts/socks.py --project-dir . --hil --top <entity>`
- **`--migrate`:** Read `references/project-migration.md`. Classify the project
  (legacy SOCKS vs flat/3rd-party), run `scripts/clean.py --project-dir . --all`
  to remove generated artifacts, then follow the migration workflow. This is
  Claude-driven — no pipeline stages run automatically.

---

## Pipeline Overview

```
Stage 0:  Environment Setup                              AUTOMATED
          dashboard -- check project.json on re-entry
Stage 1:  Architecture (RTL + TB) -> Plan Mode approval  BOTH
          +-------------------------------------------------------+
          |  DESIGN LOOP (2-9) -- see references/design-loop.md  |
          |  references/regmap.md -- after any register change    |
          +-------------------------------------------------------+
Stage 10: Vivado Synthesis                               AUTOMATED
          references/constraints.md -- generate XDC before first synthesis
          references/timing.md -- diagnose and fix if VIOLATED
Stage 11: Bash Audit                                     AUTOMATED
Stage 12: CLAUDE.md Documentation                        GUIDANCE
Stage 13: SOCKS Self-Audit                               AUTOMATED
          +-------------------------------------------------------+
          |  HIL FLOW (14-19) -- see references/hil.md           |
          |  requires hil.json in project root                    |
          +-------------------------------------------------------+
Stage 14: HIL: Vivado Project                            AUTOMATED
Stage 15: HIL: Implementation                            AUTOMATED
Stage 16: HIL: Firmware Build                            BOTH
Stage 17: HIL: Program + Test (user gate)                AUTOMATED
Stage 18: HIL: ILA Capture (VCD required)                   AUTOMATED
Stage 19: HIL: ILA Verify (VCD required)                    AUTOMATED
```

**Entering the design loop:** Read `references/design-loop.md` before Stage 2.
It contains all loop control, re-entry, failure recovery, and per-stage details
for stages 2-9.

**Entering the HIL flow:** Read `references/hil.md` before Stage 14. It contains
`hil.json` schema, board presets, wiring rules, per-stage details, ILA trigger
plan authoring, and troubleshooting.

**Running logs:** Throughout any workflow, Claude must maintain two running logs:
1. **Generated file log** -- all files generated by scripts or Claude, with their
   dependencies. Reviewed after the last stage for extraction into the SOCKS skill.
2. **Copied file log** -- all files copied during the workflow: source path,
   destination path, and why. Reviewed after the last stage to identify patterns
   that should be templatized or automated.

---

## Stage Dispatch Table

| Stage | Name | Script / Action | Reference |
|-------|------|----------------|-----------|
| 0 | Environment Setup | `scripts/env.py` | -- |
| 0+ | **Project Status** | Check `build/state/project.json` or run dashboard | *On re-entry to existing project* |
| 1 | Architecture | `scripts/architecture.py` + guidance | `references/architecture-diagrams.md`, `references/vhdl.md`, `references/axi-lite.md` |
| 2-9 | **Design Loop** | *See `references/design-loop.md`* | *`references/regmap.md` after register changes* |
| 10a | **XDC Constraints** | Read `references/constraints.md`, generate XDC | *Before first synthesis or when missing XDC* |
| 10b | Vivado Synthesis | `scripts/synth.py` | `references/synthesis.md` |
| 10c | **Timing Closure** | Read `references/timing.md`, diagnose + fix | *Only if Stage 10b shows VIOLATED* |
| 11 | Bash Audit | `scripts/bash_audit.py` | -- |
| 12 | CLAUDE.md | *Claude writes docs* | `references/project-structure.md`, `references/claude_notes.md` |
| 13 | SOCKS Self-Audit | `scripts/self_audit.py` | -- |
| 14-19 | **HIL Flow** | *See `references/hil.md`* | *Requires `hil.json` in project root* |
| 14 | HIL: Vivado Project | `scripts/hil/hil_project.py` | `references/hil.md` |
| 15 | HIL: Implementation | `scripts/hil/hil_impl.py` | `references/hil.md` |
| 16 | HIL: Firmware Build | `scripts/hil/hil_firmware.py` + guidance | `references/hil.md` (§ Firmware Authoring Guide) |
| 17 | HIL: Program + Test | `scripts/hil/hil_run.py` | `references/hil.md` |
| 18 | HIL: ILA Capture | `scripts/hil/hil_ila.py` | `references/hil.md` (VCD required) |
| 19 | HIL: ILA Verify | `scripts/hil/hil_verify.py` | `references/hil.md` (VCD required) |

**For DPLL/PLL/NCO/clock recovery designs:** read `references/dpll.md` before Stage 1.

---

## Orchestrator

**Workflow entry points (required — always use these, never call stage scripts directly):**
```bash
python scripts/socks.py --project-dir . --design --scope block
python scripts/socks.py --project-dir . --test
python scripts/socks.py --project-dir . --architecture --scope module
python scripts/socks.py --project-dir . --bughunt
python scripts/socks.py --project-dir . --migrate   # Claude-driven, no automated stages
python scripts/socks.py --project-dir . --hil --top <entity>
```

**Explicit stage control (legacy):**
```bash
python scripts/socks.py --project-dir . --stages automated
python scripts/socks.py --project-dir . --stages 0,4,7
python scripts/socks.py --project-dir . --stages 4 --files src/*.vhd
python scripts/socks.py --project-dir . --stages 10 --top my_module --part xc7z020clg484-1
```

**Workflows map to stages:**
- `--design` -- 0, 1, 3, 4, 5, 7, 8, 9, 10, 11, 13 (all automated)
- `--test` -- 4, 5, 7, 8, 9 (sim only)
- `--architecture` -- 0, 1, 3, 4, 5, 7, 8, 9, 10, 11, 13 (full re-architecture)
- `--bughunt` -- 3, 4, 5, 7, 8, 9, 10 (sim + synthesis)
- `--hil` -- 0, 10, 14, 15, 16, 17, 18, 19 (hardware-in-the-loop, --top required)
- `--migrate` -- Claude-driven (`references/project-migration.md`), no automated stages

**Stage keywords:**
- `--stages automated` -- all stages with scripts: 0, 1, 3, 4, 5, 7, 8, 9, 10, 11, 13, 14, 15, 16, 17, 18, 19
- `--stages 5,7,8` -- specific stages, comma-separated (no auto-expansion)

Guidance-only stages (2, 6, 12) are driven by Claude, not the orchestrator.

**Never call stage scripts directly** (e.g. `xsim.py`, `audit.py`, `python_rerun.py`).
Always route through `socks.py` — this is mandatory because:
- Stage results are only tracked when run through the orchestrator
- Hash-based re-entry (skip unchanged stages) only works via `socks.py`
- Direct calls corrupt pipeline state and break `--bughunt` recovery

**Full rebuild:** When the user asks to "build", "rebuild", or "recompile",
run `scripts/build.py` which handles clean + full pipeline. **Do not manually
invoke individual stage scripts for a build request.**

---

## Project Structure

```
project_name/
├── src/               # VHDL source (synthesisable RTL)
├── tb/                # Python TB, SV TB, DPI-C bridge
├── sw/                # C/C++ bare-metal drivers
├── build/
│   ├── sim/           # Simulation scripts + Xsim artifacts
│   ├── synth/         # Synthesis TCL + Vivado reports
│   ├── hil/           # HIL build outputs (Vivado project, firmware, ILA CSVs)
│   ├── state/         # project.json (pipeline state)
│   ├── logs/          # Legacy pipeline logs
│   └── artifacts/     # Claude scratch space
├── docs/              # Architecture diagrams + README
├── hil.json           # HIL config (optional, triggers stages 14-19)
├── CLAUDE.md          # Project guide (Stage 12 output)
└── .gitignore         # Vivado/Xsim artifact ignores
```

See `references/project-structure.md` for full conventions.

---

## Stage Details (0-1, 10-13)

### Stage 0 -- Environment Preflight

Run `scripts/env.py` to verify everything the pipeline needs:

1. **Vivado** -- discovers `settings64.sh`, verifies all 5 EDA tools
   (`xvhdl`, `xvlog`, `xelab`, `xsim`, `vivado`), warns if version != 2023.2
2. **Python** -- version >= 3.8, all stdlib modules used by SOCKS scripts
3. **SOCKS skill** -- all scripts and reference files exist, SKILL.md valid
4. **Project structure** (with `--project-dir`) -- `src/` required;
   `tb/`, `build/`, `sw/`, `docs/`, `CLAUDE.md`, `.gitignore` checked if present

On a new machine, run it standalone first:
```bash
python3 scripts/env.py
python3 scripts/env.py --project-dir /path/to/my_project
```

Exit codes: 0 = all passed, 1 = critical failure, 2 = warnings only.

**On re-entry to an existing project:** check `build/state/project.json`
for current pipeline state, or run `scripts/dashboard.py` for a visual
overview of stage results, timing, and next-action suggestions.

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

**Stage 10a -- XDC Constraints:** Before the first synthesis run, or if the
project has no `.xdc` file and `synth_timing.tcl` uses inline constraints,
read `references/constraints.md` and follow the procedure to generate proper
XDC timing constraints. This ensures async inputs have `set_false_path` to
sync1 registers (not just `get_ports`), clock definitions are correct, and
monitor ports are excluded.

**Stage 10b -- Synthesis:** Run `scripts/synth.py`. All timing checks must
show MET.

**Stage 10c -- Timing Closure:** If Stage 10b reports VIOLATED timing, read
`references/timing.md` and follow the diagnostic procedure. Parse the critical
path report, diagnose root causes (carry chain depth, logic levels, fan-out),
and recommend RTL fixes. After applying fixes, re-run synthesis to verify.
If the critical path is inside a read-only external module, flag this and
discuss constraint-based workarounds.

Do not proceed to Stage 11 until all timing checks show MET (or the user
explicitly accepts the violation with a documented rationale in CLAUDE.md).

### Stage 11 -- Bash Audit

Run `scripts/bash_audit.py --project-dir .`. Scans all shell scripts, Tcl files,
and Makefiles for raw EDA tool calls that should route through SOCKS scripts.
Also checks for `source settings64.sh` patterns and process substitution. All
checks must pass.

### Stage 12 -- CLAUDE.md

Read `references/claude_notes.md` for the comprehensive content template.
Create project documentation: entity overview, architecture, file inventory,
naming conventions, build commands, test overview, synthesis results, known
limitations, decision boundaries, and tech stack. Written last because it
documents everything including the C driver from Stage 6.

After Stage 19 (HIL), Claude updates CLAUDE.md with HIL results (see
`references/claude_notes.md` § "Post-HIL Update").

### Stage 13 -- SOCKS Self-Audit

Run `scripts/self_audit.py`. Validates internal consistency: all scripts
referenced in SKILL.md exist, all reference files exist, no stale
stage-numbered filenames, no absolute paths leaked in, orchestrator dispatch
table matches actual script files. Runs automatically after every orchestrator
invocation.
