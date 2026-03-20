# System Scope Migration

Migrate existing Vivado block design projects to the SOCKS system scope
directory structure. Two paths:

| Source | Description |
|--------|-------------|
| **Raw Vivado project** | `.xpr` project with block design, no SOCKS structure |
| **Flat TCL/XDC** | Standalone TCL scripts + XDC at root or in non-standard dirs |

---

## Target Structure

```
project/
├── build/
│   ├── synth/            # TCL scripts + Vivado reports
│   │   ├── create_bd.tcl
│   │   ├── build_bitstream.tcl
│   │   ├── *_preset.tcl
│   │   ├── utilization.rpt
│   │   ├── timing.rpt
│   │   └── system_wrapper.xsa
│   ├── vivado_project/   # Vivado project (generated, gitignored)
│   ├── state/
│   ├── logs/
│   └── artifacts/
├── constraints/          # XDC files
├── sw/                   # C drivers (if applicable)
├── docs/                 # DESIGN-INTENT.md, ARCHITECTURE.md
├── socks.json
├── CLAUDE.md
└── .gitignore
```

See `references/structure-system.md` for full conventions.

---

## Workflow (Both Paths)

### Step 1: Classify

Determine which path applies:

```bash
# Check for Vivado project
ls *.xpr **/*.xpr 2>/dev/null
# Check for block design TCL
ls *.tcl **/*.tcl 2>/dev/null
# Check for existing SOCKS structure
cat socks.json 2>/dev/null
ls build/synth/ 2>/dev/null
```

**Raw Vivado project** if: `.xpr` file exists, block design is in the GUI,
no exported TCL scripts for the block design creation.

**Flat TCL/XDC** if: TCL scripts exist at root or in non-standard directories,
possibly with XDC files alongside them.

### Step 2: Inventory

Identify what exists and where:

```bash
# TCL scripts
find . -name "*.tcl" -not -path "./.Xil/*" | sort
# XDC constraints
find . -name "*.xdc" | sort
# C source
find . -name "*.c" -o -name "*.h" | sort
# Vivado project
find . -name "*.xpr" | sort
# Block design
find . -name "*.bd" | sort
# Reports
find . -name "*utilization*" -o -name "*timing*" | sort
```

### Step 3: Review and present plan

Present the migration plan to the user before making changes:

- Which files move where
- Which TCL scripts need to be written (Path A) or moved (Path B)
- Whether a PS7 preset needs to be extracted
- Whether XDC files need reorganizing

Get user approval before proceeding.

---

## Path A: Raw Vivado Project

The block design exists only in the Vivado GUI. Need to export it to
reproducible TCL scripts.

### A1. Extract block design TCL

Open the project in Vivado and export the block design:

```tcl
# In Vivado TCL console:
open_project /path/to/project.xpr
open_bd_design [get_files *.bd]
write_bd_tcl -force /path/to/exported_bd.tcl
```

This produces a monolithic TCL script. It needs to be refactored into
the two-script pattern SOCKS expects.

### A2. Create directory structure

```bash
mkdir -p build/synth build/vivado_project constraints sw docs
```

### A3. Split into create_bd.tcl and build_bitstream.tcl

**`build/synth/create_bd.tcl`** should contain:
- `create_project` with the correct part
- All `create_bd_cell`, `set_property`, `connect_bd_net` calls
- `validate_bd_design`
- `make_wrapper`
- `save_bd_design`

**`build/synth/build_bitstream.tcl`** should contain:
- `open_project`
- `launch_runs synth_1`
- `launch_runs impl_1 -to_step write_bitstream`
- Report generation (`report_utilization`, `report_timing_summary`)
- XSA export (`write_hw_platform`)

See the microzed-spi project for a working example of both scripts.

### A4. Extract PS7/PS8 preset

If the block design uses a Zynq PS with board-specific configuration,
extract the preset into a standalone TCL file:

```tcl
# From the exported BD TCL, find the PS7 configuration section
# Copy all set_property calls on the PS7 cell into:
build/synth/<board>_ps7_preset.tcl
```

The `create_bd.tcl` should `source` this preset file.

### A5. Move XDC constraints

