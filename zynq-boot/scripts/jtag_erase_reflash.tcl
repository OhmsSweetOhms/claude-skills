# jtag_erase_reflash.tcl — one-shot, boot-mode-independent QSPI erase / reprogram for
# Zynq-7000, driven entirely over JTAG (no UART, no bash, no Python). Run with xsdb:
#
#     xsdb jtag_erase_reflash.tcl erase        --ps7 <ps7_init.tcl> [options]
#     xsdb jtag_erase_reflash.tcl erase+flash  <image.mcs> --ps7 <ps7_init.tcl> [options]
#
# Windows and Linux use the SAME invocation (xsdb on PATH after sourcing/settings the
# Xilinx toolchain); there is deliberately no shell wrapper in the critical path.
#
# WHY THIS EXISTS: program_flash regressed in 2020.x+ (AMD AR 76051) and fails outright on
# a board hardwired to QSPI/NAND boot mode. But the JTAG/DAP is always alive regardless of
# the strap pins, and ps7_init reconfigures the whole PS (clocks, DDR, MIO incl. QSPI pins)
# by direct register writes — it never reads the boot mode. So: halt the core AT the reset
# vector (halt-on-reset, AR 68065) before the BootROM can boot stale flash, re-init the PS,
# load Xilinx's DCC-console cfgmem U-Boot into OCM, and drive `sf erase`/`sf write` over the
# ARM DCC channel (U-Boot's drivers/serial/arm_dcc.c — the same transport program_flash
# uses). This evolves the skill's jtag_qspi_flash.tcl (which stops at staging and only
# PRINTS the sf commands) into full erase/program/verify automation.
#
# TECHNIQUE HW-VERIFIED on a hardwired-QSPI xc7z020 (thread zynq-boot/20260629-hardwired-
# qspi-jtag-flash, findings 2026-06-30 .. 2026-07-09). The proven vehicle is the workbench
# dashboard (backend/xsct_driver.py); this script ports its exact xsdb sequences to
# standalone TCL, including the two 2026-07-09 fixes:
#   * `rst -system -stop` halt-on-reset (NOT the racy `rst -system; stop`) — commit e2bf200
#   * `rst -dap` self-recovery when a FSBL-wedged A9 falls off the debug bus — commit b210080
#
# CREDITS (details inline at each step):
#   AMD AR 76051  — program_flash regression on QSPI/NAND-strapped boards (why this exists)
#   AMD AR 68065  — debugger halt-on-reset / vector catch (`rst -system -stop`)
#   UG585         — Zynq-7000 TRM: "Exit Sleep" PLL_PWRDWN sequence; OCM_CFG (Table 29-4);
#                   SCU address filtering
#   U-Boot drivers/serial/arm_dcc.c — the DCC-only console the cfgmem helper speaks
#   Blog: "ZynqBerry QSPI boot workaround (Vitis 2021.x)" — .research/session-20260629-
#   093358/blogs/zynqberry-qspi-boot-workaround-vitis-2021.md (independent confirmation of
#   the JTAG+U-Boot flash path when program_flash fails; and that cfgmem helpers are
#   version-temperamental — the 2021.1 zynq_qspi_x1_single is a known dud, see --uboot)
#
# MODES
#   erase        — full-chip `sf erase 0 <chip_size>`, then verify sampled offsets read 0xFF
#   erase+flash  — the above, then program the .mcs image span from offset 0 and verify.
#                  ("flash" is accepted as a shorthand.) The image SPAN (trailing 0xFF
#                  trimmed) is written, not a padded chip-size write: after the full-chip
#                  erase the untouched tail is already 0xFF, so the chip ends
#                  content-identical to a full write, minutes faster (plan-08 D2).
#
# ARGUMENTS
#   <mode>            erase | erase+flash   (required, first positional)
#   <image.mcs>       Intel-HEX image (bootgen -format MCS) — required for erase+flash.
#                     Parsed IN TCL (record types 00/01/04 + per-line checksum) to a raw
#                     .bin, because U-Boot `sf write` copies raw bytes from DRAM (plan-08 D1).
#   --ps7 <path>      ps7_init.tcl matching THIS board (extract from its .xsa). REQUIRED.
#                     An incompatible ps7_init can WEDGE the debug DAP/TAP (power-cycle to
#                     recover) — always use the board's own export.
#   --uboot <path>    DCC-console U-Boot ELF. Default: zynq_qspi_x1_single.bin carried NEXT
#                     TO THIS SCRIPT — the pinned Vitis 2022.2 build, the proven-good one.
#                     (The 2021.1 build of the same file aborts at env_init / -ENODEV and
#                     never reaches a prompt — a known dud. plan-08 D6.)
#   --url <url>       hw_server URL, e.g. tcp:localhost:3121. Default: none — plain
#                     `connect`, which auto-starts a local hw_server. If a Vivado GUI or
#                     another tool already owns the cable, point --url at ITS hw_server.
#   --chip-size <n>   Flash size in bytes (decimal or 0x-hex). Default: auto from `sf probe`.
#   --log <path>      Run log. Default: ./jtag_erase_reflash-<timestamp>.log
#   --help            Print usage and exit.
#
# JTAG has a SINGLE owner: close other xsdb/XSCT sessions and stop dashboard jobs before
# running this. Everything the script does is logged (command + result) to the run log.

