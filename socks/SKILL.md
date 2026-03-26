---
name: socks
description: "System-On-a-Chip Kit for Synthesis. Use this skill for any FPGA/SoC design task: VHDL RTL, AXI-Lite interfaces, register maps, memory-mapped peripherals, bare-metal C drivers, Python models of VHDL, synthesis checks, Xsim testbenches, VCD verification, or Zynq-7000/UltraScale fabric design. Also triggers on PLLs, DPLLs, NCOs, clock recovery, phase detectors, fractional-N architectures, PI loop filters, lock detection, DSP48E1 mapping, CDC synchronisers, and PS-PL SoC integration."
---

# SOCKS -- System-On-a-Chip Kit for Synthesis

## Entry Point: `/socks [directory]`

When invoked as `/socks` or `/socks <path>`:

1. Resolve directory (default: cwd)
2. If `socks.json` exists in directory → **single-project mode**
3. Else if any immediate subdirectory has `socks.json` → **multi-project mode**
4. Else → **new project mode** (offer to run discovery)

### Single-project mode

Run: `python3 scripts/status.py --project-dir <dir>` (human output)
Then: `python3 scripts/status.py --json --project-dir <dir>` (parse for options)

Present a numbered menu based on the `suggestions` array from JSON output.
Each suggestion has a `priority` field: `"recommended"` (state-driven) or
`"available"` (always-present workflow). Present recommended items first,
then available items. Mark the top recommended item with "→ I recommend..."

Example:

> What would you like to do?
> 1. Re-run from Stage 13 (SOCKS Self-Audit) — last run FAILED
> 2. Rebuild (sources changed since last build)
> 3. Run full design workflow
> 4. Run tests
> 5. Architecture workflow
> 6. Bug hunt + verify
> 7. Run HIL
> 8. Migrate project layout
> → I recommend option 1 because Stage 13 failed and no source
>   files have changed, so a re-run should resolve it.

Map suggestion actions to orchestrator commands:
- `rerun_stage` → `python scripts/socks.py --project-dir <dir> --stages <stage>`
- `rebuild` → `python scripts/socks.py --project-dir <dir> --design --scope <scope>`
- `design` → `python scripts/socks.py --project-dir <dir> --design --scope <scope>`
- `test` → `python scripts/socks.py --project-dir <dir> --test`
- `architecture` → `python scripts/socks.py --project-dir <dir> --architecture --scope <scope>`
- `bughunt` → `python scripts/socks.py --project-dir <dir> --bughunt`
- `validate` → `python scripts/socks.py --project-dir <dir> --validate`
- `migrate` → `python scripts/socks.py --project-dir <dir> --migrate`

### Multi-project mode

Run: `python3 scripts/status.py --scan --project-dir <dir>`

Display summary table from JSON output:

| Project | Scope | Pass | Warn | Fail | Last Workflow |
|---------|-------|------|------|------|---------------|

Ask user to pick a project, then enter single-project mode for that project.

### New project mode

No `socks.json` found in directory or subdirectories. Offer:

> No SOCKS project found here. Want to create one?
> → This starts the `--design` discovery workflow.

Then proceed with the `--design` flow (ask scope, run discovery).

---

## Workflows

Seven workflow entry points. These are the execution paths that the entry point
routes into. Parse the user's `/socks` message for flags:

| Invocation | What happens |
|---|---|
| `/socks --design [scope]` | Discovery conversation, then full pipeline (0-13) |
| `/socks --test [scope]` | Ask what to test/enhance, then sim stages (4,5,7,8,9) |
| `/socks --architecture [scope]` | Ask what architecture changes, then full pipeline (0-13) |
| `/socks --bughunt [scope]` | Ask what bug, then sim+synth stages (3-10) |
| `/socks --validate [scope]` | Full validation: env + sim + synth + audit + HIL (skips HIL if no hardware) |
| `/socks --migrate` | Claude-driven project migration (legacy SOCKS or flat/3rd-party) |
| `/socks` *(no flags)* | Status-first entry point (see above) |

**Scope** is `module` or `system`. If the user doesn't specify
scope, ask: "What scope? (module / system)". Scope definitions:
- **module** -- one or more VHDL entities forming a self-contained peripheral (e.g. CRC engine, SPI master with AXI-Lite wrapper and register map, UART controller with TX/RX cores)
- **system** -- SoC integration (Xilinx IP + optional custom modules from separate designs)

