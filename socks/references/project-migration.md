# SOCKS Project Migration

Migrate existing projects to the current SOCKS directory structure. Two paths:

| Source | Description |
|--------|-------------|
| **Legacy SOCKS** | Old `/socks` project — has `src/`, `tb/`, `build/` but needs updates |
| **Flat / 3rd-party** | Non-SOCKS project — flat layout, vendor structure, or GitHub import |

This is a *directory structure* migration. For migrating pipeline state from
`session.json` to `project.json`, use `--migrate` (see `references/session.md`).

---

## Target Structure

```
project/
├── src/               # VHDL source (synthesisable RTL)
├── tb/                # Python TB, SV TB, DPI-C, audit scripts
├── sw/                # C/C++ drivers (if applicable)
├── build/
│   ├── sim/           # Simulation scripts + Xsim artifacts
│   ├── synth/         # Synthesis TCL + Vivado reports
│   ├── py/            # Python outputs (plots, generated data)
│   ├── state/         # project.json (pipeline state)
│   ├── logs/          # Legacy pipeline logs
│   └── artifacts/     # Claude scratch space
├── docs/              # Architecture diagrams + README
├── CLAUDE.md          # Project guide
└── .gitignore         # SOCKS standard ignores
```

---

## Workflow (Both Paths)

### Step 1: Classify

Determine which path applies:

```bash
# Check for SOCKS markers
ls src/ tb/ build/ 2>/dev/null          # existing SOCKS structure?
ls build/state/project.json 2>/dev/null # current state file?
ls build/logs/session.json 2>/dev/null  # legacy state file?
cat .gitignore 2>/dev/null              # SOCKS-style ignores?
```

**Legacy SOCKS** if: `src/` and `tb/` exist but `build/state/` is missing, or
`session.json` exists instead of `project.json`, or CLAUDE.md references old
skill invocations (`/build`, `/status`, `/regmap`).

**Flat / 3rd-party** if: no `src/` directory, VHDL files at root or in
non-standard directories (`hdl/`, `rtl/`, `ip/`, `design/`).

### Step 2: Clean generated artifacts

Remove all generated files before moving anything. This prevents stale
artifacts from being committed in the wrong location.

```bash
python3 scripts/clean.py --project-dir . --all --dry-run   # preview first
python3 scripts/clean.py --project-dir . --all              # then clean
```

This removes Vivado/Xsim outputs, synthesis reports, VCDs, CSVs, logs,
journals, and Python bytecode. It preserves `build/state/project.json`,
hand-written TCL, source code, and docs.

### Step 3: Inventory

Scan the project tree:

```bash
git ls-files | sort          # tracked files
git status --short           # untracked/modified
ls -la *.vhd *.sv *.py *.sh *.tcl 2>/dev/null   # root-level files
```

For multiple projects, scan the parent directory:
```bash
find /path/to/parent -maxdepth 3 -name ".git" -type d | sort
```

### Step 4: Investigate

Launch one background Explore agent per project to assess migration needs.
Agents read files and report back -- they do NOT move files, run git, or
edit anything.

Each agent should:
1. List all tracked files and their current locations
2. Compare against SOCKS target structure
3. Identify which files need to move where
4. Read build scripts and find path references that will break
5. Check for absolute paths, hardcoded directory assumptions
6. Flag any files that are already in the right place
7. Report whether the project is already fully migrated (skip it)

### Step 5: Review and present plan

Collect agent results and present a consolidated migration plan:

**For each project, show:**
- Classification: Legacy SOCKS or Flat / 3rd-party
- Files to move (grouped: src, tb, build/sim, build/synth, docs)
- Path references that need updating (file:line, old -> new)
- Files already in place (no action needed)
- Projects that are already fully migrated (skip)

Get user approval before proceeding.

---

## Path A: Legacy SOCKS Projects

Projects that already have the basic SOCKS structure but need updates for
the current version. Common gaps:

