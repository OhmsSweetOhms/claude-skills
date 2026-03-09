# SOCKS Migration

Migrate existing FPGA/SoC projects to SOCKS directory structure.

## When to Use

- User says "convert to SOCKS", "migrate to SOCKS", "restructure this project"
- User has existing VHDL projects with flat layout or non-standard directories
- User wants to bring one or more projects into the SOCKS standard

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
│   ├── logs/          # Pipeline logs (auto-generated)
│   └── artifacts/     # Claude scratch space
├── docs/              # Architecture diagrams + README
├── CLAUDE.md          # Project guide
└── .gitignore         # SOCKS standard ignores
```

## Workflow

### Step 1: Inventory (main conversation)

Scan the project tree to understand what needs migrating:

```bash
git ls-files | sort          # tracked files
git status --short           # untracked/modified
ls -la *.vhd *.sv *.py *.sh *.tcl 2>/dev/null   # root-level files
```

For multiple projects, scan the parent directory:
```bash
find /path/to/parent -maxdepth 3 -name ".git" -type d | sort
```

### Step 2: Investigate (Explore agents, read-only)

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

**Agent prompt template:**
```
Assess SOCKS migration needs for /path/to/project. Read-only -- do NOT
edit or move anything.

1. Run `git ls-files | sort` and `git status --short`
2. Classify each file: VHDL source, testbench, sim script, synth TCL,
   documentation, build artifact, C driver
3. For each file, state: current location → target SOCKS location
4. Read build/sim scripts and list every path reference that would break
   after the move (e.g. `xvhdl file.vhd` needs `xvhdl ../../src/file.vhd`)
5. Check Python scripts for hardcoded paths to sim/, build/, etc.
6. Report: files to move, paths to fix, files already correct, skip if done
```

### Step 3: Review and present plan (main conversation)

Collect agent results and present a consolidated migration plan:

**For each project, show:**
- Files to move (grouped: src, tb, build/sim, build/synth, docs)
- Path references that need updating (file:line, old → new)
- Files already in place (no action needed)
- Projects that are already fully migrated (skip)

Get user approval before proceeding.

### Step 4: Apply migrations (main conversation)

Do all file moves, path fixes, and git operations directly. This is
where Bash, Edit, and git permissions are needed.

#### 4a. Create directories
```bash
mkdir -p src tb build/sim build/synth build/logs build/artifacts docs
```

#### 4b. Move tracked files with `git mv`
```bash
git mv *.vhd src/
git mv *_tb.py *_tb.sv *_audit.py *_vcd_verify.py *_csv_verify.py tb/
git mv run_*.sh build/sim/
git mv synth_check.tcl synth_timing.tcl build/synth/
git mv README.md ARCHITECTURE*.md ARCHITECTURE*.png docs/
```

#### 4c. Move gitignored artifacts with `mv`
```bash
mv *.vcd *.wdb *.log *.pb *.jou *.csv xsim.dir/ build/sim/ 2>/dev/null
mv xsim_*.backup.* build/sim/ 2>/dev/null
```

#### 4d. Update path references in hand-written scripts

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

#### 4e. Update .gitignore

Use the SOCKS template:

```gitignore
# Build outputs (tracked scripts survive — gitignore only affects untracked)
build/

# Claude local settings
.claude/
```

Add project-specific lines only if needed (e.g. `*.pdf`, `*.zip`).

`PYTHONPYCACHEPREFIX` is set in build scripts and `socks.py` to redirect
`__pycache__/` into `build/py/`, so no separate `__pycache__` gitignore
line is needed.

All Vivado/Xsim artifacts, synthesis reports, generated TCL scripts,
CSV logs, and Python-generated plots land under `build/` so a single
`build/` rule covers everything. Tracked files (shell scripts,
hand-written TCL) are unaffected — gitignore only applies to
untracked files.

#### 4f. Update CLAUDE.md

- Add SOCKS layout tree diagram
- Update all file paths in deliverables table
- Update build commands (`run_*.sh` → `build/sim/run_*.sh`)
- Update compile order section with new paths

#### 4g. Commit

```bash
git add -A
git commit -m "Migrate to SOCKS directory layout: src/, tb/, build/, docs/"
```

### Step 5: Validate with SOCKS pipeline (main conversation)

After committing, run the SOCKS build pipeline to verify the migration
didn't break anything. This is the definitive test — if the pipeline
passes, the migration is correct.

#### 5a. Clean run

```bash
python3 scripts/build.py --project-dir /path/to/project --top <entity> --skip-synth
```

Use `--skip-synth` for a quick validation (Python TB + Xsim sim +
audits). Use the full pipeline (no `--skip-synth`) if you also want
to verify synthesis paths.

The `scripts/` path refers to the SOCKS skill scripts directory
(`~/.claude/skills/socks/scripts/`). The build script resolves paths
from `--project-dir`.

#### 5b. Check pipeline logs

The pipeline writes logs to `build/logs/`:
- `pipeline_<timestamp>.log` — per-stage transition log with reasons
- `pipeline_<timestamp>.chart` — visual run chart with pass/fail/skip

```bash
cat build/logs/pipeline_*.chart    # quick visual check
```

**What to look for:**
- All stages should show `* PASS` or `o SKIP` (acceptable)
- `X FAIL` on any stage means the migration broke something
- Common post-migration failures:
  - Stage 4 (audit): path not found → fix `VHD_PATH` in audit script
  - Stage 5 (python rerun): import error → fix relative imports in TB
  - Stage 6 (xsim): compile error → fix paths in `run_*.sh`
  - Stage 7 (VCD verify): file not found → fix VCD path in verify script

#### 5c. Fix and re-run

If any stage fails:
1. Read the log to identify the broken path reference
2. Fix it in the main conversation
3. Amend the migration commit
4. Re-run the pipeline to confirm all green

Only report the migration as complete when the pipeline chart shows
all stages passing.

## Common Pitfalls

### 1. TCL `../src/` depth
The #1 breakage. Scripts moving from 1-deep to 2-deep need `../../src/`.

### 2. Python TB paths to sim output
Python TBs that read CSV/VCD from `sim/` need updating to `build/sim/`.

### 3. Absolute paths in TCL
Replace with `$script_dir` or `$proj_dir` relative paths.

### 4. Generated files are not worth fixing
Pipeline-generated TCL scripts and Vivado reports are regenerated on
next build. Do not fix paths in these — focus on hand-written scripts.

### 5. git mv vs mv
Only `git mv` for tracked files. Gitignored artifacts use plain `mv`.

### 6. Already-migrated projects
Agents should identify these in Step 2. Skip them entirely — do not
re-migrate or touch files that are already in the right place.

### 7. Partial migrations
Some projects may be half-migrated (e.g. src/ exists but build/ is
flat). Agents should identify exactly what's done vs remaining.
