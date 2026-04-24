# ======================================================================
# MicroZed 7020 -- Master Pin Constraints Template
# ======================================================================
# Part:   XC7Z020-1CLG400C
# Board:  Avnet MicroZed (AES-Z7MB-7Z020-SOM-G)
# Source: 5276-MicroZed-HW-UG-v1-7-V1.pdf (Tables 8-15)
#         AES-MBCC-BRK-G-MBCC_BKO_UG_1_1.pdf (Tables 1-2)
#
# USAGE: Copy this file into your project constraints/ directory.
#        Uncomment only the pins you use. Delete or leave commented
#        any unused pins to avoid DRC warnings.
#
# IMPORTANT NOTES:
#   - PL banks (34, 35, 13) are UNPOWERED by default on the SOM.
#     The carrier card must supply VCCO_34, VCCO_35, and VCCO_13.
#   - Set IOSTANDARD to match your carrier's Vadj voltage.
#   - Bank 13 is 7020 ONLY (not present on 7010).
#   - Pmod J5 uses PS MIO pins -- no XDC constraints needed
#     (configured in PS block design, not PL fabric).
#   - User pushbutton (MIO[51]) and LED (MIO[47]) are also PS MIO.
# ======================================================================

# ======================================================================
# Section 1: JX1 MicroHeader (MicroHeader #1)
# ======================================================================
# JX1 carries Bank 34 I/O (49 pins) + Bank 13 I/O (8 pins, 7020 only)
# Plus JTAG, power, and dedicated pins.
# Source: HW UG Table 14
# ======================================================================

# --- JX1 Dedicated / Bank 0 (active regardless of carrier power) ------
# Pin  1: F9   JTAG_TCK       (Bank 0, dedicated)
# Pin  2: J6   JTAG_TMS       (Bank 0, dedicated)
# Pin  3: F6   JTAG_TDO       (Bank 0, dedicated)
# Pin  4: G6   JTAG_TDI       (Bank 0, dedicated)
# Pin  5: --   PWR_ENABLE     (carrier control, not FPGA)
# Pin  6: --   CARRIER_SRST#  (carrier control, not FPGA)
# Pin  7: F11  FPGA_VBATT     (Bank 0, battery backup)
# Pin  8: R11  FPGA_DONE      (Bank 0, config status)

# --- JX1 Bank 34 Single-Ended (pins 9-10) ----------------------------

# JX1 pin 9  | Bank 34, R19 | JX1_SE_0
#set_property PACKAGE_PIN R19      [get_ports {jx1_se_0}]
#set_property IOSTANDARD  LVCMOS33 [get_ports {jx1_se_0}]

# JX1 pin 10 | Bank 34, T19 | JX1_SE_1
#set_property PACKAGE_PIN T19      [get_ports {jx1_se_1}]
#set_property IOSTANDARD  LVCMOS33 [get_ports {jx1_se_1}]

# --- JX1 Bank 34 LVDS Pairs (pins 11-84) -----------------------------
# Pairs are listed as P (positive) then N (negative).
# Use as differential (LVDS_25, DIFF_SSTL18_II) or single-ended.
# Odd JX1 pins on left column, even on right column of header.

# JX1_LVDS_0: pin 11 (P) / pin 13 (N)
#set_property PACKAGE_PIN T11      [get_ports {jx1_lvds_0_p}]
#set_property IOSTANDARD  LVCMOS33 [get_ports {jx1_lvds_0_p}]
#set_property PACKAGE_PIN T10      [get_ports {jx1_lvds_0_n}]
#set_property IOSTANDARD  LVCMOS33 [get_ports {jx1_lvds_0_n}]

# JX1_LVDS_1: pin 12 (P) / pin 14 (N)
#set_property PACKAGE_PIN T12      [get_ports {jx1_lvds_1_p}]
#set_property IOSTANDARD  LVCMOS33 [get_ports {jx1_lvds_1_p}]
#set_property PACKAGE_PIN U12      [get_ports {jx1_lvds_1_n}]
#set_property IOSTANDARD  LVCMOS33 [get_ports {jx1_lvds_1_n}]

# JX1_LVDS_2: pin 17 (P) / pin 19 (N)
#set_property PACKAGE_PIN U13      [get_ports {jx1_lvds_2_p}]
#set_property IOSTANDARD  LVCMOS33 [get_ports {jx1_lvds_2_p}]
#set_property PACKAGE_PIN V13      [get_ports {jx1_lvds_2_n}]
#set_property IOSTANDARD  LVCMOS33 [get_ports {jx1_lvds_2_n}]