# ---------------------------------------------------------------------------- usage / help
# Usage text lives in a proc so both --help and every arg-parse error print the same thing.
proc usage {} {
    puts {jtag_erase_reflash.tcl — boot-mode-independent QSPI erase / reprogram over JTAG (Zynq-7000)

USAGE
  xsdb jtag_erase_reflash.tcl erase        --ps7 <ps7_init.tcl> [options]
  xsdb jtag_erase_reflash.tcl erase+flash  <image.mcs> --ps7 <ps7_init.tcl> [options]

MODES
  erase          full-chip erase, then verify sampled offsets read back 0xFF
  erase+flash    full-chip erase, program the .mcs image span from offset 0, verify
                 readback ("flash" accepted as shorthand)

OPTIONS
  --ps7 <path>        ps7_init.tcl for THIS board (from its .xsa)  [REQUIRED]
  --uboot <path>      DCC-console U-Boot ELF (default: the 2022.2 zynq_qspi_x1_single.bin
                      carried next to this script — do NOT substitute the 2021.1 dud)
  --url <url>         hw_server URL (default: auto-start a local hw_server)
  --chip-size <n>     flash size in bytes, decimal or 0x-hex (default: from sf probe)
  --log <path>        run log (default: ./jtag_erase_reflash-<timestamp>.log)
  --help              this text

EXAMPLES
  Linux:    source /tools/Xilinx/Vitis/2022.2/settings64.sh
            xsdb jtag_erase_reflash.tcl erase+flash boot.mcs --ps7 ps7_init.tcl
  Windows:  C:\Xilinx\Vitis\2022.2\settings64.bat
            xsdb jtag_erase_reflash.tcl erase+flash boot.mcs --ps7 ps7_init.tcl

DESTRUCTIVE: both modes wipe the entire QSPI flash. JTAG has a single owner — close other
xsdb/Vivado hw sessions first, or point --url at the session that owns the cable.}
}

# ------------------------------------------------------------------------------ logging
# One run log per invocation, timestamped so successive runs never clobber each other
# (requirement 10). Every message — and every xsdb command + its result — goes through
# logputs, which tees to the console AND the file, so a failed bench run is replayable
# from the log alone.
set ::LOGFH ""
proc log_open {path} {
    # Normalize for a Windows-safe absolute path in the banner (requirement 2).
    set path [file normalize $path]
    set ::LOGFH [open $path a]
    fconfigure $::LOGFH -buffering line   ;# line-buffered: a crash still leaves the log usable
    set ::LOGPATH $path
    logputs "== jtag_erase_reflash run log — [clock format [clock seconds]] =="
}
proc logputs {msg} {
    puts $msg                              ;# console copy (operator watches this live)
    if {$::LOGFH ne ""} { puts $::LOGFH $msg }   ;# file copy (post-mortem / CI artifact)
}

# die: single exit path for every failure — logs the error, logs the actionable HINT
# (requirement 11: no bare stack traces), closes the log, and exits non-zero so CI notices.
proc die {msg {hint ""}} {
    logputs "ERROR: $msg"
    if {$hint ne ""} { logputs "HINT:  $hint" }
    if {$::LOGFH ne ""} {
        logputs "== run FAILED — log: $::LOGPATH =="
        close $::LOGFH
    }
    exit 1
}

# run: execute one xsdb/TCL command in the CALLER's scope, logging the command and its
# result; on error, die with the given hint. This is how every fallible bench step gets
# both a transcript line and a mapped hint (requirements 10 + 11).
proc run {cmd {hint ""}} {
    logputs "+ $cmd"
    if {[catch {uplevel 1 $cmd} result]} {
        die "command failed: $cmd\n$result" $hint
    }
    if {$result ne ""} { logputs $result }
    return $result
}

# ---------------------------------------------------------------------------- arg parsing
# Pure-TCL argv parse (requirement 2: no bash/env resolution — Windows-friendly). Positional
# args first (mode, then the .mcs for erase+flash), then --flag value pairs.
if {[lsearch -exact $argv --help] >= 0 || [llength $argv] == 0} { usage; exit 0 }

