# flash_psu.tcl - Program Zynq UltraScale+ with bitstream + bare-metal firmware
# Usage: xsdb flash_psu.tcl <bitstream> <firmware_elf> <psu_init_tcl>

set bitstream [lindex $argv 0]
set firmware  [lindex $argv 1]
set psu_init  [lindex $argv 2]

puts "Bitstream: $bitstream"
puts "Firmware:  $firmware"
puts "PSU init:  $psu_init"

connect
after 3000
puts "=== Targets after connect ==="
puts [targets]

set tgt_list [targets -filter {name =~ "xczu*"}]
if {$tgt_list eq ""} {
    puts "ERROR: No xczu device found"
    disconnect
    exit 1
}

puts "=== System reset ==="
catch {
    targets -set -nocase -filter {name =~ "APU*" || name =~ "DAP*" || name =~ "PSU*"}
    rst -system
} rst_result
puts "rst result: $rst_result"
after 5000

puts "=== Targets after reset ==="
puts [targets]

puts "=== Selecting ARM target #0 ==="
targets -set -nocase -filter {name =~ "*Cortex*#0" || name =~ "*ARM*#0"}
catch {stop}
after 500

puts "=== Initializing PSU ==="
source $psu_init
psu_init
psu_post_config
after 1000

puts "=== Programming FPGA ==="
targets -set -filter {name =~ "xczu*"}
fpga $bitstream
after 3000

puts "=== Downloading firmware ==="
targets -set -nocase -filter {name =~ "*Cortex*#0" || name =~ "*ARM*#0"}
dow $firmware
after 1000

puts "=== Starting execution ==="
con
after 2000

puts "=== Done! Firmware running ==="
disconnect