# JX1_LVDS_3: pin 18 (P) / pin 20 (N)
#set_property PACKAGE_PIN V12      [get_ports {jx1_lvds_3_p}]
#set_property IOSTANDARD  LVCMOS33 [get_ports {jx1_lvds_3_p}]
#set_property PACKAGE_PIN W13      [get_ports {jx1_lvds_3_n}]
#set_property IOSTANDARD  LVCMOS33 [get_ports {jx1_lvds_3_n}]

# JX1_LVDS_4: pin 23 (P) / pin 25 (N)
#set_property PACKAGE_PIN T14      [get_ports {jx1_lvds_4_p}]
#set_property IOSTANDARD  LVCMOS33 [get_ports {jx1_lvds_4_p}]
#set_property PACKAGE_PIN T15      [get_ports {jx1_lvds_4_n}]
#set_property IOSTANDARD  LVCMOS33 [get_ports {jx1_lvds_4_n}]

# JX1_LVDS_5: pin 24 (P) / pin 26 (N)
#set_property PACKAGE_PIN P14      [get_ports {jx1_lvds_5_p}]
#set_property IOSTANDARD  LVCMOS33 [get_ports {jx1_lvds_5_p}]
#set_property PACKAGE_PIN R14      [get_ports {jx1_lvds_5_n}]
#set_property IOSTANDARD  LVCMOS33 [get_ports {jx1_lvds_5_n}]

# JX1_LVDS_6: pin 29 (P) / pin 31 (N)
#set_property PACKAGE_PIN Y16      [get_ports {jx1_lvds_6_p}]
#set_property IOSTANDARD  LVCMOS33 [get_ports {jx1_lvds_6_p}]
#set_property PACKAGE_PIN Y17      [get_ports {jx1_lvds_6_n}]
#set_property IOSTANDARD  LVCMOS33 [get_ports {jx1_lvds_6_n}]

# JX1_LVDS_7: pin 30 (P) / pin 32 (N)
#set_property PACKAGE_PIN W14      [get_ports {jx1_lvds_7_p}]
#set_property IOSTANDARD  LVCMOS33 [get_ports {jx1_lvds_7_p}]
#set_property PACKAGE_PIN Y14      [get_ports {jx1_lvds_7_n}]
#set_property IOSTANDARD  LVCMOS33 [get_ports {jx1_lvds_7_n}]

# JX1_LVDS_8: pin 35 (P) / pin 37 (N)
#set_property PACKAGE_PIN T16      [get_ports {jx1_lvds_8_p}]
#set_property IOSTANDARD  LVCMOS33 [get_ports {jx1_lvds_8_p}]
#set_property PACKAGE_PIN U17      [get_ports {jx1_lvds_8_n}]
#set_property IOSTANDARD  LVCMOS33 [get_ports {jx1_lvds_8_n}]

# JX1_LVDS_9: pin 36 (P) / pin 38 (N)
#set_property PACKAGE_PIN V15      [get_ports {jx1_lvds_9_p}]
#set_property IOSTANDARD  LVCMOS33 [get_ports {jx1_lvds_9_p}]
#set_property PACKAGE_PIN W15      [get_ports {jx1_lvds_9_n}]
#set_property IOSTANDARD  LVCMOS33 [get_ports {jx1_lvds_9_n}]

# JX1_LVDS_10: pin 41 (P) / pin 43 (N) -- SRCC (clock capable)
#set_property PACKAGE_PIN U14      [get_ports {jx1_lvds_10_p}]
#set_property IOSTANDARD  LVCMOS33 [get_ports {jx1_lvds_10_p}]
#set_property PACKAGE_PIN U15      [get_ports {jx1_lvds_10_n}]
#set_property IOSTANDARD  LVCMOS33 [get_ports {jx1_lvds_10_n}]

# JX1_LVDS_11: pin 42 (P) / pin 44 (N) -- MRCC (clock capable)
#set_property PACKAGE_PIN U18      [get_ports {jx1_lvds_11_p}]
#set_property IOSTANDARD  LVCMOS33 [get_ports {jx1_lvds_11_p}]
#set_property PACKAGE_PIN U19      [get_ports {jx1_lvds_11_n}]
#set_property IOSTANDARD  LVCMOS33 [get_ports {jx1_lvds_11_n}]

