# SOCKS Migration

Migrate an existing FPGA/SoC project to SOCKS directory structure.

## When to Use

- User says "convert to SOCKS", "migrate to SOCKS", "restructure this project"
- User has an existing VHDL project with flat layout or non-standard directories
- User wants to bring an external project into the SOCKS standard

## Target Structure

```
project/
├── src/               # VHDL source (synthesisable RTL)
├── tb/                # Python TB, SV TB, DPI-C, audit scripts
├── sw/                # C/C++ drivers (if applicable)
├── build/
│   ├── sim/           # Simulation scripts + Xsim artifacts
│   ├── synth/         # Synthesis TCL + Vivado reports
│   ├── logs/          # Pipeline logs (auto-generated)
│   └── artifacts/     # Claude scratch space
├── docs/              # Architecture diagrams + README
├── CLAUDE.md          # Project guide
└── .gitignore         # SOCKS standard ignores
```

## Migration Procedure

### Phase 0: Fingerprint Scan

**Before touching anything**, invoke the `/fingerprint` skill on the project
directory. External projects may contain PII, secrets, API keys, hostnames,
or other sensitive material that should be scrubbed before committing to your
repo. Fix any findings before proceeding.

### Phase 1: Detect Current Layout

Before moving anything, inventory the project:

1. Run `git ls-files | sort` to see tracked files
2. Run `git status --short` to see untracked files
3. Identify where each file category currently lives:
   - VHDL sources (`.vhd`)
   - Testbenches (`.py`, `.sv`, `.c` DPI)
   - Synthesis TCL (`.tcl` with `synth_design`)
   - Simulation scripts (`.sh`, `.tcl` with `xsim`/`open_vcd`)
   - Vivado reports (`*_utilization.txt`, `*_timing*.txt`, `*_drc.txt`)
   - Documentation (`.md`, `.png`)
   - C drivers (`.c`, `.h` with register defines)
   - Build artifacts (`.pb`, `.wdb`, `.vcd`, `.log`, `.jou`, `xsim.dir/`)

### Phase 2: Create Directory Structure

```bash
mkdir -p src tb build/sim build/synth build/logs build/artifacts docs
# Also: mkdir -p sw  (if project has C drivers)
```

### Phase 3: Move Files

**Order matters.** Move tracked files with `git mv`, artifacts with `mv`.

#### 3a. VHDL sources → src/
```bash
git mv *.vhd src/                    # or from wherever they are
```

#### 3b. Testbenches → tb/
```bash
git mv *_tb.py *_tb.sv *_audit.py *_vcd_verify.py *_dpi.c tb/
git mv *signal_map*.json tb/         # if present
```

#### 3c. Simulation scripts → build/sim/
```bash
git mv run_*.sh build/sim/           # or sim/run_*.sh
git mv dump_signals.tcl build/sim/   # VCD signal selection
git mv _run_vcd.tcl build/sim/       # VCD runner (if present)
```

#### 3d. Synthesis TCL → build/synth/
```bash
git mv synth_check.tcl build/synth/
git mv synth_timing.tcl build/synth/
git mv synth.tcl build/synth/        # if present
```

#### 3e. Tracked reports → build/synth/
Some projects track synthesis reports. Move them:
```bash
git mv *_utilization.txt *_timing*.txt *_drc.txt build/synth/
git mv *.rpt clockInfo.txt build/synth/
```

#### 3f. C drivers → sw/
```bash
git mv *.c *.h sw/                   # if they are drivers, not DPI
```

#### 3g. Documentation → docs/
```bash
git mv ARCHITECTURE*.md ARCHITECTURE*.png README.md docs/
```

#### 3h. Move gitignored artifacts
```bash
# Simulation artifacts
mv sim/*.vcd sim/*.wdb sim/*.csv sim/*.log sim/*.pb sim/*.jou build/sim/
mv sim/xsim.dir sim/xsim_work sim/.Xil build/sim/

# Synthesis reports (gitignored copies)
mv build/*_utilization.txt build/*_timing*.txt build/*_drc.txt build/synth/
mv build/*.log build/*.jou build/synth/

# Pipeline logs
mv logs/* build/logs/
```

#### 3i. Clean up empty old directories
```bash
rmdir sim logs 2>/dev/null
```

### Phase 4: Update Path References

**This is where things break.** Scripts in `build/sim/` and `build/synth/`
are now 2 directory levels below the project root instead of 1.

#### TCL scripts (synth)

Old pattern (1 level deep):
```tcl
set script_dir [file dirname [file normalize [info script]]]
set proj_dir   [file dirname $script_dir]
```

New pattern (2 levels deep):
```tcl
set script_dir [file dirname [file normalize [info script]]]
set proj_dir   [file dirname [file dirname $script_dir]]
```

