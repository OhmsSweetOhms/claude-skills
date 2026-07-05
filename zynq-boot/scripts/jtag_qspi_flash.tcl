# jtag_qspi_flash.tcl — boot-mode-independent QSPI flash bring-up, driven entirely
# over JTAG (no UART). Run via xsct; see scripts/jtag_qspi_flash.sh for the wrapper
# that resolves paths and sources the toolchain.
#
# STATUS: PS bring-up HARDWARE-VERIFIED on a hardwired-QSPI xc7z020 (2026-06-30): the
# AR 76051 halt, the DDR/IO PLL power-up (PLL_PWRDWN clear), ps7_init, the rst -processor
# MMU drop, and DRAM read/write all confirmed (PLL_STATUS 0x39->0x3F, DDRC_mode->1). Still
# UNVERIFIED downstream: whether the cfgmem helper presents an interactive DCC prompt, and
# the full sf erase/write/verify. See references/jtag-flash-bootmode-independent.md and the
# work/uboot thread (.threads/zynq-boot/20260629-hardwired-qspi-jtag-flash).
# NOTE: an incompatible ps7_init.tcl (wrong board) can WEDGE the debug DAP/TAP and require a
# physical power-cycle — use the .xsa/ps7_init that matches the board.
#
# WHY THIS EXISTS: program_flash regressed in 2020.x+ (AMD AR 76051) and fails on a
# board hardwired to QSPI/NAND boot mode. But the JTAG/DAP is always alive regardless
# of the strap pins, and ps7_init/psu_init reconfigures the whole PS (clocks, DDR,
# MIO incl. QSPI pins) by writing registers directly — it never reads the boot mode.
# So we halt the core before the BootROM can boot stale flash, re-init the PS, load a
# DCC-console U-Boot, and drive `sf` over the ARM DCC (the same JTAG channel
# program_flash uses — the cfgmem helper has NO UART driver, only drivers/serial/arm_dcc.c).
#
# All inputs arrive as environment variables (xsct inherits the shell env). Paths must
# be ABSOLUTE and pre-resolved — xsct does NOT expand $USER or shell vars (resolve them
# in the bash wrapper). Required:
#   ZB_ARCH    zynq | zynqmp
#   ZB_TGT     target name filter, e.g. {*Cortex-A9*#0} (Zynq-7000) / {*A53*#0} (ZynqMP)
#   ZB_UBOOT   abs path to a DCC-console U-Boot ELF (the cfgmem helper .bin IS an ELF;
#              or a custom CONFIG_ARM_DCC build). A stock ttyPS u-boot needs the UART.
# PS init — provide exactly ONE of:
#   ZB_PSINIT  abs path to ps7_init.tcl (Zynq-7000) / psu_init.tcl (ZynqMP). Direct
#              register writes, preferred — no side effects.
#   ZB_FSBL    abs path to an FSBL ELF. Does ps7_init by EXECUTING; we run it only until
#              DDR comes up, then halt before it boots from QSPI. Convenient when you have
#              the FSBL (e.g. from bootgen/Vitis) but no separate ps7_init.tcl.
# Optional:
#   ZB_PMUFW   abs path to pmufw.elf (ZynqMP only)
#   ZB_URL     hw_server url (default tcp:localhost:3121)

proc need {name} {
    if {![info exists ::env($name)]} { error "missing required env var: $name" }
    return $::env($name)
}
proc opt {name {dflt ""}} {
    if {[info exists ::env($name)]} { return $::env($name) } else { return $dflt }
}

set ARCH   [need ZB_ARCH]
set TGT    [need ZB_TGT]
set PSINIT [opt  ZB_PSINIT ""]
set FSBL   [opt  ZB_FSBL ""]
set UBOOT  [need ZB_UBOOT]
set URL    [opt  ZB_URL tcp:localhost:3121]

# PS init comes from EITHER ps7_init.tcl (ZB_PSINIT) OR an FSBL ELF (ZB_FSBL).
if {$PSINIT eq "" && $FSBL eq ""} {
    error "no PS init: set ZB_PSINIT (ps7_init.tcl) or ZB_FSBL (fsbl.elf)"
}
foreach f [list $PSINIT $FSBL $UBOOT] {
    if {$f ne "" && ![file exists $f]} { error "file not found (resolve paths in bash first): $f" }
}