# JX1_LVDS_12: pin 47 (P) / pin 49 (N) -- MRCC (clock capable)
#set_property PACKAGE_PIN N18      [get_ports {jx1_lvds_12_p}]
#set_property IOSTANDARD  LVCMOS33 [get_ports {jx1_lvds_12_p}]
#set_property PACKAGE_PIN P19      [get_ports {jx1_lvds_12_n}]
#set_property IOSTANDARD  LVCMOS33 [get_ports {jx1_lvds_12_n}]

# JX1_LVDS_13: pin 48 (P) / pin 50 (N) -- SRCC (clock capable)
#set_property PACKAGE_PIN N20      [get_ports {jx1_lvds_13_p}]
#set_property IOSTANDARD  LVCMOS33 [get_ports {jx1_lvds_13_p}]
#set_property PACKAGE_PIN P20      [get_ports {jx1_lvds_13_n}]
#set_property IOSTANDARD  LVCMOS33 [get_ports {jx1_lvds_13_n}]

# JX1_LVDS_14: pin 53 (P) / pin 55 (N) -- DQS
#set_property PACKAGE_PIN T20      [get_ports {jx1_lvds_14_p}]
#set_property IOSTANDARD  LVCMOS33 [get_ports {jx1_lvds_14_p}]
#set_property PACKAGE_PIN U20      [get_ports {jx1_lvds_14_n}]
#set_property IOSTANDARD  LVCMOS33 [get_ports {jx1_lvds_14_n}]

# JX1_LVDS_15: pin 54 (P) / pin 56 (N)
#set_property PACKAGE_PIN V20      [get_ports {jx1_lvds_15_p}]
#set_property IOSTANDARD  LVCMOS33 [get_ports {jx1_lvds_15_p}]
#set_property PACKAGE_PIN W20      [get_ports {jx1_lvds_15_n}]
#set_property IOSTANDARD  LVCMOS33 [get_ports {jx1_lvds_15_n}]

# -- Pins 57-60: VIN_HDR (power, not FPGA I/O) --

# JX1_LVDS_16: pin 61 (P) / pin 63 (N)
#set_property PACKAGE_PIN Y18      [get_ports {jx1_lvds_16_p}]
#set_property IOSTANDARD  LVCMOS33 [get_ports {jx1_lvds_16_p}]
#set_property PACKAGE_PIN Y19      [get_ports {jx1_lvds_16_n}]
#set_property IOSTANDARD  LVCMOS33 [get_ports {jx1_lvds_16_n}]

# JX1_LVDS_17: pin 62 (P) / pin 64 (N)
#set_property PACKAGE_PIN V16      [get_ports {jx1_lvds_17_p}]
#set_property IOSTANDARD  LVCMOS33 [get_ports {jx1_lvds_17_p}]
#set_property PACKAGE_PIN W16      [get_ports {jx1_lvds_17_n}]
#set_property IOSTANDARD  LVCMOS33 [get_ports {jx1_lvds_17_n}]

# JX1_LVDS_18: pin 67 (P) / pin 69 (N) -- N is VREF
#set_property PACKAGE_PIN R16      [get_ports {jx1_lvds_18_p}]
#set_property IOSTANDARD  LVCMOS33 [get_ports {jx1_lvds_18_p}]
#set_property PACKAGE_PIN R17      [get_ports {jx1_lvds_18_n}]
#set_property IOSTANDARD  LVCMOS33 [get_ports {jx1_lvds_18_n}]

# JX1_LVDS_19: pin 68 (P) / pin 70 (N)
#set_property PACKAGE_PIN T17      [get_ports {jx1_lvds_19_p}]
#set_property IOSTANDARD  LVCMOS33 [get_ports {jx1_lvds_19_p}]
#set_property PACKAGE_PIN R18      [get_ports {jx1_lvds_19_n}]
#set_property IOSTANDARD  LVCMOS33 [get_ports {jx1_lvds_19_n}]

# JX1_LVDS_20: pin 73 (P) / pin 75 (N) -- DQS
#set_property PACKAGE_PIN V17      [get_ports {jx1_lvds_20_p}]
#set_property IOSTANDARD  LVCMOS33 [get_ports {jx1_lvds_20_p}]
#set_property PACKAGE_PIN V18      [get_ports {jx1_lvds_20_n}]
#set_property IOSTANDARD  LVCMOS33 [get_ports {jx1_lvds_20_n}]

