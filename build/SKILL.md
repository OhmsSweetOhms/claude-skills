---
name: build
description: "Build or rebuild an FPGA/SoC project. Use when the user says build, rebuild, recompile, make, re-run the pipeline, run all stages, or clean build. Routes to the SOCKS build.py script instead of running stages manually. Supports full rebuild, sim-only, synth-only, and no-clean modes."
---

# Build

Run the SOCKS build pipeline via `scripts/build.py`. **Do not attempt to
run pipeline stages manually or generatively when the user asks to build.**

## When to Use

Trigger on any of these user requests:
- "build", "rebuild", "recompile", "make"
- "re-run the pipeline", "run all stages"
- "clean build", "full rebuild"
- "just run synthesis", "synth only"
- "run the sim", "sim only" (when they mean the full sim pipeline, not a single test)

## How to Build

Determine the project parameters from CLAUDE.md or the project directory:
- `--top`: top-level entity name (e.g., `sdlc_axi`)
- `--project-dir`: project root (default: current working directory)
- `--part`: FPGA part (default: `xc7z020clg484-1`)

Then run the appropriate variant:

### Full rebuild (default)
```bash
python3 scripts/build.py --project-dir . --top <entity>
```

### Rebuild without clean (preserve sim outputs, rebuild pipeline)
```bash
python3 scripts/build.py --project-dir . --top <entity> --no-clean
```

### Sim only (skip Vivado synthesis)
```bash
python3 scripts/build.py --project-dir . --top <entity> --skip-synth
```

### Synth only (skip sim pipeline)
```bash
python3 scripts/build.py --project-dir . --top <entity> --synth-only
```

## Parameter Discovery

If `--top` is not obvious, find it by:
1. Reading CLAUDE.md for the top entity name
2. Looking for the entity with AXI ports in `src/*.vhd`
3. Asking the user

If the project has async ports that need false paths (for the synthesis
stages), also check CLAUDE.md for the `--async-ports` list. These are
passed through `build.py` to `socks.py` to `synth.py`.

## Important

- **Always use `scripts/build.py`** -- never manually invoke individual
  stage scripts when the user asks to "build" or "rebuild".
- The build script handles ordering, clean, and stage dependencies.
- If `build.py` is not found at the expected path, check for it relative
  to the socks skill scripts directory.
- Report the exit code to the user: 0 = success, non-zero = failure with
  the stage that failed.
