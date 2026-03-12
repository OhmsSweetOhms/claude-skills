# XDC Constraint Generator

Generate Vivado XDC timing constraints for FPGA designs. Read this reference
at Stage 10a, before the first synthesis run or when no `.xdc` file exists.

---

## Step 1: Analyse the Design

Read the VHDL source to identify:

1. **Clock ports** -- ports connected to `rising_edge()` (typically `clk`)
2. **Async input ports** -- ports that feed CDC synchronisers (`*_sync1_r`).
   Look for 2-FF chains with `ASYNC_REG` attribute.
3. **Output ports** -- all entity output ports
4. **Clock frequency** -- from generics (`SYS_CLK_HZ`) or CLAUDE.md
5. **Generated clocks** -- any internally divided clocks (baud counters, etc.)
   that drive output ports
6. **Multicycle paths** -- any paths the user or timing analysis has identified
   as needing multicycle constraints

## Step 2: Generate Constraints

Generate an XDC file with sections in this order:

### 2a. Primary Clock

```tcl
# ==============================================================
# Clock Definition
# ==============================================================
# System clock -- 100 MHz from PS FCLK_CLK0
create_clock -period 10.000 -name sys_clk [get_ports clk]
```

Use the actual frequency from the design. For Zynq designs where the clock
comes from the PS, note that the PS block design typically defines this clock
already -- in that case, comment out the `create_clock` and add a note.

### 2b. Generated Clocks (if any)

If the design has output clocks derived from the system clock (e.g.,
`tx_clk`), define them:

```tcl
# TX bit clock -- generated from sys_clk by baud counter
# Frequency varies with TX_BIT_DIV register; worst case = sys_clk/10
# create_generated_clock is not applicable here (software-configured divider)
# Instead, constrain the output port with set_output_delay
```

For software-configurable dividers, a generated clock constraint is usually
not appropriate. Use `set_output_delay` or `set_false_path` instead.

### 2c. Async Input False Paths

For each async input that has a CDC synchroniser in the RTL:

```tcl
# ==============================================================
# Async Input False Paths
# ==============================================================
# These inputs are asynchronous and pass through 2-FF synchronisers
# with ASYNC_REG="TRUE" in the RTL. No timing relationship to sys_clk.

# SDLC RX data inputs
set_false_path -to [get_cells -hierarchical -filter {NAME =~ *sdlc_rx_in_a_sync1_r_reg*}]
set_false_path -to [get_cells -hierarchical -filter {NAME =~ *sdlc_rx_in_b_sync1_r_reg*}]

# DPLL reference clock inputs
set_false_path -to [get_cells -hierarchical -filter {NAME =~ *ref_clk_in_a_sync1_r_reg*}]
set_false_path -to [get_cells -hierarchical -filter {NAME =~ *ref_clk_in_b_sync1_r_reg*}]
```

**Important:** Use `-to [get_cells ...]` targeting the sync1 register's D pin,
not `-from [get_ports ...]`. The `-from` form only covers the port-to-sync1
path but misses timing checks on the sync1 register itself. The `-to` form
correctly waives timing to the first synchroniser stage.

Verify that every async input port in the entity has a corresponding
false path. Cross-reference with VHDL `ASYNC_REG` attributes.

### 2d. I/O Delays (optional)

For synchronous I/O ports (not covered by false paths):

```tcl
# ==============================================================
# I/O Delays
# ==============================================================
# AXI bus I/O -- constrained by the interconnect; these are placeholder
# values for OOC synthesis. In-context, the block design provides these.
set_input_delay  -clock sys_clk 2.0 [get_ports s_axi_*]
set_output_delay -clock sys_clk 2.0 [get_ports s_axi_*]

# Serial TX output
set_output_delay -clock sys_clk 2.0 [get_ports tx_out]
set_output_delay -clock sys_clk 2.0 [get_ports tx_clk]
```

For out-of-context synthesis, use conservative placeholder values.
For in-context block design, these come from the interconnect.

### 2e. Multicycle Paths (if needed)

```tcl
# ==============================================================
# Multicycle Paths
# ==============================================================
# Example: DPLL parameter registers are written by software and stable
# for many cycles. Relax timing if needed.
# set_multicycle_path 2 -setup -from [get_cells *dpll_freq_sel_r_reg*]
# set_multicycle_path 1 -hold  -from [get_cells *dpll_freq_sel_r_reg*]
```

Only add multicycle constraints when justified by the design. Comment them
out by default with an explanation so the user can enable them consciously.

### 2f. Debug/Monitor Ports

```tcl
# ==============================================================
# Debug Ports -- false path (unconnected in production)
# ==============================================================
set_false_path -to [get_ports mon_*]
```

## Step 3: Validate

After generating the XDC:

1. Check that every async input port has a false path
2. Check that no constrained paths target CDC synchroniser registers
3. Verify the clock period matches the design's `SYS_CLK_HZ` generic
4. If the project already has constraints (in `sim/synth_timing.tcl` or
   `build/*.xdc`), diff against them and flag differences

## Step 4: Write the File

Write the XDC to `constraints/<top_entity>.xdc` (or the location the user
specifies). Also update `sim/synth_timing.tcl` if it has inline constraints
that should reference the XDC instead.

## Notes

- **OOC vs in-context:** Out-of-context synthesis needs explicit clock
  definitions. In a block design, the PS defines clocks. Generate both
  variants if the user is unsure.
- **Zynq PS clock:** If the clock comes from FCLK_CLK0, the block design
  automation creates the clock constraint. Adding a duplicate `create_clock`
  causes a multi-clock warning.
- **Hier filter patterns:** Use `*` wildcards in hierarchical cell names.
  Vivado may rename registers (e.g., adding `_reg` suffix). Test the filter
  pattern with `get_cells -hierarchical -filter {NAME =~ *pattern*}` in the
  Vivado TCL console.