# JX1_LVDS_21: pin 74 (P) / pin 76 (N)
#set_property PACKAGE_PIN W18      [get_ports {jx1_lvds_21_p}]
#set_property IOSTANDARD  LVCMOS33 [get_ports {jx1_lvds_21_p}]
#set_property PACKAGE_PIN W19      [get_ports {jx1_lvds_21_n}]
#set_property IOSTANDARD  LVCMOS33 [get_ports {jx1_lvds_21_n}]

# -- Pins 77-80: VCCO_34 (power, not I/O) --

# JX1_LVDS_22: pin 81 (P) / pin 83 (N)
#set_property PACKAGE_PIN N17      [get_ports {jx1_lvds_22_p}]
#set_property IOSTANDARD  LVCMOS33 [get_ports {jx1_lvds_22_p}]
#set_property PACKAGE_PIN P18      [get_ports {jx1_lvds_22_n}]
#set_property IOSTANDARD  LVCMOS33 [get_ports {jx1_lvds_22_n}]

# JX1_LVDS_23: pin 82 (P) / pin 84 (N)
#set_property PACKAGE_PIN P15      [get_ports {jx1_lvds_23_p}]
#set_property IOSTANDARD  LVCMOS33 [get_ports {jx1_lvds_23_p}]
#set_property PACKAGE_PIN P16      [get_ports {jx1_lvds_23_n}]
#set_property IOSTANDARD  LVCMOS33 [get_ports {jx1_lvds_23_n}]

# --- JX1 Bank 13 (pins 87-94, 7020 ONLY) -----------------------------
# Bank 13 VCCO is powered independently from carrier (VCCO_13 rail).

# BANK13_LVDS_0: pin 87 (P) / pin 89 (N) -- SRCC (clock capable)
#set_property PACKAGE_PIN U7       [get_ports {bank13_lvds_0_p}]
#set_property IOSTANDARD  LVCMOS33 [get_ports {bank13_lvds_0_p}]
#set_property PACKAGE_PIN V7       [get_ports {bank13_lvds_0_n}]
#set_property IOSTANDARD  LVCMOS33 [get_ports {bank13_lvds_0_n}]

# BANK13_LVDS_1: pin 88 (P) / pin 90 (N) -- MRCC (clock capable)
#set_property PACKAGE_PIN T9       [get_ports {bank13_lvds_1_p}]
#set_property IOSTANDARD  LVCMOS33 [get_ports {bank13_lvds_1_p}]
#set_property PACKAGE_PIN U10      [get_ports {bank13_lvds_1_n}]
#set_property IOSTANDARD  LVCMOS33 [get_ports {bank13_lvds_1_n}]

# BANK13_LVDS_2: pin 91 (P) / pin 93 (N) -- DQS
#set_property PACKAGE_PIN V8       [get_ports {bank13_lvds_2_p}]
#set_property IOSTANDARD  LVCMOS33 [get_ports {bank13_lvds_2_p}]
#set_property PACKAGE_PIN W8       [get_ports {bank13_lvds_2_n}]
#set_property IOSTANDARD  LVCMOS33 [get_ports {bank13_lvds_2_n}]

# BANK13_LVDS_3: pin 92 (P) / pin 94 (N) -- N is VREF
#set_property PACKAGE_PIN T5       [get_ports {bank13_lvds_3_p}]
#set_property IOSTANDARD  LVCMOS33 [get_ports {bank13_lvds_3_p}]
#set_property PACKAGE_PIN U5       [get_ports {bank13_lvds_3_n}]
#set_property IOSTANDARD  LVCMOS33 [get_ports {bank13_lvds_3_n}]

# ======================================================================
# Section 2: JX2 MicroHeader (MicroHeader #2)
# ======================================================================
# JX2 carries Bank 35 I/O (50 pins) + Bank 13 I/O (8 pins, 7020 only)
# Plus PS MIO Pmod, PS control, and power pins.
# Source: HW UG Table 15
# ======================================================================