### A1. Missing directories

```bash
mkdir -p build/state build/py build/artifacts sw docs
```

Older SOCKS versions may not have `build/state/`, `build/py/`,
`build/artifacts/`, or `sw/`.

### A2. State file migration

If `build/logs/session.json` exists but `build/state/project.json` does not,
create a version-2 state stub:

```python
import sys, os
sys.path.insert(0, os.path.expanduser("~/.claude/skills/socks/scripts"))
from socks_lib import migrate_project
migrate_project("/path/to/project")
```

This creates a minimal `project.json` in `build/state/` and preserves
the old `build/logs/` directory. See `references/session.md` for the
full schema.

### A3. Stale skill references

Old CLAUDE.md and build scripts may reference standalone skills that no longer
exist. Search and replace:

| Old reference | New equivalent |
|--------------|----------------|
| `/build` or `scripts/build.py` directly | `python3 scripts/socks.py --project-dir . --design` |
| `/status` | `python3 scripts/dashboard.py --project-dir .` or check `build/state/project.json` |
| `/regmap` | Read `references/regmap.md`, run regmap check manually |
| `/constraints` | Read `references/constraints.md` |
| `/timing` | Read `references/timing.md` |
| `session.json` | `project.json` (in `build/state/`) |
| `build/logs/` for current state | `build/state/project.json` |

```bash
# Find stale references in project files
grep -rn '/build\b\|/status\b\|/regmap\b\|/constraints\b\|/timing\b' \
  CLAUDE.md docs/ tb/ build/sim/ sw/ 2>/dev/null
grep -rn 'session\.json\|build/logs' \
  CLAUDE.md docs/ tb/ build/sim/ sw/ 2>/dev/null
```

### A4. Update .gitignore

Old projects may have verbose gitignore rules listing individual artifact
extensions. Replace with the current template:

```gitignore
# Build outputs (tracked scripts survive — gitignore only affects untracked)
build/

# Claude local settings
.claude/
```

### A5. Update CLAUDE.md

- Replace skill invocation references with workflow commands
- Add `build/state/` to directory tree
- Mark `build/logs/` as legacy
- Update build/test commands to use `socks.py --project-dir .` workflows

### A6. Validate

```bash
python3 scripts/socks.py --project-dir . --test
```

Sim-only validation is usually sufficient for legacy SOCKS projects since the
core structure is already correct.

---

## Path B: Flat / 3rd-Party Projects

Projects with no SOCKS structure. Covers flat layouts (everything at root),
vendor-style layouts, and GitHub imports.

### Common source layouts

| Layout | What to expect |
|--------|---------------|
| **Flat** | `*.vhd`, `*_tb.sv`, `run_sim.sh` all at project root |
| **Vendor** | `hdl/`, `rtl/`, `sim/`, `testbench/`, `constraints/` |
| **IP-centric** | `ip/module_v1_0/`, Vivado IP packager structure |
| **Academic** | Mixed VHDL/Verilog, ISIM scripts, ISE project files |

### B1. Create directories

```bash
mkdir -p src tb build/sim build/synth build/state build/py build/logs build/artifacts sw docs
```

### B2. Move tracked files with `git mv`

Adapt these patterns to the source layout:

```bash
# Flat layout
git mv *.vhd src/
git mv *_tb.py *_tb.sv *_audit.py *_vcd_verify.py *_csv_verify.py tb/
git mv run_*.sh build/sim/
git mv synth_check.tcl synth_timing.tcl build/synth/
git mv README.md ARCHITECTURE*.md ARCHITECTURE*.png docs/

# Vendor layout (adjust source dirs)
git mv hdl/*.vhd src/
git mv sim/*_tb.sv sim/*_tb.py tb/
git mv sim/run_*.sh build/sim/
git mv constraints/*.tcl build/synth/
```

For 3rd-party projects, you may need to rename files to match SOCKS
conventions (e.g. `testbench.sv` → `module_tb.sv`).

