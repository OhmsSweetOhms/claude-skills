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

# --------------------------------------------------------------- event-loop-safe sleep
# The jtagterminal DCC<->TCP bridge is pumped by xsdb's OWN Tcl event loop. A plain
# `after <ms>` (or a blocking socket read) would freeze that loop and starve the bridge —
# no DCC bytes would ever reach our socket. So every wait goes through vwait, which keeps
# the event loop running while we sleep. (This differs from the dashboard, whose socket
# client is a separate Python process; in-process is the price of a single-file tool.)
set ::wake_seq 0
proc sleep_ms {ms} {
    set var ::wake[incr ::wake_seq]        ;# unique var per sleep: nested vwaits can't collide
    after $ms [list set $var 1]            ;# schedule the wake-up event
    vwait $var                             ;# process events (incl. the DCC bridge) until it fires
    unset $var
}

# ------------------------------------------------------------------ target selection
# Select the Zynq-7000's A9 core 0 — the UNIQUE name on a multi-device JTAG chain (an
# "APU"/"DAP" filter would also match a co-resident ZynqMP and error out), and using it as
# the `rst -system` target scopes the reset to THIS board. Ported from the dashboard's
# _select_a9 (commit b210080), including the self-recovery:
#
# When the board's QSPI FSBL has run (a board that sat powered on its own image), it can
# leave the A9's debug AHB-AP wedged (DAP status 0x30000021 "AHB AP transaction error");
# the A9 falls off the debug bus and `targets -set` fails "no targets found" (only the DAP
# + jtag device remain). `rst -system` can't clear that — it's a soft SLCR reset, not a
# debug-logic reset. `rst -dap` (Arm DAP reset) does. The DAP filter is ambiguous on a
# multi-device chain, so if `targets -set` reports >1 match we surface a power-cycle hint
# rather than reset the WRONG board's DAP.
proc select_a9 {} {
    # Fast path: the A9 is on the bus (healthy board / already-recovered DAP).
    if {![catch {targets -set -nocase -filter {name =~ "*Cortex-A9*#0"}} err]} { return }
    if {[string first "no targets found" [string tolower $err]] < 0} {
        # Some OTHER selection failure (chain unreadable, cable gone) — not the wedge.
        die "cannot select the Cortex-A9 target: $err" \
            "board off/unplugged, wrong cable, or another tool owns the JTAG — check the cable and close other xsdb/Vivado hardware sessions (or pass --url of the session that owns it)"
    }
    logputs "A9 off the debug bus (FSBL-wedged DAP?) — attempting rst -dap self-recovery"
    # rst -dap needs a DAP-type target (the jtag-device 'xc7z020' target answers
    # "Invalid reset type"). If the DAP name matches more than one device, targets -set
    # errors — deep-wedged + ambiguous means only a power-cycle is safe.
    if {[catch {targets -set -nocase -filter {name =~ "*DAP*"}} err2]} {
        die "A9 debug target is wedged and the DAP filter is ambiguous/unavailable: $err2" \
            "power-cycle the board, then re-run (a FSBL-wedged debug AP cannot be cleared over JTAG on a multi-device chain)"
    }
    if {[catch {rst -dap} err3]} {
        die "rst -dap failed: $err3" "power-cycle the board, then re-run"
    }
    sleep_ms 500                            ;# give the DAP a moment to re-enumerate targets
    # Retry once after the DAP reset; if the A9 is still absent, only power fixes it.
    if {[catch {targets -set -nocase -filter {name =~ "*Cortex-A9*#0"}} err4]} {
        die "A9 still absent after rst -dap: $err4" \
            "power-cycle the board, then re-run (deep wedge — DAP status 0x30000021 class)"
    }
    logputs "rst -dap recovery OK — A9 back on the debug bus"
}