puts "== connecting to $URL =="
connect -url $URL

# Select the APU core 0. -nocase so "Cortex-A9"/"cortex-a9" both match.
targets -set -nocase -filter "name =~ \"$TGT\""
puts "== target selected: $TGT =="

# --- THE CRUX: halt before the BootROM boots stale flash (AR 76051) ---
# rst -system resets and the core immediately starts the BootROM, which on a QSPI
# strap will try to boot whatever is in flash. stop halts it. On a board that boots
# stale flash too fast, a faster halt may be needed (rst -processor, or halt-on-reset)
# — capture that on the bring-up run.
puts "== rst -system ; stop  (halt BootROM) =="
# NO delay between reset and stop: the whole point (AR 76051) is to halt BEFORE the
# BootROM boots stale QSPI and seizes the PS. Even ~200 ms is enough for a small stale
# FSBL to boot. If the DAP needs a moment to re-enumerate after rst and `stop` errors,
# establish the MINIMUM settle empirically on the bring-up run (Stage 2 decision-tree A)
# — do not add a blanket delay here.
rst -system
stop

# --- Zynq-7000 PLL power-up (precondition for PS init) ---
# Some boot states leave the DDR/IO PLLs POWERED DOWN (slcr.{DDR,IO}_PLL_CTRL[PLL_PWRDWN]=1).
# Stock ps7_init — and an FSBL's internal ps7_init — only write the FDIV / BYPASS_FORCE /
# RESET fields; they NEVER clear PWRDWN. So they pulse RESET on a powered-down PLL, which can
# never lock: slcr.PLL_STATUS (0xF800010C) sticks at 0x39 (ARM locks, DDR/IO don't) and DDR
# never initializes. Power the two PLLs up first, per UG585 "Exit Sleep" (set PLL_PWRDWN=0
# before the lock sequence). Verified on a hardwired-QSPI xc7z020: PLL_STATUS 0x39 -> 0x3F,
# DDRC_mode -> 1, DRAM read/write OK. ZynqMP uses a different clock subsystem (psu_init owns
# its PLLs) — skip there.
if {$ARCH eq "zynq"} {
    puts "== power up DDR/IO PLLs (clear slcr.PLL_PWRDWN) before PS init =="
    mwr -force 0xF8000008 0x0000DF0D                                ;# SLCR unlock
    mwr -force 0xF8000104 [expr {[mrd -value 0xF8000104] & ~0x2}]   ;# DDR_PLL_CTRL[PLL_PWRDWN]=0
    mwr -force 0xF8000108 [expr {[mrd -value 0xF8000108] & ~0x2}]   ;# IO_PLL_CTRL [PLL_PWRDWN]=0
}

if {$ARCH eq "zynqmp"} {
    # Fake JTAG boot mode regardless of strap pins (ZynqMP only): BOOT_MODE_USER.
    # NOTE: the 0x100 bit value is UNVERIFIED against UG1085's BOOT_MODE_USER bitfield —
    # confirm before relying on the ZynqMP path. Zynq-7000 (the primary target) does not
    # use this register at all.
    puts "== ZynqMP: BOOT_MODE_USER <- JTAG (mwr 0xFF5E0200 0x100) \[UNVERIFIED\] =="
    mwr 0xFF5E0200 0x100
    if {[info exists ::env(ZB_PMUFW)]} {
        puts "== loading PMUFW: $::env(ZB_PMUFW) =="
        targets -set -nocase -filter {name =~ "*MicroBlaze PMU*"}
        dow $::env(ZB_PMUFW)
        con
        after 500
        targets -set -nocase -filter "name =~ \"$TGT\""
        stop
    }
}

