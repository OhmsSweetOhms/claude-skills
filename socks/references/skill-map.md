# Skill Dependency Map

How the SOCKS skill ecosystem is wired together.

```
                      ┌──────────────┐
                      │  Root CLAUDE │
                      │  (VHDL rules)│
                      └──────────────┘
                        (no links)

┌─────────────────────────────────────────────────────────┐
│                     /socks  (orchestrator)               │
│                                                          │
│  SKILL.md ──────┬── /status      (Stage 0+)             │
│                 ├── /regmap      (Stage 2)               │
│                 ├── /constraints (Stage 10a)             │
│                 ├── /timing      (Stage 10c)             │
│                 └── /build       (rebuild)               │
│                                                          │
│  references/                                             │
│    design-loop.md ─── /regmap  (×3)                      │
│    vhdl.md            (standalone)                        │
│    synthesis.md       (standalone)                        │
│    dpll.md            (standalone)                        │
│    linter.md          (standalone)                        │
│    python-testbench.md(standalone)                        │
│    xsim.md            (standalone)                        │
│    vcd-verify.md      (standalone)                        │
│    baremetal.md       (standalone)                        │
│    architecture-diagrams.md (standalone)                  │
│    project-structure.md     (standalone)                  │
└─────────────────────────────────────────────────────────┘
        │           │            │            │
        ▼           ▼            ▼            ▼
  ┌──────────┐ ┌─────────┐ ┌────────────┐ ┌───────┐
  │ /status  │ │ /regmap │ │/constraints│ │/timing│
  │          │ │         │ │            │ │       │
  │ (no refs)│ │(no refs)│ │ refs ──────┼─┤       │
  └──────────┘ └─────────┘ │  /timing   │ │refs ──┼── /socks Stage 10
                            └────────────┘ └───────┘

  ┌──────────┐  ┌──────────────────┐
  │ /build   │  │ /socks-migration │
  │          │  │                  │
  │ (no refs)│  │ refs /socks      │
  └──────────┘  │ (build.py)       │
                └──────────────────┘
```

## Key observations

- **/socks is the hub** — it references 5 other skills and 11 internal reference files
- **/constraints ↔ /timing** have a bidirectional relationship (constraints references timing, timing references socks which invokes constraints)
- **/status, /regmap, /build** are leaf nodes — referenced by socks but don't reference anything back
- The 11 reference files under `socks/references/` are all standalone (no cross-refs between them), except `design-loop.md` which references /regmap
- **Root CLAUDE.md** is completely isolated — no links to/from any skill