# ------------------------------------------------------------------------- PS bring-up
# The HW-proven boot-mode-independent bring-up (thread findings 2026-06-30 .. 07-09; the
# dashboard's bringup()/_ps_up_dow_uboot is the proven vehicle). Leaves the DCC-console
# U-Boot RUNNING with DDR up and the QSPI controller initialized.
proc bringup {} {
    global PS7 UBOOT
    # Discard any stale DCC terminal FIRST — before rst/dow/con. The ARM DCC is a single
    # 1-word channel: a leftover bridge (e.g. from a prior failed run) holds buffered
    # host->target bytes that would be fed into the NEXT U-Boot at `con`, corrupting its
    # first probe (this exactly broke recovery after the 2021.1 dud helper).
    logputs "+ jtagterminal -stop (discard any stale DCC bridge)"
    catch {jtagterminal -stop}

    # --- THE CRUX: halt-on-reset (AR 76051 + AR 68065) ---
    # `rst -system -stop` makes the debugger suspend the cores AS PART OF the reset
    # (vector catch), pinning the A9 at the reset vector BEFORE the BootROM can boot stale
    # QSPI. The old form — `rst -system` then a separate `stop` — RESUMES the cores by
    # default and races the FSBL; on a board whose flash holds a valid image the FSBL wins,
    # reconfigures the PS, and ps7_init then dies with "AP transaction timeout" @0xE0001034
    # (commit e2bf200; do NOT regress this).
    run {rst -system -stop} \
        "reset failed — if the error mentions 'Cannot reset APU'/'PLL lock', this ps7_init/XSA does not match the board (use the board's own .xsa export); otherwise power-cycle and re-run"
    select_a9                               ;# reset can re-enumerate; re-pin the A9
    catch {stop}                            ;# belt-and-braces; "already stopped" is fine

    # --- power up the DDR/IO PLLs BEFORE ps7_init (UG585 "Exit Sleep") ---
    # Some boot states leave slcr.{DDR,IO}_PLL_CTRL[PLL_PWRDWN]=1. Stock ps7_init only
    # writes the FDIV/BYPASS/RESET fields and NEVER clears PWRDWN — so it pulses RESET on
    # a powered-down PLL that can never lock: PLL_STATUS sticks at 0x39 (ARM locks, DDR/IO
    # don't) and DDR never comes up. Clear PWRDWN first (verified: 0x39 -> 0x3F, DDRC up).
    run {mwr -force 0xF8000008 0x0000DF0D} ;# SLCR unlock (write key, UG585)
    run {mwr -force 0xF8000104 [expr {[mrd -value 0xF8000104] & ~0x2}]} ;# DDR_PLL_CTRL[PLL_PWRDWN]=0
    run {mwr -force 0xF8000108 [expr {[mrd -value 0xF8000108] & ~0x2}]} ;# IO_PLL_CTRL [PLL_PWRDWN]=0

    # --- PS init: direct register writes from the board-matching ps7_init.tcl ---
    # Reconfigures clocks, DDR, and MIO (incl. the QSPI pins) without ever reading the
    # boot-mode straps — this is what makes the whole flow boot-mode-independent.
    logputs "+ source ps7_init.tcl; ps7_init"
    if {[catch {
        uplevel #0 [list source $PS7]       ;# defines ps7_init (+ post_config) globally
        ps7_init
    } err]} {
        die "ps7_init failed: $err" \
            "'AP transaction timeout @0xE0001034' means a FSBL seized the PS before the halt (should not happen after rst -system -stop; power-cycle if the board is deep-wedged). 'Cannot reset APU'/'PLL lock' means this ps7_init/.xsa is for a DIFFERENT board — use the board's own export."
    }
    catch {ps7_post_config}                 ;# PL/EMIO setup if the export defines it

    # --- drop the stale APU MMU so debugger DRAM access works ---
    # A halted boot image usually leaves the MMU enabled; debugger writes to DRAM (the
    # `dow -data` staging below) would fault "MMU section translation fault" even with DDR
    # up. `rst -processor` resets ONLY the core (PC->0, MMU off); PS/PLLs/DDR survive.
    run {rst -processor} "processor reset failed — power-cycle and re-run"
    catch {stop}                            ;# rst -processor leaves it halted; ignore "already"

    # --- map OCM high so the cfgmem helper can load at its link address ---
    # The helper is linked/entered at 0xFFFC0000 (high OCM). slcr.OCM_CFG[RAM_HI] resets
    # to 0 -> all four 64K OCM blocks map LOW, 0xFFFC0000 is unbacked, and `dow` fails
    # "OCM is not enabled at 0xFFFC0000". RAM_HI=1111 maps OCM0..3 contiguous high (UG585
    # Table 29-4). Also enable SCU address filtering so the A9 routes 0x00100000..
    # 0xFFE00000 to DDR — U-Boot relocates itself into DRAM and our staging lives there.
    run {mwr -force 0xF8000008 0x0000DF0D}  ;# re-unlock SLCR (ps7_init locks it on exit)
    run {mwr -force 0xF8000910 0x0000000F}  ;# OCM_CFG RAM_HI=1111: OCM0..3 -> 0xFFFC0000+
    run {mwr 0xF8F00040 0x00100000}         ;# SCU filter start (DDR window base)
    run {mwr 0xF8F00044 0xFFE00000}         ;# SCU filter end
    run {mwr 0xF8F00000 [expr {[mrd -value 0xF8F00000] | 0x2}]} ;# SCU addr-filter enable

    # --- load + run the DCC-console U-Boot (dow sets PC to the ELF entry) ---
    # The cfgmem helper has NO UART driver — only drivers/serial/arm_dcc.c. Its console
    # rides the same JTAG cable we're on; jtagterminal bridges it to a TCP socket below.
    logputs "+ dow (load U-Boot helper into OCM)"
    if {[catch {dow $UBOOT} err]} {
        die "loading U-Boot failed: $err" \
            "'OCM is not enabled at 0xFFFC0000' means the OCM-high remap didn't take — check the SLCR unlock + OCM_CFG writes above (did ps7_init re-lock the SLCR?)"
    }
    run {con} "resume (con) failed — power-cycle and re-run"
    logputs "U-Boot helper running — bring-up complete"
}