# --- strap-independent PS bring-up: EITHER ps7_init (direct register writes, preferred,
#     no side effects) OR run the FSBL (it does ps7_init by executing). ---
if {$PSINIT ne ""} {
    puts "== PS init via $PSINIT (direct register writes) =="
    source $PSINIT
    set initproc [file rootname [file tail $PSINIT]]   ;# ps7_init or psu_init
    $initproc
    catch { ${initproc}_post_config }                  ;# PL/EMIO setup if present
} else {
    # An FSBL does ps7_init by RUNNING, but afterward it tries to boot from QSPI, which
    # would seize the PS. So run it only until DDR comes up (init done), then halt before
    # it touches the boot device. Poll DDR via the DAP (non-intrusive) while it runs —
    # this is version-independent (no reliance on FSBL symbol names).
    puts "== PS init via FSBL: $FSBL (run until DDR up, then halt before QSPI handoff) =="
    dow $FSBL
    con
    set ddr_up 0
    for {set i 1} {$i <= 50} {incr i} {
        after 100
        if {![catch {mrd -value 0x00100000}]} { set ddr_up 1; break }
    }
    catch {stop}
    if {$ddr_up} {
        puts "== FSBL init done: DDR up after [expr {$i * 100}] ms =="
    } else {
        puts "== WARNING: DDR not confirmed up within 5 s of FSBL run — proceeding cautiously =="
    }
}

# --- Zynq-7000 MMU drop (so debugger DRAM access works) ---
# A halted boot image usually leaves the APU MMU enabled, so debugger reads/writes to DRAM
# — loading U-Boot below, and staging the payload via 'dow -data' — fault with "MMU section
# translation fault" even though DDR is up. rst -processor resets ONLY the core (PC->0, MMU
# off) and leaves the PS/PLLs/DDR intact. Verified on xc7z020: DRAM read/write succeeds after
# this; harmless if the MMU was already off. ZynqMP A53 reset semantics differ — skip.
if {$ARCH eq "zynq"} {
    puts "== rst -processor (drop stale APU MMU; PS/PLL/DDR survive) =="
    rst -processor
    catch { stop }   ;# rst -processor leaves the core halted — ignore "Already stopped"
}

# --- Zynq-7000 OCM remap (so the cfgmem U-Boot helper loads at 0xFFFC0000) ---
# The Vitis cfgmem helper (zynq_qspi_x1_single.bin etc.) is linked/entered at 0xFFFC0000
# (high OCM). slcr.OCM_CFG[RAM_HI] resets to 0 → all four 64K OCM blocks map LOW, so
# 0xFFFC0000 is unbacked and `dow` fails "OCM is not enabled at 0xFFFC0000". RAM_HI=1111
# maps OCM0..3 contiguous high (UG585 Table 29-4); also enable SCU address filtering so the
# A9 routes DDR (0x100000..0xFFE00000) for U-Boot relocation. Verified on xc7z020: the
# helper then runs and gives an interactive `Zynq>` DCC console. ZynqMP differs — skip.
if {$ARCH eq "zynq"} {
    puts "== map OCM high to 0xFFFC0000 (OCM_CFG.RAM_HI=1111) + SCU addr filtering =="
    mwr -force 0xF8000008 0x0000DF0D                                  ;# SLCR unlock
    mwr -force 0xF8000910 0x0000000F                                  ;# OCM_CFG RAM_HI=1111
    mwr 0xF8F00040 0x00100000                                         ;# SCU filter start
    mwr 0xF8F00044 0xFFE00000                                         ;# SCU filter end
    mwr 0xF8F00000 [expr {[mrd -value 0xF8F00000] | 0x2}]             ;# SCU addr-filter enable
}

# --- load the DCC-console U-Boot and run it (dow sets PC to the ELF entry) ---
puts "== loading U-Boot: $UBOOT =="
dow $UBOOT
con

# --- optional: stage the payload into DRAM over JTAG. The cfgmem helper has NO
#     loadx/ymodem command (confirmed by strings), so transfer via the debugger — this
#     is exactly what program_flash does. DDR is up because ps7_init ran above. ---
if {[info exists ::env(ZB_IMAGE)]} {
    set stage [opt ZB_LOADADDR 0x01000000]
    puts "== staging $::env(ZB_IMAGE) -> DRAM @ $stage via 'dow -data' (JTAG) =="
    dow -data $::env(ZB_IMAGE) $stage
    puts "== payload at $stage. Flash it with:  sf probe 0 0 0 ; sf erase 0 <len_hex> ; sf write $stage 0 <len_hex>"
}

# --- open the DCC console over JTAG (no UART) for VISIBILITY into U-Boot's output.
#     Bring-up needs no typing; the sf sequence above is what flashes. If nothing prints
#     here, the cfgmem helper may speak only program_flash's framing — use a custom
#     CONFIG_ARM_DCC=y U-Boot instead (see the reference doc). ---
puts "== opening DCC console (jtagterminal) for U-Boot output visibility =="
jtagterminal -start
