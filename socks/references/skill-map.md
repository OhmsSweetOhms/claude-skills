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
│    discovery.md          (Stage -1: design intent)       │
│    design-loop.md ─┬── regmap.md (Stage 2)               │
│                    └── constraints.md, timing.md         │
│    architecture-diagrams.md (Stage 1)                    │
│    vhdl.md               (Stage 2)                       │
│    linter.md             (Stage 3)                       │
│    synthesis.md          (Stage 4)                       │
│    python-testbench.md   (Stage 5)                       │
│    baremetal.md          (Stage 6)                       │
│    xsim.md               (Stage 7)                       │
│    vcd-verify.md         (Stage 8)                       │
│    constraints.md        (Stage 10a)                     │
│    timing.md             (Stage 10c)                     │
│    regmap.md             (Stage 2, on register change)   │
│    dpll.md               (DPLL/PLL designs)              │
│    project-structure.md  (directory conventions)          │
│    project-migration.md  (legacy + flat→SOCKS migration) │
│    session.md            (state file & dashboard)        │
│    skill-map.md          (this file)                     │
└──────────────────────────────────────────────────────────┘
```

## Key observations

- **Self-contained** — all domain knowledge is in references/, no external
  skill invocations needed
- **design-loop.md** cross-references regmap.md, constraints.md, and timing.md
- **timing.md** cross-references constraints.md (for missing constraint fixes)
- **discovery.md** cross-references design-loop.md (scope creep detection)
- **session.md** documents the project.json schema that dashboard.py reads
- Former standalone skills (build, status, constraints, timing, regmap)
  are now folded into socks as references or replaced by workflow commands
