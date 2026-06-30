# jtag_qspi_flash.tcl — boot-mode-independent QSPI flash bring-up, driven entirely
# over JTAG (no UART). Run via xsct; see scripts/jtag_qspi_flash.sh for the wrapper
# that resolves paths and sources the toolchain.
#
# STATUS: authored, NOT yet hardware-verified. The reset/halt timing vs the BootROM
# (ZB_* below) and whether the cfgmem helper presents an interactive DCC prompt are
# the two spots to confirm on a real bring-up — see references/jtag-flash-bootmode-
# independent.md.
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