```bash
git mv *.xdc constraints/ 2>/dev/null
# Or extract from Vivado project:
cp project.srcs/constrs_1/imports/*.xdc constraints/
git add constraints/
```

### A6. Move C drivers

```bash
mkdir -p sw
git mv *.c *.h sw/ 2>/dev/null
# Or copy from SDK workspace if applicable
```

### A7. Create socks.json

```json
{
    "name": "project-name",
    "scope": "system",
    "board": {
        "preset": "board-name",
        "part": "xc7z020clg400-1"
    },
    "dut": {
        "entity": "system_wrapper"
    }
}
```

### A8. Create .gitignore

```gitignore
build/vivado_project/
build/synth/*.log
build/synth/*.jou
build/synth/*.rpt
build/synth/*.xsa
build/state/
build/logs/
build/artifacts/
*.Xil/
vivado*.log
vivado*.jou
vivado*.backup.*
.claude/
```

### A9. Create docs

- `docs/DESIGN-INTENT.md` -- Describe what the system does, IP configuration,
  pin assignments, memory map
- `docs/ARCHITECTURE.md` -- Mermaid data flow and clocking diagrams

### A10. Validate

```bash
python3 scripts/socks.py --project-dir . --design --scope system
```

The orchestrator should pass Stage 1 (architecture validation), Stage 20
(check TCL/XDC/docs exist), and Stage 10 (parse reports or run Vivado).

---

## Path B: Flat TCL/XDC

TCL scripts and XDC files exist but are not in SOCKS layout.

### B1. Create directories

```bash
mkdir -p build/synth build/vivado_project constraints sw docs
```

### B2. Move TCL scripts

```bash
# Adapt to actual filenames
git mv create_bd.tcl build/synth/
git mv build_bitstream.tcl build/synth/
git mv *_preset.tcl build/synth/
```

### B3. Update path references in TCL

TCL scripts moving from root to `build/synth/` (2 levels deep) need path
updates:

```tcl
# Old (at project root):
source microzed_ps7_preset.tcl
add_files -fileset constrs_1 spi_system.xdc

# New (in build/synth/):
set script_dir [file dirname [file normalize [info script]]]
set proj_dir   [file dirname [file dirname $script_dir]]
source [file join $script_dir microzed_ps7_preset.tcl]
add_files -fileset constrs_1 [file join $proj_dir constraints spi_system.xdc]
```

**Common path patterns to fix:**
- `source` of preset files -- now relative to `$script_dir`
- XDC file paths -- now under `$proj_dir/constraints/`
- Project directory -- use `$proj_dir/build/vivado_project/`
- Report output paths -- use `$script_dir/` for reports in `build/synth/`

### B4. Move XDC constraints

```bash
git mv *.xdc constraints/
```

### B5. Move C drivers

```bash
git mv *.c *.h sw/ 2>/dev/null
```

### B6. Create socks.json, .gitignore, docs

Same as Path A steps A7-A9.

### B7. Commit

```bash
git add -A
git commit -m "Migrate to SOCKS system scope layout"
```

### B8. Validate

```bash
python3 scripts/socks.py --project-dir . --design --scope system
```

---

## Common Pitfalls

1. **TCL path depth** -- The #1 breakage. Scripts moving from root to
   `build/synth/` need `[file dirname [file dirname $script_dir]]` to find
   the project root.
2. **PS7 preset source path** -- `create_bd.tcl` sources the preset file.
   After moving both to `build/synth/`, the `source` path changes.
3. **XDC paths in create_bd.tcl** -- Constraint files referenced via
   `add_files -fileset constrs_1` need updating to `$proj_dir/constraints/`.
4. **Vivado project location** -- `create_project` path should point to
   `$proj_dir/build/vivado_project/system`.
5. **Report output paths** -- `report_utilization -file` should write to
   `$script_dir/utilization.rpt` (inside `build/synth/`).
6. **Generated files are not worth fixing** -- Vivado project, IP outputs,
   and synthesis runs are fully regenerated from TCL. Only migrate the
   scripts themselves.
7. **Block design export completeness** -- `write_bd_tcl` may miss custom
   IP repo paths or board part settings. Verify `create_bd.tcl` reproduces
   the design from scratch.
