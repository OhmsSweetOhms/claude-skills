# flash_psu_load_only.tcl - Load and start a secondary firmware on a
# Zynq UltraScale+ core that has already been booted by an earlier
# flash_psu_no_os.tcl / flash_psu.tcl invocation in the same multi-
# firmware Stage 17 boot.
#
# Usage:
#   xsdb flash_psu_load_only.tcl <firmware_elf> <target_filter>
#
# <firmware_elf>   path to the ELF for this firmware role
# <target_filter>  XSDB `targets -filter` expression naming the core
#                  that should run the ELF, e.g.
#                    {name =~ "*Cortex-R5 #0*"}
#                    {name =~ "*Cortex-A53 #1*"}
#                  The expression is passed verbatim to `targets -set`.
#
# This script attaches to a board that already has PL programmed and
# at least one core running (typically A53 no-OS holding JESD up). It
# resets ONLY the targeted secondary core, downloads the ELF, and
# resumes it. The PL, PSU init, and any peer cores running on the
# board are left undisturbed.
#
# It is the multi-firmware sibling of flash_psu.tcl / flash_psu_no_os.tcl
# -- those bring up the board from cold reset and load the first
# firmware; this one loads each subsequent firmware role in turn. The
# Stage 17 wrapper (hil_run.py) picks this TCL automatically for
# entries 1..N in firmware.firmwares.

set firmware     [lindex $argv 0]
set target_filter [lindex $argv 1]

if {$firmware eq "" || $target_filter eq ""} {
    puts "ERROR: usage: flash_psu_load_only.tcl <firmware_elf> <target_filter>"
    exit 1
}

puts "Firmware:      $firmware"
puts "Target filter: $target_filter"

connect
after 2000
puts "=== Targets after connect ==="
puts [targets]

puts "=== Selecting target ==="
if {[catch {targets -set -nocase -filter $target_filter} tgt_result]} {
    puts "ERROR: targets -set failed: $tgt_result"
    disconnect
    exit 1
}
puts "Selected: [targets -filter $target_filter]"

puts "=== Resetting target processor (peer cores untouched) ==="
catch {rst -processor} rst_result
puts "rst result: $rst_result"
after 1000

catch {stop} stop_result
puts "stop result: $stop_result"

puts "=== Downloading firmware ==="
dow [file normalize $firmware]
after 1000

puts "=== Starting execution ==="
con
after 2000

puts "=== Done! Secondary firmware running ==="
disconnect
exit