# --- JX2 PS Pmod (pins 1-8, directly on JX2) -------------------------
# These are PS MIO pins -- configured in block design, NOT in XDC.
# Pin 1: E8  PMOD_D0 (MIO 13)   Pin 2: E9  PMOD_D1 (MIO 10)
# Pin 3: C6  PMOD_D2 (MIO 11)   Pin 4: D9  PMOD_D3 (MIO 12)
# Pin 5: E6  PMOD_D4 (MIO 0)    Pin 6: B5  PMOD_D5 (MIO 9)
# Pin 7: C5  PMOD_D6 (MIO 14)   Pin 8: C8  PMOD_D7 (MIO 15)

# --- JX2 PS Control (pins 9-12) --------------------------------------
# Pin  9: R10  INIT# (Bank 0)
# Pin 10: --   VCCIO_EN (carrier)
# Pin 11: C7   PG_MODULE (Bank 500)
# Pin 12: --   VIN_HDR

# --- JX2 Bank 35 Single-Ended (pins 13-14) ---------------------------

# JX2 pin 13 | Bank 35, G14 | JX2_SE_0
#set_property PACKAGE_PIN G14      [get_ports {jx2_se_0}]
#set_property IOSTANDARD  LVCMOS33 [get_ports {jx2_se_0}]

# JX2 pin 14 | Bank 35, J15 | JX2_SE_1
#set_property PACKAGE_PIN J15      [get_ports {jx2_se_1}]
#set_property IOSTANDARD  LVCMOS33 [get_ports {jx2_se_1}]

# --- JX2 Bank 35 LVDS Pairs (pins 17-90) -----------------------------

# JX2_LVDS_0: pin 17 (P) / pin 19 (N)
#set_property PACKAGE_PIN C20      [get_ports {jx2_lvds_0_p}]
#set_property IOSTANDARD  LVCMOS33 [get_ports {jx2_lvds_0_p}]
#set_property PACKAGE_PIN B20      [get_ports {jx2_lvds_0_n}]
#set_property IOSTANDARD  LVCMOS33 [get_ports {jx2_lvds_0_n}]

# JX2_LVDS_1: pin 18 (P) / pin 20 (N)
#set_property PACKAGE_PIN B19      [get_ports {jx2_lvds_1_p}]
#set_property IOSTANDARD  LVCMOS33 [get_ports {jx2_lvds_1_p}]
#set_property PACKAGE_PIN A20      [get_ports {jx2_lvds_1_n}]
#set_property IOSTANDARD  LVCMOS33 [get_ports {jx2_lvds_1_n}]

# JX2_LVDS_2: pin 23 (P) / pin 25 (N) -- DQS
#set_property PACKAGE_PIN E17      [get_ports {jx2_lvds_2_p}]
#set_property IOSTANDARD  LVCMOS33 [get_ports {jx2_lvds_2_p}]
#set_property PACKAGE_PIN D18      [get_ports {jx2_lvds_2_n}]
#set_property IOSTANDARD  LVCMOS33 [get_ports {jx2_lvds_2_n}]

# JX2_LVDS_3: pin 24 (P) / pin 26 (N)
#set_property PACKAGE_PIN D19      [get_ports {jx2_lvds_3_p}]
#set_property IOSTANDARD  LVCMOS33 [get_ports {jx2_lvds_3_p}]
#set_property PACKAGE_PIN D20      [get_ports {jx2_lvds_3_n}]
#set_property IOSTANDARD  LVCMOS33 [get_ports {jx2_lvds_3_n}]

# JX2_LVDS_4: pin 29 (P) / pin 31 (N)
#set_property PACKAGE_PIN E18      [get_ports {jx2_lvds_4_p}]
#set_property IOSTANDARD  LVCMOS33 [get_ports {jx2_lvds_4_p}]
#set_property PACKAGE_PIN E19      [get_ports {jx2_lvds_4_n}]
#set_property IOSTANDARD  LVCMOS33 [get_ports {jx2_lvds_4_n}]

# JX2_LVDS_5: pin 30 (P) / pin 32 (N)
#set_property PACKAGE_PIN F16      [get_ports {jx2_lvds_5_p}]
#set_property IOSTANDARD  LVCMOS33 [get_ports {jx2_lvds_5_p}]
#set_property PACKAGE_PIN F17      [get_ports {jx2_lvds_5_n}]
#set_property IOSTANDARD  LVCMOS33 [get_ports {jx2_lvds_5_n}]