### Discovery Phase (`--design` only)

Before Stage 1, run a discovery conversation to produce `docs/DESIGN-INTENT.md`.

1. Read `references/discovery.md` for module scope, or
   `references/discovery-system.md` for system scope
2. Ask each core question, one at a time or in small batches
3. Analyze the user's answers and ask generative follow-up questions
   (clarifications triggered by their specific answers)
4. When the design space is sufficiently constrained, synthesize all answers
   into `docs/DESIGN-INTENT.md` using the template in `references/discovery.md`
5. Present the intent document to the user for approval
6. If the user requests changes, iterate
7. Once approved, **immediately** run the orchestrator — do not author any
   other files first:
   `python scripts/socks.py --project-dir . --design --scope {scope}`
8. The orchestrator creates `build/state/project.json` (pipeline state tracking),
   runs automated stages, and prints guidance for manual stages. Only after the
   orchestrator runs should you begin authoring deliverables for guidance stages.

**Important:** Discovery is a conversation, not a script. Claude drives the
questions and synthesis. The pipeline only starts after the user approves
the design intent.

### Guidance Stage Protocol

When the orchestrator reaches a guidance-only stage (e.g. Stage 20, Stage 12),
it prints `WAITING` with a list of required files and stops the pipeline.
Claude's job is to fulfill that stage and then re-run the orchestrator so
automated stages resume:

1. Read the orchestrator output — it names the stage and missing files
2. Read the reference document for that stage (see Stage Dispatch Table)
3. Author all required deliverables for the stage
4. Re-run the orchestrator with the same command — it detects the new files,
   marks the guidance stage PASS, and continues to the next stage
5. Repeat until the pipeline completes

**Do not skip the re-run.** Automated stages after a guidance stage (e.g.
Stage 10 after Stage 20) depend on deliverables authored during the guidance
stage. The orchestrator validates these exist before proceeding.

### Other Workflows

- **`--test`:** Ask the user what they want to test or enhance. Help them edit
  `tb/` files. Then run: `python scripts/socks.py --project-dir . --test`
- **`--architecture`:** Ask what architecture changes. Help edit
  `docs/ARCHITECTURE.md` and `src/` files. Then run:
  `python scripts/socks.py --project-dir . --architecture`
- **`--bughunt`:** Ask what bug they're hunting. Analyze, help edit `src/` files.
  Then run: `python scripts/socks.py --project-dir . --bughunt`
- **`--validate`:** Full end-to-end validation: runs the union of design + HIL
  stages. HIL stages (15, 17, 18, 19) gracefully skip when no hardware is
  detected (Stage 0 persists hardware capabilities). `hil.json` is auto-generated
  by Stage 0 if missing.

  **Missing generated files:** When `--validate` stops because a generated file
  is missing (guidance stage WAITING or a script that expects a Claude-authored
  deliverable like `docs/TEST-INTENT.md`), Claude should:
  1. Identify the missing file(s) from the orchestrator output
  2. Ask the user: "Stage N needs `<file>` — should I generate it?"
  3. If yes, read the relevant reference doc and author the file
  4. Warn the user: "I'll re-run `--validate --clean` to get a fresh run. This
     deletes build artifacts (sim, synth, py cache). OK?"
  5. On confirmation, run: `python scripts/socks.py --project-dir . --validate --clean`
  6. Repeat until the pipeline completes or hits a real failure

  Run: `python scripts/socks.py --project-dir . --validate`
- **`--migrate`:** Read `references/migration-module.md` (module) or
  `references/migration-system.md` (system scope). Classify the project,
  follow the migration workflow, then validate with the SOCKS pipeline.
  Use `--scope` to specify the target layout. This is Claude-driven — no
  pipeline stages run automatically. After completing migration steps, always
  run `--validate --clean` to confirm the migrated project is sound:
  `python scripts/socks.py --project-dir . --validate --clean`

---

## Pipeline Overview