set MODE ""            ;# erase | erase+flash
set MCS  ""            ;# Intel-HEX image path (erase+flash only)
set PS7  ""            ;# ps7_init.tcl path (required)
# Default helper: the pinned 2022.2 cfgmem U-Boot carried NEXT TO THIS SCRIPT (plan-08 D6:
# self-contained; the 2021.1 build is a known dud). [info script] is this file's own path.
set UBOOT [file join [file dirname [file normalize [info script]]] zynq_qspi_x1_single.bin]
set URL  ""            ;# empty → plain `connect` (auto-starts a local hw_server)
set CHIPSIZE ""        ;# empty → auto-detect from `sf probe` output
set LOG  [file join [pwd] "jtag_erase_reflash-[clock format [clock seconds] -format %Y%m%d-%H%M%S].log"]

set positionals {}
for {set i 0} {$i < [llength $argv]} {incr i} {
    set a [lindex $argv $i]
    switch -exact -- $a {
        --ps7 - --uboot - --url - --chip-size - --log {
            # Every option takes exactly one value; a trailing bare flag is an error.
            incr i
            if {$i >= [llength $argv]} { usage; puts stderr "\nERROR: $a needs a value"; exit 2 }
            set v [lindex $argv $i]
            switch -exact -- $a {
                --ps7       { set PS7      $v }
                --uboot     { set UBOOT    $v }
                --url       { set URL      $v }
                --chip-size { set CHIPSIZE $v }
                --log       { set LOG      $v }
            }
        }
        default {
            # Unknown flags are errors (typo protection); bare words are positionals.
            if {[string match --* $a]} { usage; puts stderr "\nERROR: unknown option $a"; exit 2 }
            lappend positionals $a
        }
    }
}

# Positional 1: mode. "flash" is accepted as shorthand for erase+flash (requirement 12).
set MODE [lindex $positionals 0]
if {$MODE eq "flash"} { set MODE "erase+flash" }
if {$MODE ni {erase erase+flash}} {
    usage; puts stderr "\nERROR: mode must be 'erase' or 'erase+flash' (got '$MODE')"; exit 2
}
# Positional 2: the .mcs — required by erase+flash, meaningless for erase.
set MCS [lindex $positionals 1]
if {$MODE eq "erase+flash" && $MCS eq ""} {
    usage; puts stderr "\nERROR: erase+flash needs an <image.mcs> argument"; exit 2
}
if {$MODE eq "erase" && $MCS ne ""} {
    usage; puts stderr "\nERROR: mode 'erase' takes no image (did you mean erase+flash?)"; exit 2
}
if {[llength $positionals] > 2} {
    usage; puts stderr "\nERROR: unexpected extra arguments: [lrange $positionals 2 end]"; exit 2
}

# Required/existing-file checks up front — fail BEFORE touching the board (requirement 11).
if {$PS7 eq ""} { usage; puts stderr "\nERROR: --ps7 <ps7_init.tcl> is required"; exit 2 }
# file normalize: absolute, forward-slash, symlink-resolved — same path semantics on
# Windows and Linux (requirement 2). xsdb does NOT expand ~ or shell vars, so we don't either.
set PS7   [file normalize $PS7]
set UBOOT [file normalize $UBOOT]
if {$MCS ne ""} { set MCS [file normalize $MCS] }
foreach {what f} [list ps7_init $PS7 u-boot-helper $UBOOT image $MCS] {
    if {$f ne "" && ![file isfile $f]} {
        puts stderr "ERROR: $what file not found: $f"
        if {$what eq "u-boot-helper"} {
            puts stderr "HINT:  the default helper is the 2022.2 zynq_qspi_x1_single.bin carried next to this script;\n       copy it there or pass --uboot <path> (do NOT use the 2021.1 build — known dud)"
        }
        exit 2
    }
}
# --chip-size sanity: accept decimal or 0x-hex, reject garbage before it reaches sf erase.
if {$CHIPSIZE ne ""} {
    if {[catch {set CHIPSIZE [expr {$CHIPSIZE + 0}]}] || $CHIPSIZE <= 0} {
        puts stderr "ERROR: --chip-size must be a positive byte count (decimal or 0x-hex)"; exit 2
    }
}

# ------------------------------------------------------------------------- run banner
log_open $LOG
logputs "mode:      $MODE"
logputs "image:     [expr {$MCS ne "" ? $MCS : "(none — erase only)"}]"
logputs "ps7_init:  $PS7"
logputs "u-boot:    $UBOOT"
logputs "hw_server: [expr {$URL ne "" ? $URL : "(auto-start local)"}]"
logputs "chip size: [expr {$CHIPSIZE ne "" ? $CHIPSIZE : "(auto from sf probe)"}]"
logputs "log:       $LOG"

# ------------------------------------------------------------------------ mode dispatch
# Step 1+ fills these in: bring-up, staging, erase, program, verify.
die "not implemented yet: mode '$MODE' (plan-08 Step 1+)"
