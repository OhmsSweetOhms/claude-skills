# MicroZed 7020 Board Reference

## Part Information
- **FPGA Part:** xc7z020clg400-1 (NOT clg484 -- CLG484 is ZC706, not MicroZed)
- **Package:** CLG400
- **Speed grade:** -1

## UART
- **Chip:** Silicon Labs CP2104
- **USB VID:** 10c4
- **USB PID:** ea60
- **MIO:** 48 (TX), 49 (RX)
- **Baud:** 115200

## Power Notes
- **Bank 34 VCCO:** Unpowered by default; carrier must supply or connect 3.3V to JX1 pins 77-80
- **Bank 35 VCCO:** Same as Bank 34 -- requires carrier board power
- **Vadj jumper J18:** Set to 3.3V for Bank 34 I/O (LVCMOS33)

## Connectors
- **JX1:** Even pins are user I/O, odd pins are power/ground
- **JX2:** Even pins are user I/O, odd pins are power/ground
- **Bank 34:** JX1 Section 2 (24 differential pairs = 48 single-ended pins)
- **Bank 35:** JX2 Section 4 (24 differential pairs = 48 single-ended pins)
- **Bank 13:** JX1 Section 3 + JX2 Section 5 (7020 only, limited pins)

## PS7 Preset
Use `microzed_ps7_preset.tcl` in this directory. Configures:
- DDR3 (MT41K256M16 @ 533 MHz)
- M_AXI_GP0 enabled
- FCLK_CLK0 = 100 MHz
- UART1 on MIO 48/49
- USB, SD, Ethernet, QSPI

## Master XDC
`microzed_7020_master.xdc` contains all JX1/JX2 pin assignments.
All constraints are commented out by default -- uncomment as needed.
Clock-capable pins (MRCC/SRCC) are documented.

## Reference
Avnet MicroZed Hardware User Guide (HW UG)
