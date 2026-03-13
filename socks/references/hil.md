# SOCKS HIL Flow -- Stages 14-19

This file contains the agent logic for the Hardware-in-the-Loop (HIL) flow.
It is read by Claude when entering or re-entering the HIL pipeline. All
stage details, configuration, and troubleshooting live here.

> **Future extraction note:** This file is intentionally self-contained so it
> can become an agent system prompt without restructuring.

---

## What the HIL Flow Is

Stages 14-19 build a Vivado project around the DUT, synthesize and implement
it for a Zynq-7000 SoC, build bare-metal firmware, program the board via JTAG,
capture UART test output, and optionally capture ILA waveforms for comparison
against simulation VCD.

```
Simulation:  Stage 5 (Python TB) -> Stage 7 (SV/Xsim + VCD) -> Stage 8 (VCD verify)
                                                                      |
Hardware:    Stage 14 (project) -> 15 (impl) -> 16 (firmware) -> 17 (program+test)
                                                                      |
ILA verify:  Stage 18 (ILA capture, VCD-gated) -> Stage 19 (ILA vs VCD)
```

The HIL flow is standalone (`--hil`), not auto-appended to `--design` or
`--bughunt`. It requires hardware presence and explicit user intent.

---

## Prerequisites

### Tools
- **Vivado** (2023.2 recommended) -- synthesis, implementation, ILA
- **XSDB** (ships with Vivado) -- JTAG programming (flash.tcl, boot_cpu.tcl)
- **XSCT** (ships with Vitis SDK) -- firmware workspace creation (build_app.tcl)
- **pyserial** (`pip install pyserial`) -- UART capture (stages 17-18)

Stage 0 (`env.py`) checks all of these when `hil.json` exists in the project.

### Hardware
- Zynq-7000 board (e.g. MicroZed) connected via JTAG (Digilent or Xilinx cable)
- UART connection (typically USB-to-serial, e.g. FTDI or CP2102)
- Serial port permissions: user must be in `dialout` group on Linux

### Project
- `hil.json` in project root (required -- if missing, all HIL stages skip)
- `src/*.vhd` DUT sources referenced from `hil.json`
- Prior synthesis (Stage 10) completed, or `--hil` runs it automatically

---

## hil.json Schema

`hil.json` lives in the project root and drives all HIL stages.

```json
{
  "dut": {
    "entity": "my_peripheral",
    "sources": ["src/my_peripheral.vhd", "src/sub_module.vhd"]
  },
  "board": {
    "part": "xc7z020clg484-1",
    "preset": "microzed_ps7_preset.tcl",
    "serial_vid": "10c4",
    "serial_pid": "ea60",
    "serial_fallback": "/dev/ttyUSB1"
  },
  "axi": {
    "base_address": "0x43C00000",
    "range": "4K",
    "fclk_mhz": 100
  },
  "wiring": {
    "loopback": [["txd", "rxd"]],
    "monitor": {
      "prefixes": ["irq", "mon_"],
      "ports": ["irq", "mon_tx_state", "mon_rx_state"]
    }
  },
  "firmware": {
    "test_src": "sw/hil_test_main.c",
    "driver_sources": ["sw/my_peripheral.c", "sw/my_peripheral.h"],
    "pass_marker": "HIL_PASS",
    "fail_marker": "HIL_FAIL",
    "timeout_s": 30
  }
}
```

### Required keys

| Key | Type | Description |
|-----|------|-------------|
| `dut.entity` | string | Top-level VHDL entity name |
| `dut.sources` | list | Paths to VHDL source files (relative to project root) |
| `board.part` | string | Xilinx part number |
| `axi.base_address` | string | AXI-Lite base address (hex) |

### Optional keys

