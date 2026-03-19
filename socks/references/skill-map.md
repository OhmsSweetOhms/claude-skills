# Skill Dependency Map

How the SOCKS skill is wired together. All domain knowledge now lives
under `socks/references/` — no external skill dependencies.

```
┌──────────────────────────────────────────────────────────┐
│                     /socks  (orchestrator)                │
│                                                          │
│  SKILL.md ──── workflows: --design, --test,              │
│                --architecture, --bughunt, --migrate       │
│                                                          │
│  scripts/                                                │
│    socks.py ──── state_manager.py (project.json)         │
│    build.py, env.py, synth.py, xsim.py, ...             │
│    dashboard.py (live SSE, reads project.json)           │
│                                                          │
│  references/                                             │
│    discovery.md          (Stage -1: module/block intent)  │
│    discovery-system.md   (Stage -1: system scope intent)  │
│    design-loop.md ─┬── regmap.md (Stage 2)               │
│                    └── constraints.md, timing.md         │
│    design-loop-system.md (Stage 20: system design loop)  │
│    architecture-diagrams.md (Stage 1)                    │
│    linter.md             (Stage 3)                       │
│    synthesis.md          (Stage 4)                       │
│    python-testbench.md   (Stage 5)                       │
│    baremetal.md          (Stage 6)                       │
│    xsim.md               (Stage 7)                       │
│    vcd-verify.md         (Stage 8)                       │
│    constraints.md        (Stage 10a)                     │
│    timing.md             (Stage 10c)                     │
│    regmap.md             (Stage 2, on register change)   │
│    hil.md ────────── (Stages 14-19: HIL flow)            │
│    test-discovery-system.md (system scope test discovery) │
│    dpll.md               (DPLL/PLL designs)              │
│    project-structure.md  (directory conventions)          │
│    project-migration.md  (legacy + flat→SOCKS migration) │
│    session.md            (state file & dashboard)        │
│    skill-map.md          (this file)                     │
│    boards/               (board reference assets)         │
│      microzed/           (MicroZed 7020 preset + XDC)    │
│                                                          │
│  scripts/hil/                                            │
│    hil_lib.py ── shared HIL utilities                    │
│    hil_project.py (14), hil_impl.py (15),               │
│    hil_firmware.py (16), hil_run.py (17),               │
│    hil_ila.py (18), hil_verify.py (19)                  │
│    tcl/ ── gen_hil_top, run_impl, ila_capture, flash,   │
│            boot_cpu, templates (create_project,          │
│            block_design, build_app)                      │
│    presets/ ── microzed_ps7_preset.tcl (legacy)           │
│    xdc/ ── insert_debug.xdc, microzed.xdc               │
└──────────────────────────────────────────────────────────┘
```

## Key observations

- **Self-contained** — all domain knowledge is in references/, no external
  skill invocations needed
- **design-loop.md** cross-references regmap.md, constraints.md, and timing.md
- **timing.md** cross-references constraints.md (for missing constraint fixes)
- **discovery.md** cross-references design-loop.md (scope creep detection)
- **hil.md** is the HIL equivalent of design-loop.md — covers stages 14-19,
  hil.json schema, board presets, ILA trigger plan authoring
- **session.md** documents the project.json schema that dashboard.py reads
- Former standalone skills (build, status, constraints, timing, regmap)
  are now folded into socks as references or replaced by workflow commands