### B3. Move gitignored artifacts with `mv`

```bash
mv *.vcd *.wdb *.log *.pb *.jou *.csv xsim.dir/ build/sim/ 2>/dev/null
mv xsim_*.backup.* build/sim/ 2>/dev/null
```

### B4. Update path references in hand-written scripts

**Shell scripts** (build/sim/run_*.sh):
```bash
# Add at top, after set -e:
SIM_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJ_DIR="$(cd "${SIM_DIR}/../.." && pwd)"
SRC_DIR="${PROJ_DIR}/src"
TB_DIR="${PROJ_DIR}/tb"
cd "${SIM_DIR}"

# Update compile commands:
xvhdl "${SRC_DIR}/file.vhd"
xvlog -sv "${TB_DIR}/file_tb.sv"
```

**TCL scripts** (hand-written only, skip pipeline-generated):
```tcl
# 2-level depth to project root:
set script_dir [file dirname [file normalize [info script]]]
set proj_dir   [file dirname [file dirname $script_dir]]
```

**Python scripts** (tb/*.py):
```python
# Source references:
# Old: Path(__file__).parent / "file.vhd"
# New: Path(__file__).parent.parent / "src" / "file.vhd"

# Sim data references:
# Old: os.path.join(os.path.dirname(__file__), "sim", "file.csv")
# New: os.path.join(os.path.dirname(__file__), "..", "build", "sim", "file.csv")

# Python outputs (plots, generated data) go to build/py/:
_PY_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'build', 'py')
os.makedirs(_PY_DIR, exist_ok=True)
plt.savefig(os.path.join(_PY_DIR, 'waveform.png'))
```

### B5. Update .gitignore

```gitignore
# Build outputs (tracked scripts survive — gitignore only affects untracked)
build/

# Claude local settings
.claude/
```

Add project-specific lines only if needed (e.g. `*.pdf`, `*.zip`).

### B6. Update CLAUDE.md

- Add SOCKS layout tree diagram
- Update all file paths in deliverables table
- Update build commands (`run_*.sh` -> `build/sim/run_*.sh`)
- Update compile order section with new paths

### B7. Commit

```bash
git add -A
git commit -m "Migrate to SOCKS directory layout: src/, tb/, build/, docs/"
```

### B8. Validate with SOCKS pipeline

Run the pipeline to verify the migration didn't break anything:

```bash
python3 scripts/socks.py --project-dir /path/to/project --design --scope module
```

Or for a quick sim-only check:
```bash
python3 scripts/socks.py --project-dir /path/to/project --test
```

**Common post-migration failures:**
- Stage 4 (audit): path not found -> fix `VHD_PATH` in audit script
- Stage 5 (python rerun): import error -> fix relative imports in TB
- Stage 7 (xsim): compile error -> fix paths in `run_*.sh`
- Stage 8 (VCD verify): file not found -> fix VCD path in verify script

Fix, commit, and re-run until all stages pass.

---

## Common Pitfalls

1. **TCL `../src/` depth** -- The #1 breakage. Scripts moving from 1-deep to
   2-deep need `../../src/`.
2. **Python TB paths to sim output** -- TBs that read CSV/VCD from `sim/`
   need updating to `build/sim/`.
3. **Absolute paths in TCL** -- Replace with `$script_dir` or `$proj_dir`
   relative paths.
4. **Generated files are not worth fixing** -- Pipeline-generated TCL and
   Vivado reports are regenerated on next build.
5. **git mv vs mv** -- Only `git mv` for tracked files. Gitignored artifacts
   use plain `mv`.
6. **Already-migrated projects** -- Skip entirely. Do not re-migrate.
7. **Partial migrations** -- Identify exactly what's done vs remaining.
8. **3rd-party naming** -- Rename files to SOCKS conventions during migration,
   not after. Avoids double-rename git history noise.