| Key | Default | Description |
|-----|---------|-------------|
| `board.preset` | `microzed_ps7_preset.tcl` | PS7 configuration preset |
| `board.serial_vid` | -- | USB vendor ID for serial port auto-detection |
| `board.serial_pid` | -- | USB product ID |
| `board.serial_fallback` | -- | Fallback serial port path |
| `axi.range` | `4K` | AXI address range |
| `axi.fclk_mhz` | `100` | PS FCLK frequency in MHz |
| `wiring.loopback` | `[]` | Port pairs to connect externally (e.g. TX->RX) |
| `wiring.monitor.prefixes` | `[]` | Port name prefixes for MARK_DEBUG |
| `wiring.monitor.ports` | `[]` | Explicit ports to externalize/monitor |
| `firmware.test_src` | -- | Main test C file |
| `firmware.driver_sources` | `[]` | C driver source files |
| `firmware.pass_marker` | `HIL_PASS` | UART string indicating test passed |
| `firmware.fail_marker` | `HIL_FAIL` | UART string indicating test failed |
| `firmware.timeout_s` | `30` | Seconds to wait for pass/fail marker |

---

## Board Presets

Board presets are TCL scripts that configure PS7 block design properties.
They live in `scripts/hil/presets/`.

**Included:** `microzed_ps7_preset.tcl` -- MicroZed 7020 with:
- DDR3 memory configuration
- MIO assignments for UART, GPIO
- Clock configuration (33.333 MHz input)

**Custom presets:** Export from Vivado: Block Design > PS7 > Presets > Export.
Place the TCL file in your project or reference by absolute path in
`board.preset`.

---

## Wiring Rules

### Loopback
External connections between DUT ports. Each entry is a `[output, input]` pair.
These ports are externalized from the block design and connected at the top
level. Use for self-test (e.g. UART TX -> RX loopback via board wiring or
jumper wire).

### Monitor
Ports to observe via ILA. Two mechanisms:
- **prefixes**: Port names matching these prefixes get `MARK_DEBUG` attributes
  in `gen_hil_top.tcl`. Zero RTL cost without ILA.
- **ports**: Explicit port names to externalize from the block design.

MARK_DEBUG is applied unconditionally in the VHDL architecture. The ILA is
only instantiated when `insert_debug.xdc` is added to the project (Stage 14
adds it automatically when VCD exists from simulation).

---

## Per-Stage Details

### Stage 14: HIL Vivado Project (`scripts/hil/hil_project.py`)

**What it does:**
1. Reads `hil.json`
2. Expands `block_design.template.tcl` (PS7 config, AXI interconnect, DUT module)
3. Expands `create_project.template.tcl` (project creation, source addition)
4. Runs Vivado batch: creates project, block design, runs `gen_hil_top.tcl`
5. If VCD exists from simulation, adds `insert_debug.xdc` for ILA support

**Outputs:** `build/hil/vivado_project/*.xpr`, `build/hil/hil_top.vhd`

**VCD-gated debug:** If `build/sim/*.vcd` exists (from Stage 7), `--debug` is
auto-enabled. This adds `insert_debug.xdc` to the project, which causes Vivado
to insert an ILA core during implementation. If no VCD, the ILA XDC is not
added and `MARK_DEBUG` attributes have zero hardware cost.

**Template parameters:** `{{DUT_ENTITY}}`, `{{PART}}`, `{{PRESET_TCL}}`,
`{{FCLK_MHZ}}`, `{{AXI_RANGE}}`, `{{AXI_BASE_ADDRESS}}`, `{{SOURCES_TCL}}`,
`{{EXTERNALIZE_TCL}}`, `{{GEN_HIL_TOP_TCL}}`, `{{BLOCK_DESIGN_TCL}}`,
`{{BASE_XDC}}`, `{{DEBUG_XDC}}`, `{{HIL_TOP_PATH}}`, `{{BUILD_DIR}}`,
`{{PROJECT_NAME}}`, `{{LOOPBACK_OUT}}`, `{{LOOPBACK_IN}}`,
`{{MONITOR_PREFIXES}}`.

### Stage 15: HIL Implementation (`scripts/hil/hil_impl.py`)

**What it does:**
1. Runs `run_impl.tcl` via Vivado batch: synthesis, implementation, bitstream,
   XSA export, ps7_init extraction
2. Verifies timing (VIOLATED = fail)

