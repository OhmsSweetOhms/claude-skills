# boot_cpu.tcl - Boot CPU only (FPGA already programmed by Vivado)
# Usage: xsdb boot_cpu.tcl <firmware_elf> <ps7_init_tcl>
#
# Skips FPGA programming — Vivado hw_manager already did that and
# owns the JTAG connection for ILA. This script only initialises PS7,
# downloads firmware, and starts execution.

set firmware [lindex $argv 0]
set ps7_init [lindex $argv 1]

puts "Firmware: $firmware"
puts "PS7 init: $ps7_init"

connect
after 1000

# Select CPU #0
targets -set -nocase -filter {name =~ "*Cortex*#0" || name =~ "*ARM*#0"}
catch {stop}
after 500

# Initialize PS7 (clocks, DDR, MIO, UART)
source $ps7_init
ps7_init
ps7_post_config
after 1000

# Download and run firmware
dow $firmware
after 500
con
after 1000

puts "=== CPU booted ==="
disconnect