# JX2_LVDS_6: pin 35 (P) / pin 37 (N) -- DQS
#set_property PACKAGE_PIN L19      [get_ports {jx2_lvds_6_p}]
#set_property IOSTANDARD  LVCMOS33 [get_ports {jx2_lvds_6_p}]
#set_property PACKAGE_PIN L20      [get_ports {jx2_lvds_6_n}]
#set_property IOSTANDARD  LVCMOS33 [get_ports {jx2_lvds_6_n}]

# JX2_LVDS_7: pin 36 (P) / pin 38 (N)
#set_property PACKAGE_PIN M19      [get_ports {jx2_lvds_7_p}]
#set_property IOSTANDARD  LVCMOS33 [get_ports {jx2_lvds_7_p}]
#set_property PACKAGE_PIN M20      [get_ports {jx2_lvds_7_n}]
#set_property IOSTANDARD  LVCMOS33 [get_ports {jx2_lvds_7_n}]

# JX2_LVDS_8: pin 41 (P) / pin 43 (N)
#set_property PACKAGE_PIN M17      [get_ports {jx2_lvds_8_p}]
#set_property IOSTANDARD  LVCMOS33 [get_ports {jx2_lvds_8_p}]
#set_property PACKAGE_PIN M18      [get_ports {jx2_lvds_8_n}]
#set_property IOSTANDARD  LVCMOS33 [get_ports {jx2_lvds_8_n}]

# JX2_LVDS_9: pin 42 (P) / pin 44 (N)
#set_property PACKAGE_PIN K19      [get_ports {jx2_lvds_9_p}]
#set_property IOSTANDARD  LVCMOS33 [get_ports {jx2_lvds_9_p}]
#set_property PACKAGE_PIN J19      [get_ports {jx2_lvds_9_n}]
#set_property IOSTANDARD  LVCMOS33 [get_ports {jx2_lvds_9_n}]

# JX2_LVDS_10: pin 47 (P) / pin 49 (N) -- MRCC (clock capable)
#set_property PACKAGE_PIN L16      [get_ports {jx2_lvds_10_p}]
#set_property IOSTANDARD  LVCMOS33 [get_ports {jx2_lvds_10_p}]
#set_property PACKAGE_PIN L17      [get_ports {jx2_lvds_10_n}]
#set_property IOSTANDARD  LVCMOS33 [get_ports {jx2_lvds_10_n}]

# JX2_LVDS_11: pin 48 (P) / pin 50 (N) -- SRCC (clock capable)
#set_property PACKAGE_PIN K17      [get_ports {jx2_lvds_11_p}]
#set_property IOSTANDARD  LVCMOS33 [get_ports {jx2_lvds_11_p}]
#set_property PACKAGE_PIN K18      [get_ports {jx2_lvds_11_n}]
#set_property IOSTANDARD  LVCMOS33 [get_ports {jx2_lvds_11_n}]

# JX2_LVDS_12: pin 53 (P) / pin 55 (N)
#set_property PACKAGE_PIN H16      [get_ports {jx2_lvds_12_p}]
#set_property IOSTANDARD  LVCMOS33 [get_ports {jx2_lvds_12_p}]
#set_property PACKAGE_PIN H17      [get_ports {jx2_lvds_12_n}]
#set_property IOSTANDARD  LVCMOS33 [get_ports {jx2_lvds_12_n}]

# JX2_LVDS_13: pin 54 (P) / pin 56 (N)
#set_property PACKAGE_PIN J18      [get_ports {jx2_lvds_13_p}]
#set_property IOSTANDARD  LVCMOS33 [get_ports {jx2_lvds_13_p}]
#set_property PACKAGE_PIN H18      [get_ports {jx2_lvds_13_n}]
#set_property IOSTANDARD  LVCMOS33 [get_ports {jx2_lvds_13_n}]

# -- Pins 57-60: VIN_HDR (power, not I/O) --

# JX2_LVDS_14: pin 61 (P) / pin 63 (N)
#set_property PACKAGE_PIN G17      [get_ports {jx2_lvds_14_p}]
#set_property IOSTANDARD  LVCMOS33 [get_ports {jx2_lvds_14_p}]
#set_property PACKAGE_PIN G18      [get_ports {jx2_lvds_14_n}]
#set_property IOSTANDARD  LVCMOS33 [get_ports {jx2_lvds_14_n}]

