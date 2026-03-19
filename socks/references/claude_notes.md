# CLAUDE.md Content Guide

Read this file during Stage 12 (CLAUDE.md Documentation) and after Stage 19
(post-HIL update). This template defines the comprehensive content that
CLAUDE.md must contain so future Claude sessions can resume without re-reading
all source files.

---

## Required Sections

### 1. Entity Overview

- Entity name, version, purpose (one paragraph)
- Target device and tool version
- Design scope: module / block / system
- Key specifications (baud rates, frequencies, bit widths, etc.)

### 2. Architecture

- **Data path:** Input → processing stages → output (signal flow)
- **Control path:** FSM states, transitions, enable/strobe signals
- **FSM states:** Full enumeration with encoding values (from VHDL `type` or TEST-INTENT.md)
- **Register map:** For AXI-Lite peripherals: offset, name, access, bit fields
- **Clock domains:** sys_clk frequency, any derived clocks/enables, CDC crossings
- **Key timing:** Pipeline latency, throughput, critical path notes

### 3. File Inventory

For every file in the project, document:

| File | Purpose | Generated/Authored | Dependencies |
|------|---------|--------------------|--------------|
| `src/module.vhd` | Main RTL | Authored (Stage 2) | -- |
| `src/sub.vhd` | Sub-module | Authored (Stage 2) | module.vhd |
| `tb/module_tb.sv` | SV testbench | Authored (Stage 7) | src/*.vhd |
| `sw/module.c` | C driver | Authored (Stage 6) | sw/module.h |
| `build/hil/hil_top.vhd` | HIL wrapper | Generated (Stage 14) | hil.json |

### 4. Naming Conventions

- Port naming: `mon_` prefix for monitors, `s_axi_` for AXI, `irq` for interrupts
- Signal naming: internal signal conventions (e.g. `_r` for registered, `_v` for variable)
- FSM state naming: `ST_` prefix convention
- File naming: `entity_name.vhd`, `entity_name_tb.sv`, `entity_name.c`

### 5. Build & Test Commands

```bash
# Full pipeline
python scripts/socks.py --project-dir . --design --scope block

# Simulation only
python scripts/socks.py --project-dir . --test

# HIL
python scripts/socks.py --project-dir . --hil --top entity_name

# Single stage
python scripts/socks.py --project-dir . --stages 7
```

### 6. Test Overview

- Number of test cases, test pattern generation strategy
- Pass/fail criteria
- Expected simulation output (key lines)
- VCD signals of interest (from `vcd_signal_map.json`)
- CSV cross-check: which columns are compared, expected tolerance

### 7. Synthesis Results

- Resource usage (LUT, FF, BRAM, DSP48E1)
- Timing: WNS, WHS, WPWS
- Critical path description
- Any timing exceptions or constraints notes

### 8. Known Limitations

- Boundary conditions or untested scenarios
- Assumptions (e.g. "assumes single-clock domain", "baud rate must divide evenly")
- Design decisions and rationale for non-obvious choices

### 9. Decision Boundaries

Document non-obvious design decisions:
- Why a particular approach was chosen over alternatives
- Trade-offs made (area vs speed, complexity vs flexibility)
- What would need to change if requirements shifted

### 10. Tech Stack & State Handoff

- Vivado version, Python version, XSCT/Vitis version
- Board-specific notes (MicroZed pin mapping, serial port, JTAG)
- What the next developer/session needs to know to continue work

---

## Post-HIL Update

After Stage 19 completes, Claude updates CLAUDE.md with:

1. **HIL results:** Pass/fail, number of captures, any mismatches
2. **Board-specific notes:** Serial port used, any XSCT quirks encountered
3. **ILA observations:** Signal activity, FSM state sequences observed
4. **Debug notes:** If Stage 18 required debug firmware, document the trigger plan and capture results