# --------------------------------------------------------------------- DCC console I/O
# Drive U-Boot over the jtagterminal TCP bridge. All reads are NON-BLOCKING + sleep_ms
# (see above: a blocking read would starve the very event loop that feeds the socket).
set ::DCC ""                               ;# the bridge socket, once open

# Read whatever bytes are pending right now (may be ""). EOF means the bridge died.
proc dcc_avail {} {
    set d [read $::DCC]
    if {[eof $::DCC]} { die "DCC bridge socket closed unexpectedly" \
        "the jtagterminal bridge died — re-run; if it repeats, power-cycle the board" }
    return $d
}

# Read until the console has been SILENT for `quiet` ms (drain pending output).
proc dcc_drain {{quiet 500}} {
    set buf ""
    set last [clock milliseconds]
    while {[clock milliseconds] - $last < $quiet} {
        set d [dcc_avail]
        if {[string length $d]} { append buf $d; set last [clock milliseconds] }
        sleep_ms 50
    }
    return $buf
}

# Read until the console is QUIESCENT at a prompt: "Zynq> " seen AND no bytes for
# `settle` ms, bounded by `overall`. This U-Boot flushes its pre-console buffer
# asynchronously, so the banner (and even a stray prompt) can arrive out of order while
# output still streams; probing mid-flush desyncs the whole session (dashboard lesson:
# a fixed delay broke ~50% of bring-ups). A dud helper never prompts — bail early.
proc dcc_sync_to_prompt {{settle 900} {overall 15000}} {
    set buf ""
    set deadline [expr {[clock milliseconds] + $overall}]
    set last [clock milliseconds]
    while {[clock milliseconds] < $deadline} {
        set d [dcc_avail]
        if {[string length $d]} { append buf $d; set last [clock milliseconds] }
        # A helper that aborted during init says so and will never prompt — stop waiting.
        if {[string first "Please RESET the board" $buf] >= 0} { break }
        if {[string first "Zynq> " $buf] >= 0
            && [clock milliseconds] - $last >= $settle} { break }
        sleep_ms 50
    }
    return $buf
}

# Send one U-Boot command and read until the next "Zynq> " prompt (or timeout). U-Boot
# echoes the command itself, so we never locally echo it (that garbles the transcript).
proc dcc_drive {cmd {timeout 60000}} {
    dcc_drain 300                           ;# clear any stragglers from the previous command
    puts -nonewline $::DCC "$cmd\r"         ;# U-Boot wants CR line endings
    flush $::DCC
    set buf ""
    set deadline [expr {[clock milliseconds] + $timeout}]
    while {[clock milliseconds] < $deadline} {
        set d [dcc_avail]
        if {[string length $d]} { append buf $d }
        if {[string first "Zynq> " $buf] >= 0} { break }
        sleep_ms 50
    }
    return $buf
}

# Classify accumulated U-Boot console text (dashboard's _classify_uboot, proven ordering):
# dud first (an aborted helper never prints 'Detected' anyway), then detection. Callers
# must classify ALL text seen since the session opened, not just the latest response —
# ghost bytes from a prior failed session can make the DETECTION fire in the boot banner
# (single-word DCC FIFO), leaving the explicit probe a silent no-op.
proc classify_uboot {text} {
    foreach marker {"Please RESET the board" "initcall sequence"} {
        if {[string first $marker $text] >= 0} { return "dud" }
    }
    if {[string first "Detected" $text] >= 0} { return "detected" }
    return "no-detect"
}

# Parse the flash size from sf probe output "total N MiB/KiB/Bytes".
proc parse_chip_size {text} {
    if {[regexp {total\s+(\d+)\s+(MiB|KiB|Bytes)} $text -> n unit]} {
        switch -exact -- $unit {
            MiB   { return [expr {$n * 1024 * 1024}] }
            KiB   { return [expr {$n * 1024}] }
            Bytes { return $n }
        }
    }
    return 0
}