# JX2_LVDS_15: pin 62 (P) / pin 64 (N)
#set_property PACKAGE_PIN F19      [get_ports {jx2_lvds_15_p}]
#set_property IOSTANDARD  LVCMOS33 [get_ports {jx2_lvds_15_p}]
#set_property PACKAGE_PIN F20      [get_ports {jx2_lvds_15_n}]
#set_property IOSTANDARD  LVCMOS33 [get_ports {jx2_lvds_15_n}]

# JX2_LVDS_16: pin 67 (P) / pin 69 (N)
#set_property PACKAGE_PIN G19      [get_ports {jx2_lvds_16_p}]
#set_property IOSTANDARD  LVCMOS33 [get_ports {jx2_lvds_16_p}]
#set_property PACKAGE_PIN G20      [get_ports {jx2_lvds_16_n}]
#set_property IOSTANDARD  LVCMOS33 [get_ports {jx2_lvds_16_n}]

# JX2_LVDS_17: pin 68 (P) / pin 70 (N) -- DQS
#set_property PACKAGE_PIN J20      [get_ports {jx2_lvds_17_p}]
#set_property IOSTANDARD  LVCMOS33 [get_ports {jx2_lvds_17_p}]
#set_property PACKAGE_PIN H20      [get_ports {jx2_lvds_17_n}]
#set_property IOSTANDARD  LVCMOS33 [get_ports {jx2_lvds_17_n}]

# JX2_LVDS_18: pin 73 (P) / pin 75 (N)
#set_property PACKAGE_PIN K14      [get_ports {jx2_lvds_18_p}]
#set_property IOSTANDARD  LVCMOS33 [get_ports {jx2_lvds_18_p}]
#set_property PACKAGE_PIN J14      [get_ports {jx2_lvds_18_n}]
#set_property IOSTANDARD  LVCMOS33 [get_ports {jx2_lvds_18_n}]

# JX2_LVDS_19: pin 74 (P) / pin 76 (N) -- VREF
#set_property PACKAGE_PIN H15      [get_ports {jx2_lvds_19_p}]
#set_property IOSTANDARD  LVCMOS33 [get_ports {jx2_lvds_19_p}]
#set_property PACKAGE_PIN G15      [get_ports {jx2_lvds_19_n}]
#set_property IOSTANDARD  LVCMOS33 [get_ports {jx2_lvds_19_n}]

# -- Pins 77-80: VCCO_35 (power, not I/O) --

# JX2_LVDS_20: pin 81 (P) / pin 83 (N)
#set_property PACKAGE_PIN N15      [get_ports {jx2_lvds_20_p}]
#set_property IOSTANDARD  LVCMOS33 [get_ports {jx2_lvds_20_p}]
#set_property PACKAGE_PIN N16      [get_ports {jx2_lvds_20_n}]
#set_property IOSTANDARD  LVCMOS33 [get_ports {jx2_lvds_20_n}]

# JX2_LVDS_21: pin 82 (P) / pin 84 (N) -- DQS
#set_property PACKAGE_PIN L14      [get_ports {jx2_lvds_21_p}]
#set_property IOSTANDARD  LVCMOS33 [get_ports {jx2_lvds_21_p}]
#set_property PACKAGE_PIN L15      [get_ports {jx2_lvds_21_n}]
#set_property IOSTANDARD  LVCMOS33 [get_ports {jx2_lvds_21_n}]

# JX2_LVDS_22: pin 87 (P) / pin 89 (N)
#set_property PACKAGE_PIN M14      [get_ports {jx2_lvds_22_p}]
#set_property IOSTANDARD  LVCMOS33 [get_ports {jx2_lvds_22_p}]
#set_property PACKAGE_PIN M15      [get_ports {jx2_lvds_22_n}]
#set_property IOSTANDARD  LVCMOS33 [get_ports {jx2_lvds_22_n}]

# JX2_LVDS_23: pin 88 (P) / pin 90 (N)
#set_property PACKAGE_PIN K16      [get_ports {jx2_lvds_23_p}]
#set_property IOSTANDARD  LVCMOS33 [get_ports {jx2_lvds_23_p}]
#set_property PACKAGE_PIN J16      [get_ports {jx2_lvds_23_n}]
#set_property IOSTANDARD  LVCMOS33 [get_ports {jx2_lvds_23_n}]

# --- JX2 Bank 13 (pins 91-100, 7020 ONLY) ----------------------------