**Outputs:** `build/hil/system_wrapper.xsa`, `build/hil/ps7_init.tcl`,
bitstream in `vivado_project/*/impl_1/*.bit`, optionally `hil_top.ltx`

**Prerequisite:** Stage 14 must have created the `.xpr` project.

### Stage 16: HIL Firmware Build (`scripts/hil/hil_firmware.py`)

**What it does:**
1. Expands `build_app.template.tcl` from `hil.json` firmware config
2. Runs XSCT to create Vitis workspace, hardware platform, BSP, and app
3. Imports driver and test sources from `hil.json`
4. Builds firmware ELF

**Outputs:** `build/hil/vitis_ws/hil_app/Debug/hil_app.elf`

**Prerequisite:** Stage 15 must have generated `system_wrapper.xsa`.

**Driver deduplication:** Multiple driver source files in the same directory
are imported once (directories are deduplicated).

### Stage 17: HIL Program + Test (`scripts/hil/hil_run.py`)

**What it does:**
1. Pre-flight: verify bitstream, ELF, ps7_init, serial port, XSDB all present
2. User confirmation gate (unless `--auto-confirm`)
3. Start UART capture background thread
4. Program board via XSDB (`flash.tcl`)
5. Wait for pass/fail marker on UART

**Outputs:** Console log with UART output, PASS/FAIL result

**Flags:**
- `--auto-confirm` -- skip "Program board? [y/N]" prompt
- `--no-hw` -- skip entirely (exit 0, for build-only runs)
- `--serial /dev/ttyUSBx` -- override serial port auto-detection
- `--timeout N` -- override UART capture timeout (default: from hil.json or 30s)

**Pass/fail markers:** The firmware prints `HIL_PASS` or `HIL_FAIL` to UART.
These strings are configurable in `hil.json` (`firmware.pass_marker`,
`firmware.fail_marker`).

### Stage 18: HIL ILA Capture (`scripts/hil/hil_ila.py`)

**VCD-gated:** Only runs if `build/sim/*.vcd` exists from Stage 7. If no VCD,
this stage skips cleanly (exit 0).

**What it does:**
1. Launch Vivado in interactive mode (programs FPGA, discovers ILA)
2. Boot CPU via XSDB (`boot_cpu.tcl` -- no FPGA reprogram)
3. Open serial port
4. For each capture in `ila_trigger_plan.json`:
   - ARM ILA with trigger probe and value
   - Send "G" byte to serial (firmware go signal)
   - Wait for ILA trigger
   - Read UART line
5. Export ILA data to CSV

**Outputs:** `build/hil/ila_*.csv` (one per capture)

**Prerequisites:**
- `hil_top.ltx` (debug probes file from implementation)
- `ila_trigger_plan.json` (hand-written or Claude-assisted)
- Firmware ELF + ps7_init

### Stage 19: HIL ILA Verify (`scripts/hil/hil_verify.py`)

**VCD-gated:** Only runs if both ILA CSVs and simulation VCD exist.

**What it does:**
1. Parse VCD (first 50000 timestamps)
2. Parse each ILA CSV
3. For each signal in the ILA capture:
   - **Activity check:** signal has at least one toggle
   - **State sequence check:** FSM states in ILA appear in same order as VCD
     (subsequence match, not exact -- different time windows)

**Outputs:** Per-signal PASS/WARN/FAIL with details

**Philosophy:** Behavioural comparison, not sample-by-sample. The ILA and VCD
have different time bases and capture windows. What matters is that the same
state transitions occur in the same order.

---

## ILA Trigger Plan

`ila_trigger_plan.json` is required for Stage 18. Place it in `build/hil/`.

```json
{
  "captures": [
    {
      "name": "fsm_transition",
      "probe": "u_ila_0/probe0",
      "value": "8'bXXXXX010",
      "output": "ila_fsm_transition.csv",
      "description": "Capture FSM entering state 2 (use X for don't-care bits)"
    },
    {
      "name": "irq_assert",
      "probe": "u_ila_0/probe0",
      "value": "8'bXXXXXXX1",
      "output": "ila_irq_assert.csv",
      "description": "Capture on interrupt assertion"
    }
  ]
}
```

