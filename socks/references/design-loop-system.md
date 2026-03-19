# System Design Loop (Stage 20)

This file is read by Claude when entering Stage 20 -- the system scope
equivalent of the design loop (stages 2-9 for module/block scope).

Stage 20 is guidance-only (no script). Claude authors all system deliverables
in one pass, then Stage 10 runs synthesis.

---

## Deliverables

Before Stage 10 can run, Stage 20 must produce:

1. **`build/synth/create_bd.tcl`** -- Vivado block design TCL script
2. **`build/synth/build_bitstream.tcl`** -- synthesis/implementation/bitstream TCL
3. **`constraints/*.xdc`** -- pin assignment and I/O standard constraints
4. **`docs/ARCHITECTURE.md`** -- Mermaid diagrams (data flow, clocking, rate summary)

---

## create_bd.tcl Authoring Guide

The TCL script creates a Vivado project and block design. Pattern:

```tcl
# Project creation
create_project system build/vivado_project -part {part} -force
set_property target_language VHDL [current_project]

# Block design
create_bd_design "system"

# PS7 with board preset
create_bd_cell -type ip -vlnv xilinx.com:ip:processing_system7:5.5 ps7
source {preset_tcl}  ;# apply_bd_automation or set_property calls

# IP cores
create_bd_cell -type ip -vlnv {ip_vlnv} {ip_name}
set_property -dict [list CONFIG.{param} {value} ...] [get_bd_cells {ip_name}]

# AXI interconnect
create_bd_cell -type ip -vlnv xilinx.com:ip:axi_interconnect:2.1 axi_ic
set_property CONFIG.NUM_SI 1 [get_bd_cells axi_ic]
set_property CONFIG.NUM_MI {N} [get_bd_cells axi_ic]

# Processor system reset
create_bd_cell -type ip -vlnv xilinx.com:ip:proc_sys_reset:5.0 ps_reset

# Connections (AXI, clocks, resets)
connect_bd_net [get_bd_pins ps7/FCLK_CLK0] [get_bd_pins axi_ic/ACLK] ...
connect_bd_intf_net [get_bd_intf_pins ps7/M_AXI_GP0] [get_bd_intf_pins axi_ic/S00_AXI]

# Address assignment
assign_bd_address

# External ports (for pin I/O)
create_bd_port -dir O {port_name}
connect_bd_net [get_bd_pins {ip}/{pin}] [get_bd_ports {port_name}]

# Generate wrapper
make_wrapper [get_files system.bd] -top
add_files [glob build/vivado_project/system.gen/sources_1/bd/system/hdl/system_wrapper.vhd]

# FCLK_CLK0 export (for ILA probe clock)
create_bd_port -dir O FCLK_CLK0
connect_bd_net [get_bd_pins ps7/FCLK_CLK0] [get_bd_ports FCLK_CLK0]

save_bd_design
close_project
```

**Key rules:**
- Always export FCLK_CLK0 from the block design if ILA probes are planned
- Use `assign_bd_address` for auto address assignment unless specific addresses required
- Source the PS7 preset TCL from `references/boards/<preset>/`
- Set target_language to VHDL

---

## build_bitstream.tcl Authoring Guide

```tcl
open_project build/vivado_project/system.xpr
set_property top system_wrapper [current_fileset]

# Add constraint files
add_files -fileset constrs_1 [glob constraints/*.xdc]

# Generate block design output products
generate_target all [get_files system.bd]

# Synthesis
launch_runs synth_1 -jobs 4
wait_on_run synth_1

# If MARK_DEBUG nets exist, handle ILA insertion here
# (see references/hil.md SS Inline Implementation for ILA Builds)

# Implementation
launch_runs impl_1 -to_step write_bitstream -jobs 4
wait_on_run impl_1

# Reports
open_run impl_1
report_utilization -file build/synth/utilization.rpt
report_timing_summary -file build/synth/timing.rpt

# Export XSA (for firmware)
write_hw_platform -fixed -include_bit -force build/synth/system_wrapper.xsa

close_project
```

**For ILA builds:** Do NOT use `launch_runs` after `implement_debug_core`. Use inline implementation instead:
```tcl
implement_debug_core
write_debug_probes -force system_wrapper.ltx
opt_design
place_design
route_design
write_bitstream -force system_wrapper.bit
```
See `references/hil.md` for the full ILA inline implementation pattern.

---

## XDC Authoring Guide

Pin constraints from DESIGN-INTENT.md:

```xdc
# Pin assignment from DESIGN-INTENT.md
set_property PACKAGE_PIN {ball} [get_ports {signal}]
set_property IOSTANDARD {std} [get_ports {signal}]

# Bank VCCO must match I/O standard voltage
# LVCMOS33 -> 3.3V, LVCMOS25 -> 2.5V, LVDS_25 -> 2.5V
```

**Rules:**
- One constraint file per functional group (e.g., `spi_pins.xdc`, `gpio_pins.xdc`) or one combined file
- Comment each pin with the connector pin and signal description
- Verify ball assignments against the master XDC in `references/boards/<preset>/`

---

## ARCHITECTURE.md Guide

Write two Mermaid diagrams + rate summary table:

1. **Data Flow Diagram** -- PS -> AXI Interconnect -> IP blocks -> external pins
2. **Clocking Diagram** -- FCLK_CLK0 fan-out to all IP blocks, derived clocks
3. **Rate Summary Table** -- every clock/rate with derivation

---

## Loop Re-Entry Table

| Stage 10 Error | What to Fix | Re-enter at |
|----------------|-------------|-------------|
| Synthesis error (missing IP, bad TCL) | Fix create_bd.tcl | Stage 20 |
| Pin DRC failure | Fix XDC constraints | Stage 20 |
| Timing violation | Adjust constraints or clocking | Stage 20 |
| Implementation error | Fix build_bitstream.tcl | Stage 20 |

If the same failure occurs 3 times, ask the user for guidance.

---

## Scope Creep Detection

If user wants custom RTL during Stage 20:
- Ask: "Does this warrant a separate `/socks --design block/module`?"
- Track sub-design reference in socks.json `sub_designs` array
- Do NOT auto-enter the module/block design loop from within the system design loop