# BANK13_LVDS_4: pin 93 (P) / pin 95 (N)
#set_property PACKAGE_PIN Y12      [get_ports {bank13_lvds_4_p}]
#set_property IOSTANDARD  LVCMOS33 [get_ports {bank13_lvds_4_p}]
#set_property PACKAGE_PIN Y13      [get_ports {bank13_lvds_4_n}]
#set_property IOSTANDARD  LVCMOS33 [get_ports {bank13_lvds_4_n}]

# BANK13_LVDS_5: pin 94 (P) / pin 96 (N)
#set_property PACKAGE_PIN V11      [get_ports {bank13_lvds_5_p}]
#set_property IOSTANDARD  LVCMOS33 [get_ports {bank13_lvds_5_p}]
#set_property PACKAGE_PIN V10      [get_ports {bank13_lvds_5_n}]
#set_property IOSTANDARD  LVCMOS33 [get_ports {bank13_lvds_5_n}]

# BANK13_LVDS_6: pin 97 (P) / pin 99 (N)
#set_property PACKAGE_PIN V6       [get_ports {bank13_lvds_6_p}]
#set_property IOSTANDARD  LVCMOS33 [get_ports {bank13_lvds_6_p}]
#set_property PACKAGE_PIN W6       [get_ports {bank13_lvds_6_n}]
#set_property IOSTANDARD  LVCMOS33 [get_ports {bank13_lvds_6_n}]

# Pin 98: VCCO_13 (power)
# Pin 100: V5 | BANK13_SE_0
#set_property PACKAGE_PIN V5       [get_ports {bank13_se_0}]
#set_property IOSTANDARD  LVCMOS33 [get_ports {bank13_se_0}]

# ======================================================================
# Section 3: On-Board Analog (JX1 pins 97-100, active Bank 0)
# ======================================================================
# These are XADC dedicated analog inputs, active without carrier power.

# JX1 pin 97 | Bank 0, L10 | VP_0_P (XADC positive)
# JX1 pin 98 | Bank 0, M9  | DXP_0_P
# JX1 pin 99 | Bank 0, K9  | VN_0_N (XADC negative)
# JX1 pin 100| Bank 0, M10 | DXN_0_N

# ======================================================================
# Section 4: PS MIO Reference (no XDC needed -- block design config)
# ======================================================================
# Included for reference only. These are configured via the PS7
# block design GUI, not via XDC constraints.
#
# Pmod J5 Header (MIO Bank 0/500, 3.3V):
#   PMOD_D0 = MIO 13, E8     PMOD_D1 = MIO 10, E9
#   PMOD_D2 = MIO 11, C6     PMOD_D3 = MIO 12, D9
#   PMOD_D4 = MIO  0, E6     PMOD_D5 = MIO  9, B5
#   PMOD_D6 = MIO 14, C5     PMOD_D7 = MIO 15, C8
#
# User Push Button:
#   PB1 = MIO 51, B9 (Bank 501, active low with pull-down)
#
# User LED:
#   D3  = MIO 47, B14 (Bank 501, active high)
#
# UART (active by default, directly on SOM):
#   UART1_TX = MIO 49    UART1_RX = MIO 48
#
# USB-UART Bridge (directly on SOM, active without carrier):
#   Connected to Silicon Labs CP2104 via MIO 48/49
# ======================================================================

# ======================================================================
# Section 5: Quick-Reference Pin Count Summary
# ======================================================================
#
# Connector | Bank(s)   | PL I/O Pins | Notes
# ----------+-----------+-------------+-------------------------------
# JX1       | 34        | 49          | 2 SE + 24 diff pairs (48 pins)
# JX1       | 13        |  8          | 7020 only, 4 diff pairs
# JX2       | 35        | 50          | 2 SE + 24 diff pairs (48 pins)
# JX2       | 13        |  8+1        | 7020 only, 3 pairs + 1 SE + VCCO
# ----------+-----------+-------------+-------------------------------
# Total PL I/O:           116 (7020)  or 100 (7010, no Bank 13)
#
# Clock-capable inputs (MRCC/SRCC):
#   JX1 Bank 34: LVDS_10(SRCC), LVDS_11(MRCC), LVDS_12(MRCC), LVDS_13(SRCC)
#   JX1 Bank 13: LVDS_0(SRCC), LVDS_1(MRCC)     [7020 only]
#   JX2 Bank 35: LVDS_10(MRCC), LVDS_11(SRCC)
#
# ======================================================================