### Writing trigger plans

1. **Identify signals:** Use the same signals verified by `vcd_signal_map.json`
   in Stages 7-8. The ILA trigger plan probes the same state transitions.

2. **Probe paths:** Use the hierarchical path as it appears in the Vivado ILA
   dashboard. Typically `hil_top_i/dut/<signal_name>`. Check the `.ltx` file
   for exact probe names.

3. **Values:** Binary string for multi-bit probes (e.g. FSM state encoding).
   Single bit for flags. Must match the signal width.

4. **Output naming:** Use `ila_<descriptive_name>.csv`. Stage 19 finds all
   `ila_*.csv` files in `build/hil/`.

5. **Claude assistance:** Claude can generate the trigger plan from VHDL FSM
   state type declarations. Provide the state enum and the signal map, and
   Claude will produce the JSON.

---

## State Hashing and Rebuild

`hil.json` is tracked by `state_manager.py` with re-entry at Stage 14:

```python
HASH_FILES = {"hil.json": 14}
```

- If `hil.json` changes -> re-enter at Stage 14 (rebuild everything)
- If `src/*.vhd` changes -> re-enter at Stage 4 (cascades through synthesis
  and HIL)
- If nothing changes -> CACHED (skip pipeline)

---

## CLI Flags

| Flag | Effect |
|------|--------|
| `--hil` | Run HIL workflow: stages 0, 10, 14-19 |
| `--no-hw` | Skip stages 17-19 (build but don't program) |
| `--auto-confirm` | Skip Stage 17 confirmation prompt (for CI) |
| `--stages 14` | Run Stage 14 only |
| `--stages 14,15,16` | Run specific HIL stages |

---

## Troubleshooting

### "No hil.json -- skipping HIL stages"
Create `hil.json` in the project root. See schema above.

### "XSCT not found"
XSCT ships with Vitis SDK, not Vivado. Install Vitis or set the `XSCT`
environment variable to the XSCT binary path.

### "No serial port found"
- Check board USB connection
- Verify user is in `dialout` group: `sudo usermod -a -G dialout $USER`
- Override with `--serial /dev/ttyUSBx`
- Set `board.serial_vid`/`board.serial_pid` in `hil.json`

### Programming fails (XSDB error)
- Check JTAG cable connection
- Verify only one Zynq target is connected
- Check `ps7_init.tcl` exists in `build/hil/`

### UART timeout (no HIL_PASS/HIL_FAIL)
- Verify firmware prints the marker string
- Check baud rate (default 115200)
- Increase timeout: `firmware.timeout_s` in `hil.json` or `--timeout` flag
- Check serial port wiring

### ILA trigger timeout
- Verify probe path matches `.ltx` file
- Check trigger value matches signal encoding
- Ensure firmware sends "G" response (ILA pacing protocol)

### "No VCD from simulation -- skipping ILA"
Run simulation stages 7-8 first to generate VCD. ILA stages 18-19 are the
hardware extension of simulation verification -- they require VCD as the
reference baseline.

### Stage 19 state sequence mismatch
- ILA captures a different window than VCD. Check that the trigger captures
  the same state transitions.
- Verify signal name mapping: ILA probe names may have `_s` suffix that
  gets stripped during comparison.
- Check that the FSM encoding in hardware matches simulation (no synthesis
  optimisation changing state values).

---

## Verification Chain

The HIL flow extends the simulation verification chain:

```
Stage 5 (Python TB)      -> ground truth model
Stage 7 (SV/Xsim + VCD) -> RTL simulation against model
Stage 8 (VCD verify)     -> automated VCD signal checks

Stage 14-16 (build)      -> synthesize DUT for real hardware
Stage 17 (program+test)  -> functional test on silicon
Stage 18 (ILA capture)   -> capture real waveforms
Stage 19 (ILA verify)    -> confirm hardware matches simulation
```

The same `vcd_signal_map.json` used by Stages 7-8 informs the ILA trigger
plan. HIL doesn't introduce new verification criteria -- it confirms that
simulation-proven behaviour holds on silicon.
