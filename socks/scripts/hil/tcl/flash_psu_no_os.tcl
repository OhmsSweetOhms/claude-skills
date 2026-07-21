# flash_psu_no_os.tcl - Program ZynqMP PL and start A53 no-OS firmware
# Usage: xsdb flash_psu_no_os.tcl <bitstream> <firmware_elf> <psu_init_tcl> <fsbl_elf>
#
# Multi-cable safety: if HIL_JTAG_CABLE_FILTER is set in the environment
# (e.g. "*<cable-serial>*", the JTAG cable serial), every target selection is
# additionally constrained to that JTAG cable. With two boards on the
# hw_server the bare name filters (APU*, PSU, Cortex-A53 #0) can match
# the WRONG board -- a Zynq-7000 on a second cable exposes an "APU"
# target too, and the rst -system below would smash it. Unset = legacy
# single-cable behavior, byte-for-byte identical filters.

set bitstream [lindex $argv 0]
set firmware  [lindex $argv 1]
set psu_init  [lindex $argv 2]
set fsbl      [lindex $argv 3]

set cable_filter ""
if {[info exists ::env(HIL_JTAG_CABLE_FILTER)] && $::env(HIL_JTAG_CABLE_FILTER) ne ""} {
    set cable_filter $::env(HIL_JTAG_CABLE_FILTER)
}

proc guarded_filter {name_expr} {
    global cable_filter
    if {$cable_filter ne ""} {
        return "($name_expr) && jtag_cable_name =~ \"$cable_filter\""
    }
    return $name_expr
}

puts "Bitstream: $bitstream"
puts "Firmware:  $firmware"
puts "PSU init:  $psu_init"
puts "FSBL:      $fsbl"
if {$cable_filter ne ""} {
    puts "JTAG cable filter: $cable_filter"
}

connect
after 3000
puts "=== Targets after connect ==="
puts [targets]

puts "=== System reset ==="
catch {
    targets -set -filter [guarded_filter {name =~ "APU*"}]
    stop
    rst -system
} rst_result
puts "rst result: $rst_result"
after 5000

puts "=== Programming FPGA ==="
# Select the ZynqMP configuration TAP explicitly: with a second FPGA on
# another cable, `fpga` cannot infer the device ("Multiple FPGA devices
# found ... select one of" names the device TAPs, and for ZynqMP that is
# the PS TAP, not the PL child node). On single-cable setups this is the
# same device the old PSU-then-fpga sequence programmed implicitly.
if {[catch {targets -set -filter [guarded_filter {name =~ "PS TAP"}]} tap_sel_err]} {
    puts "PS TAP target select failed ($tap_sel_err); falling back to PSU"
    targets -set -filter [guarded_filter {name =~ "PSU"}]
}
fpga -file [file normalize $bitstream]
after 3000
targets -set -filter [guarded_filter {name =~ "PSU"}]

puts "=== Initializing PSU for A53 no-OS ==="
if {$fsbl eq ""} {
    puts "ERROR: FSBL ELF is required for A53 no-OS programming"
    disconnect
    exit 1
}
targets -set -nocase -filter [guarded_filter {name =~ "*Cortex-A53 #0*"}]
rst -processor
dow [file normalize $fsbl]
set fsbl_bp [bpadd -addr &XFsbl_Exit]
con -block -timeout 60
bpremove $fsbl_bp

puts "=== Downloading firmware to Cortex-A53 #0 ==="
targets -set -filter [guarded_filter {name =~ "*Cortex-A53 #0*"}]
dow [file normalize $firmware]
after 1000

puts "=== Starting execution ==="
con
after 2000

puts "=== Done! A53 no-OS firmware running ==="
disconnect
exit