```
Stage  Scope          Name                                Type
-----  -----          ----                                ----
 0     all            Environment Setup                   AUTOMATED
                      dashboard -- check project.json on re-entry
 1     all            Architecture -> Plan Mode approval  BOTH
                      +-------------------------------------------------------+
                      |  DESIGN LOOP (2-9,21) -- see references/design-loop.md|
                      |  references/regmap.md -- after any register change    |
                      +-------------------------------------------------------+
 2-9   module         Design Loop (RTL, TB, sim, verify)  BOTH
21     module         IP Packaging (ipx:: -> component.xml) AUTOMATED
                      +-------------------------------------------------------+
                      |  SYSTEM DESIGN LOOP (20)                               |
                      |  see references/design-loop-system.md                  |
                      +-------------------------------------------------------+
 20    system         System Design Loop (TCL, XDC, arch) GUIDANCE
10     all            Vivado Synthesis                     AUTOMATED
                      references/constraints.md -- generate XDC before first synthesis
                      references/timing.md -- diagnose and fix if VIOLATED
11     all            Bash Audit                           AUTOMATED
12     all            CLAUDE.md Documentation              GUIDANCE
13     all            SOCKS Self-Audit                     AUTOMATED
                      +-------------------------------------------------------+
                      |  HIL FLOW (14-19) -- see references/hil.md           |
                      |  requires hil.json in project root                    |
                      +-------------------------------------------------------+
14     all            HIL: Vivado Project                  AUTOMATED
15     all            HIL: Implementation                  AUTOMATED
16     all            HIL: Firmware Build                  BOTH
17     all            HIL: Program + Test (user gate)      AUTOMATED
18     module         HIL: ILA Capture (VCD required)      AUTOMATED
18     system         HIL: ILA Capture (capture-only)      AUTOMATED
19     module         HIL: ILA Verify (VCD required)       AUTOMATED
19     system         (skipped -- no VCD baseline)
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
| 1 | Architecture | `scripts/architecture.py` (module) or `scripts/architecture-system.py` (system) + guidance | `references/architecture-diagrams.md` |
| 2-9 | **Design Loop** (module) | *See `references/design-loop.md`* | *`references/regmap.md` after register changes* |
| 21 | IP Packaging | `scripts/ip_package.py` | `references/ip-packaging.md` |
| 20 | **System Design Loop** (system) | *Claude authors TCL/XDC/ARCHITECTURE.md* | `references/design-loop-system.md` |
| 10a | **XDC Constraints** | Read `references/constraints.md`, generate XDC | *Before first synthesis or when missing XDC* |
| 10b | Vivado Synthesis | `scripts/synth.py` (module) or `scripts/synth-system.py` (system) | `references/synthesis.md` |
| 10c | **Timing Closure** | Read `references/timing.md`, diagnose + fix | *Only if Stage 10b shows VIOLATED* |
| 11 | Bash Audit | `scripts/bash_audit.py` | -- |
| 12 | CLAUDE.md | *Claude writes docs* | `references/structure-module.md` or `references/structure-system.md`, `references/claude_notes.md` |
| 13 | SOCKS Self-Audit | `scripts/self_audit.py` | -- |
| 14-19 | **HIL Flow** | *See `references/hil.md`* | *Requires `hil.json` in project root* |
| 14 | HIL: Vivado Project + trigger validation | `scripts/hil/hil_project.py` | `references/hil.md` |
| 15 | HIL: Implementation | `scripts/hil/hil_impl.py` | `references/hil.md` |
| 16 | HIL: Firmware Build | `scripts/hil/hil_firmware.py` + guidance | `references/hil.md` (§ Firmware Authoring Guide) |
| 17 | HIL: Program + Test | `scripts/hil/hil_run.py` | `references/hil.md` |
| 18 | HIL: ILA Capture | `scripts/hil/hil_ila.py` | `references/hil.md` (VCD required) |
| 19 | HIL: ILA Verify | `scripts/hil/hil_verify.py` | `references/hil.md` (VCD required) |

**For DPLL/PLL/NCO/clock recovery designs:** read `references/dpll.md` before Stage 1.

**VCD-based trigger plan generation:** If `ila_trigger_plan.json` doesn't exist
or needs regeneration, run `scripts/hil/gen_trigger_plan.py` to auto-generate
from VCD data (see `references/hil.md` § "Auto-Generating Trigger Plans from VCD").

---

## Orchestrator

**Workflow entry points (required — always use these, never call stage scripts directly):**
```bash
python scripts/socks.py --project-dir . --design --scope system
python scripts/socks.py --project-dir . --test
python scripts/socks.py --project-dir . --architecture --scope module
python scripts/socks.py --project-dir . --bughunt
python scripts/socks.py --project-dir . --migrate   # Claude-driven, no automated stages
python scripts/socks.py --project-dir . --validate
```

**Explicit stage control (legacy):**
```bash
python scripts/socks.py --project-dir . --stages automated
python scripts/socks.py --project-dir . --stages 0,4,7
python scripts/socks.py --project-dir . --stages 4 --files src/*.vhd
python scripts/socks.py --project-dir . --stages 10 --top my_module --part xc7z020clg400-1
```

**Workflows map to stages:**
- `--design` (module) -- 0, 1, 3, 4, 21, 5, 7, 8, 9, 10, 11, 12, 13
- `--design --scope system` -- 0, 1, 20, 10, 11, 12, 13 (Stage 20 replaces 2-9)
- `--test` -- 4, 21, 5, 7, 8, 9 (sim only)
- `--architecture` -- 0, 1, 3, 4, 21, 5, 7, 8, 9, 10, 11, 12, 13 (full re-architecture)
- `--bughunt` -- 3, 4, 21, 5, 7, 8, 9, 10 (sim + synthesis)
- `--validate` (module) -- 0, 1, 3, 4, 21, 5, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19 (HIL stages skip if no hardware)
- `--validate --scope system` -- 0, 1, 20, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19
- `--migrate` -- Claude-driven (`references/migration-module.md` or `references/migration-system.md`), no automated stages

**Stage keywords:**
- `--stages automated` -- all stages with scripts: 0, 1, 3, 4, 21, 5, 7, 8, 9, 10, 11, 13, 14, 15, 16, 17, 18, 19
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
│   ├── ip/            # IP packaging artifacts (Stage 21)
│   ├── hil/           # HIL build outputs (Vivado project, firmware, ILA CSVs)
│   ├── state/         # project.json (pipeline state)
│   ├── logs/          # Legacy pipeline logs
│   └── artifacts/     # Claude scratch space
├── docs/              # Architecture diagrams + README
├── socks.json         # Project config (name, scope, board, sub-designs)
├── hil.json           # HIL config (optional, triggers stages 14-19)
├── CLAUDE.md          # Project guide (Stage 12 output)
└── .gitignore         # Vivado/Xsim artifact ignores
```

See `references/structure-module.md` for module conventions,
`references/structure-system.md` for system scope conventions.

---

## Stage Details (0-1, 10-13)

### Stage 0 -- Environment Preflight

Run `scripts/env.py` to verify everything the pipeline needs:

1. **Vivado** -- discovers `settings64.sh`, verifies all 5 EDA tools
   (`xvhdl`, `xvlog`, `xelab`, `xsim`, `vivado`), warns if version != 2023.2
2. **Python** -- version >= 3.8, all stdlib modules used by SOCKS scripts
3. **SOCKS skill** -- all scripts and reference files exist, SKILL.md valid
4. **Project structure** (with `--project-dir`) -- `src/` required for module
   (optional for system scope); `tb/`, `build/`, `sw/`, `docs/`, `CLAUDE.md`,
   `.gitignore` checked if present. Reads `socks.json` for scope and `board.part`.

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

**2. Resource analysis** (module scope) -- widest intermediates, integer
overflow risk, DSP48E1 count per multiply, critical path depth.

**System scope variant:** When scope is `system`, Stage 1 runs
`scripts/architecture-system.py` which validates DESIGN-INTENT.md for system
scope sections (IP config, pin assignment, memory map), checks board references,
and sets `dut.entity` in socks.json. System scope deliverables are authored by
Claude during Stage 20 (system design loop):
1. `build/synth/create_bd.tcl` -- Vivado block design TCL
2. `build/synth/build_bitstream.tcl` -- synthesis/impl/bitstream TCL
3. `constraints/*.xdc` -- pin assignment + I/O standard
4. `docs/ARCHITECTURE.md` -- Mermaid diagrams (data flow, clocking, rate summary)

### Stage 10 -- Vivado Synthesis

**Stage 10a -- XDC Constraints:** Before the first synthesis run, or if the
project has no `.xdc` file and `synth_timing.tcl` uses inline constraints,
read `references/constraints.md` and follow the procedure to generate proper
XDC timing constraints. This ensures async inputs have `set_false_path` to
sync1 registers (not just `get_ports`), clock definitions are correct, and
monitor ports are excluded.

**Stage 10b -- Synthesis:** Run `scripts/synth.py` (module scope) or
`scripts/synth-system.py` (system scope — runs user-authored TCL scripts
from Stage 20). All timing checks must show MET.

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