Or if using `[pwd]`:
```tcl
# Old: ../src/  → New: ../../src/
```

**Check every TCL file for:**
- `../src/` → `../../src/`
- `$proj_dir/src/` (verify proj_dir resolves to project root)
- Absolute paths in report output (replace with `$script_dir/` or `[pwd]/`)

#### Shell scripts (simulation)

Old pattern:
```bash
PROJ_DIR="$(cd "${SIM_DIR}/.." && pwd)"
```

New pattern:
```bash
PROJ_DIR="$(cd "${SIM_DIR}/../.." && pwd)"
```

**Check for:**
- `PROJ_DIR` definition
- Any references to `../tb/`, `../src/` → `../../tb/`, `../../src/`

#### Python scripts (testbench, audit, VCD verify)

Common patterns to find and fix:
```python
# Old
Path(__file__).parent.parent / "sim" / "file.vcd"
os.path.join(os.path.dirname(__file__), '..', 'sim', 'file.csv')

# New
Path(__file__).parent.parent / "build" / "sim" / "file.vcd"
os.path.join(os.path.dirname(__file__), '..', 'build', 'sim', 'file.csv')
```

**Search for these patterns:**
```bash
grep -rn 'sim/' tb/                  # Python TB paths
grep -rn '"sim"' tb/                 # os.path.join segments
grep -rn '../src/' build/            # TCL/shell relative paths
```

### Phase 5: Generate .gitignore

Use the SOCKS template:

```gitignore
# Build directory — logs and artifacts always gitignored
build/logs/
build/artifacts/

# Xilinx Vivado / Xsim build artifacts
xsim.dir/
xsim_work/
.Xil/
webtalk/
*.pb
*.wdb
*.vcd
*.jou
*.log
*.backup.jou
*.backup.log

# Vivado synthesis reports (regenerated; contain hostname/username)
*_utilization.txt
*_timing.txt
*_timing_constrained.txt
*_timing_paths.txt
*_drc.txt
*.rpt
clockInfo.txt

# Simulation intermediates
build/sim/_run.tcl
build/sim/*.csv

# Python artifacts
__pycache__/
*.pyc

# Testbench generated plots
tb/*.png

# Claude local settings
.claude/
```

### Phase 6: Generate Missing Docs

If `docs/` is empty or missing key files:

1. **ARCHITECTURE.md** — Create with two Mermaid diagrams (Data Flow + Clocking)
   plus Rate Summary table. See SOCKS `references/architecture-diagrams.md`.

2. **Render PNGs** — `mmdc -i ARCHITECTURE.md -o arch.png -w 1600 -b white`
   then split into `ARCHITECTURE_dataflow.png` and `ARCHITECTURE_clocking.png`.

3. **README.md** — Feature spec, entity interface, build & test, synthesis results.

### Phase 7: Validate

```bash
# Python TB still passes?
python3 tb/*_tb.py

# Git status looks right? Tracked files in correct locations?
git ls-files | sort
git status --short

# No stale references?
grep -rn 'sim/' tb/ build/ CLAUDE.md docs/  # should only show build/sim/
```

### Phase 8: Update CLAUDE.md

Update all path references in CLAUDE.md:
- Files table: `sim/` → `build/sim/`, `build/` → `build/synth/`
- Build commands: `cd sim` → `cd build/sim`, `cd build` → `cd build/synth`
- Report paths: `sim/*.txt` → `build/synth/*.txt`
- Convention line: describe the SOCKS layout

---

## Common Pitfalls

### 1. TCL `../src/` depth
The #1 breakage. Every TCL script that uses relative paths to `src/` needs
updating when moving from 1-deep to 2-deep.

### 2. Python TB csv_path
Python TBs that write CSV files (for stage 8 cross-check) to `sim/` need
the path updated to `build/sim/`.

### 3. Absolute paths in TCL
Some TCL scripts use absolute paths for source files or report output.
Replace with `$script_dir` or `$proj_dir` relative paths.

### 4. Duplicate synth TCLs
Some projects have copies of synth TCLs in both `sim/` and `build/`.
After migration, decide which copy is canonical. The `build/synth/` copy
is the canonical one; `build/sim/` may have a copy for convenience.

### 5. VCD verify script
The VCD verify script in `tb/` looks for VCD in `sim/`. Must update to
`build/sim/`.

### 6. .gitignore pattern coverage
The wildcard patterns (`*.log`, `*_timing.txt`, etc.) catch files in any
directory, including `build/sim/` and `build/synth/`. But `build/logs/`
and `build/artifacts/` need explicit full-directory ignores since they
may contain files that don't match any pattern.

### 7. git mv vs mv
Only use `git mv` for tracked files. Gitignored artifacts should use plain
`mv`. Using `git mv` on gitignored files creates unnecessary staging noise.
