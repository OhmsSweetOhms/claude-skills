# flash_psu_no_os.tcl - Program ZynqMP PL and start A53 no-OS firmware
# Usage: xsdb flash_psu_no_os.tcl <bitstream> <firmware_elf> <psu_init_tcl> <fsbl_elf>

set bitstream [lindex $argv 0]
set firmware  [lindex $argv 1]
set psu_init  [lindex $argv 2]
set fsbl      [lindex $argv 3]

puts "Bitstream: $bitstream"
puts "Firmware:  $firmware"
puts "PSU init:  $psu_init"
puts "FSBL:      $fsbl"

connect
after 3000
puts "=== Targets after connect ==="
puts [targets]

puts "=== System reset ==="
catch {
    targets -set -filter {name =~ "APU*"}
    stop
    rst -system
} rst_result
puts "rst result: $rst_result"
after 5000

puts "=== Programming FPGA ==="
targets -set -filter {name =~ "PSU"}
fpga -file [file normalize $bitstream]
after 3000

puts "=== Initializing PSU for A53 no-OS ==="
if {$fsbl eq ""} {
    puts "ERROR: FSBL ELF is required for A53 no-OS programming"
    disconnect
    exit 1
}
targets -set -nocase -filter {name =~ "*Cortex-A53 #0*"}
rst -processor
dow [file normalize $fsbl]
set fsbl_bp [bpadd -addr &XFsbl_Exit]
con -block -timeout 60
bpremove $fsbl_bp

puts "=== Downloading firmware to Cortex-A53 #0 ==="
targets -set -filter {name =~ "*Cortex-A53 #0*"}
dow [file normalize $firmware]
after 1000

puts "=== Starting execution ==="
con
after 2000

puts "=== Done! A53 no-OS firmware running ==="
disconnect
exit