# Open the DCC bridge socket and sync U-Boot to a usable prompt; then sf probe (with the
# proven retry + whole-transcript classification). Sets ::DCC and returns the chip size.
proc open_uboot_console {} {
    global CHIPSIZE
    # Exactly ONE terminal may own the DCC (single 1-word channel — two bridges steal
    # each other's bytes and ~50% of bring-ups scramble). Stop any leftover, then open ours.
    logputs "+ jtagterminal -stop; jtagterminal -socket (bridge DCC to a local TCP port)"
    catch {jtagterminal -stop}
    if {[catch {set port [string trim [jtagterminal -socket]]} err]} {
        die "jtagterminal -socket failed: $err" \
            "no DCC bridge — is the A9 target still selected and U-Boot running? Re-run; power-cycle if it repeats"
    }
    set ::DCC [socket 127.0.0.1 $port]
    # Binary + non-blocking + unbuffered: raw console bytes, and reads that never stall
    # the event loop pumping the bridge (see sleep_ms).
    fconfigure $::DCC -translation binary -blocking 0 -buffering none
    logputs "DCC console bridged on 127.0.0.1:$port"

    # Wait for the banner to finish and the prompt to appear (quiescence, not a fixed delay).
    set banner [dcc_sync_to_prompt]
    logputs "--- U-Boot banner ---\n[string trim $banner]\n---------------------"
    if {[classify_uboot $banner] eq "dud"} {
        die "U-Boot helper aborted during init (dud cfgmem build — env_init/-ENODEV class)" \
            "use the pinned Vitis 2022.2 zynq_qspi_x1_single.bin (the 2021.1 build is a known dud); pass it with --uboot or copy it next to this script"
    }

    # sf probe — retry loop (flaky DCC links drop lines). Classify against EVERYTHING seen
    # since the socket opened, not the latest attempt (see classify_uboot).
    set seen $banner
    for {set attempt 1} {$attempt <= 4} {incr attempt} {
        set resp [dcc_drive "sf probe 0 0 0" 20000]
        append seen $resp
        if {[classify_uboot $seen] eq "detected"} {
            # Log the device line ("SF: Detected n25q128a11 ...") — it names the part and
            # the sizes, which is what a post-mortem reader wants from this step.
            logputs "sf probe attempt $attempt/4: DETECTED — [string trim $resp]"
            break
        }
        logputs "sf probe attempt $attempt/4: no-detect — [string trim $resp]"
        sleep_ms 500
    }
    if {[classify_uboot $seen] ne "detected"} {
        die "sf probe failed after 4 tries" \
            "no flash detected: wrong QSPI MIO in this ps7_init, a dud cfgmem helper, or a flaky DCC link (reseat the JTAG cable). Use the board's own .xsa and the pinned 2022.2 helper."
    }

    # Chip size: explicit --chip-size wins; else parse the probe/banner text.
    if {$CHIPSIZE ne ""} {
        set chip $CHIPSIZE
        logputs "chip size: $chip bytes (from --chip-size)"
    } else {
        set chip [parse_chip_size $seen]
        if {$chip == 0} {
            die "could not parse the flash size from sf probe output" \
                "pass --chip-size <bytes> explicitly (e.g. --chip-size 0x1000000 for 16 MiB)"
        }
        logputs "chip size: $chip bytes ([expr {$chip / 1048576}] MiB, from sf probe)"
    }
    return $chip
}

# ------------------------------------------------------------------------ mode dispatch
# Connect to the hw_server. With --url we join an existing server (e.g. a Vivado GUI's);
# without, plain `connect` auto-starts a local one — no tool paths needed (requirement 3).
if {$URL ne ""} {
    run [list connect -url $URL] \
        "cannot reach hw_server at the given --url — is it running? (check the Vivado/xsdb session that owns the cable, or drop --url to auto-start a local server)"
} else {
    run {connect} \
        "cannot start/reach a local hw_server — is the JTAG cable plugged in and the toolchain sourced?"
}
select_a9                                   ;# pin the A9 (with rst -dap self-recovery)
bringup                                     ;# proven PS bring-up; leaves U-Boot running
set CHIP [open_uboot_console]               ;# DCC bridge + sf probe; flash size in bytes

# Step 2+ fills these in: staging, erase, program, verify.
die "not implemented yet: mode '$MODE' beyond bring-up (plan-08 Step 2+)"
