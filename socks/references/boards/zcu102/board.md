# ZCU102 board reference

- Part: `xczu9eg-ffvb1156-2-e`
- Family: `zynqmp`
- USB-UART: Silicon Labs CP2108 quad UART. A connected board typically exposes four `/dev/ttyUSB*` devices. For the AMP streaming topology, UART0 is the A53 console and UART1 is the R5_0 console.
- USB VID/PID: `10c4` / `ea70`
- FMC slot for AD9986-FMC: `HPC0_FMC0`
- Master XDC: use the upstream Xilinx ZCU102 master XDC as reference. ADI Make sources the relevant ZCU102 constraints through `projects/common/zcu102/zcu102_system_constr.xdc` in the vendored ADI HDL subset.
- ADI reference design docs: https://analogdevicesinc.github.io/hdl/projects/ad9081_fmca_ebz/index.html

No PSU preset is stored here for the ADI flow. The Zynq UltraScale+ PS is configured by ADI HDL (`projects/common/zcu102/zcu102_system_bd.tcl`). A future non-ADI native ZCU102 flow should add a separate PSU preset rather than reusing this ADI-managed board reference.
