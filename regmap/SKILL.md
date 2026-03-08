---
name: regmap
description: "Register map synchronisation checker. Use when a VHDL register map changes, when adding/removing registers, or when the user asks to check consistency between VHDL, Python testbench, SV testbench, C driver, and documentation. Parses the VHDL register decode as the source of truth and diffs against all other layers to detect drift."
---

# Register Map Synchroniser

Check and synchronise the register map across all project layers.

## When to Use

- After adding, removing, or modifying a register in VHDL
- User asks to check register map consistency
- User asks "are the registers in sync" or similar
- Before a release or after a large RTL change
- When a testbench fails on a register access and the cause is unclear

## Source of Truth

The VHDL register decode logic is the single source of truth. All other
layers must match it exactly.

## Layers to Check

| Layer | Typical Location | What to Extract |
|-------|-----------------|-----------------|
| VHDL RTL | `src/*.vhd` | Register addresses, field bit ranges, access type (RO/RW/W1C/WO), reset defaults |
| Python TB | `tb/*_tb.py` | Register address constants, field bit masks, read/write helpers |
| SV TB | `tb/*_tb.sv` | Register address parameters/defines, field offsets |
| C header | `sw/*.h` | `#define` register offsets, bit field macros, access helpers |
| C source | `sw/*.c` | Driver functions that access registers |
| Documentation | `docs/README.md` | Register map tables |

## Step 1: Parse the VHDL Register Map

Read the VHDL source and extract:

1. **AXI decode case statement** -- find the `when` cases in the read and
   write processes. Each case constant is a register offset.
2. **Register signals** -- find all `signal X_r : std_logic_vector(...)` that
   are assigned in the write decode and/or read back in the read decode.
3. **Field bit ranges** -- from the assignments, determine which bits of each
   register are implemented.
4. **Access type** -- RW if in both read and write decode; RO if read-only;
   WO if write-only; W1C if the write logic clears on write-1.
5. **Reset defaults** -- from the reset block, find the default value of each
   register.

Build a table:

```
VHDL Register Map (source of truth)
====================================
Offset  Name           Bits    Access  Reset
------  ----           ----    ------  -----
0x00    CTRL           [3:0]   RW      0x00
0x04    STATUS         [4:0]   mixed   0x00
  [0]   tx_busy                RO
  [1]   rx_frame_valid         W1C
  ...
```

## Step 2: Parse Each Layer

For each layer that exists in the project, parse the register definitions
and build an equivalent table.

### Python TB
Look for:
- Dictionary or class with register addresses (e.g., `ADDR_CTRL = 0x00`)
- Bit field constants (e.g., `CTRL_TX_START = 0x01`)
- Read/write helper methods that reference addresses

### SV TB
Look for:
- `parameter` or `` `define`` with register addresses
- Task/function definitions that do AXI reads/writes to specific offsets

### C Header
Look for:
- `#define SDLC_REG_*` offset macros
- `#define SDLC_*_MASK` or `#define SDLC_*_BIT` field macros
- Struct typedefs if register access uses typed structs

### Documentation
Look for:
- Markdown tables with Offset, Name, Bits, Access columns
- Verify every register from VHDL appears in the doc table

## Step 3: Diff and Report

Compare each layer against the VHDL source of truth. Report:

```
Register Map Consistency Check
===============================

  [PASS] Python TB: all 12 registers match VHDL
  [FAIL] SV TB: missing register DPLL_UPDATE (0x1C)
  [FAIL] C header: TX_BIT_DIV default comment says 100, VHDL says 99
  [PASS] Documentation: all registers match

Detailed Mismatches:
  SV TB (tb/sdlc_axi_tb.sv):
    - Missing: DPLL_UPDATE at offset 0x1C (WO)

  C header (sw/sdlc_axi.h):
    - Line 45: comment says "default: 100" but VHDL reset value is 99
```

Categories of drift:
- **Missing register** -- a register exists in VHDL but not in the layer
- **Extra register** -- a register exists in the layer but not in VHDL
- **Wrong offset** -- register name exists but at a different address
- **Wrong bit range** -- field bits don't match
- **Wrong access type** -- e.g., marked RW in C header but is RO in VHDL
- **Wrong default** -- reset/default value mismatch
- **Wrong comment** -- descriptive text contradicts VHDL behaviour

## Step 4: Fix or Report

If mismatches are found:
1. Present the full diff to the user
2. Ask which layers to update (or all)
3. For each layer, make the minimal edit to bring it in sync with VHDL
4. **Never modify the VHDL** to match another layer -- VHDL is the source of truth
5. After fixes, re-run the check to confirm sync

## Notes

- This skill does NOT modify VHDL. If the user wants to change the register
  map, they should edit VHDL first, then run `/regmap` to propagate.
- When a register map change also affects the Python model's behaviour
  (not just constants), flag that the Python testbench logic may need
  updates beyond just address constants.
- W1C registers are a common source of drift -- the C driver must never
  read-modify-write these, and the testbench must model the clear-on-write-1
  behaviour correctly.
