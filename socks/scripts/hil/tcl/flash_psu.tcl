# flash_psu.tcl - Program Zynq UltraScale+ with bitstream + bare-metal firmware
# Usage: xsdb flash_psu.tcl <bitstream> <firmware_elf> <psu_init_tcl> [pmufw_elf fsbl_elf]

set bitstream [lindex $argv 0]
set firmware  [lindex $argv 1]
set psu_init  [lindex $argv 2]
set pmufw     [lindex $argv 3]
set fsbl      [lindex $argv 4]

puts "Bitstream: $bitstream"
puts "Firmware:  $firmware"
puts "PSU init:  $psu_init"
if {$pmufw ne ""} {
    puts "PMUFW:     $pmufw"
}
if {$fsbl ne ""} {
    puts "FSBL:      $fsbl"
}

connect
after 3000
puts "=== Targets after connect ==="
puts [targets]

set tgt_list [targets -filter {name =~ "xczu*" || name =~ "PL"}]
if {$tgt_list eq ""} {
    puts "ERROR: No ZynqMP PL target found"
    disconnect
    exit 1
}

puts "=== System reset ==="
catch {
    targets -set -nocase -filter {name =~ "PSU"}
    rst -system
} rst_result
puts "rst result: $rst_result"
after 5000

puts "=== Targets after reset ==="
puts [targets]

if {$pmufw ne "" && $fsbl ne ""} {
    puts "=== Enabling ZynqMP JTAG PMU access ==="
    targets -set -filter {name =~ "PSU"}
    mwr 0xffca0038 0x1ff
    after 500
    puts "=== Targets after security gate disable ==="
    puts [targets]

    puts "=== Starting PMUFW ==="
    targets -set -filter {name =~ "MicroBlaze PMU*"}
    dow $pmufw
    con
    after 500

    puts "=== Running FSBL on Cortex-A53 #0 ==="
    targets -set -nocase -filter {name =~ "Cortex-A53 #0*"}
    rst -processor
    dow $fsbl
    con
    after 5000
    catch {stop} fsbl_stop
    puts "fsbl stop result: $fsbl_stop"
} else {
    puts "=== Initializing PSU ==="
    targets -set -nocase -filter {name =~ "PSU"}
    source $psu_init
    psu_init
    psu_post_config
    after 1000
}

puts "=== Programming FPGA ==="
targets -set -filter {name =~ "xczu*" || name =~ "PL"}
fpga $bitstream
after 3000

puts "=== Releasing PS-PL isolation/reset ==="
targets -set -filter {name =~ "PSU"}
source $psu_init
catch {psu_ps_pl_isolation_removal} iso_result
puts "ps-pl isolation result: $iso_result"
catch {psu_ps_pl_reset_config} reset_result
puts "ps-pl reset result: $reset_result"
after 1000

puts "=== Downloading firmware ==="
targets -set -nocase -filter {name =~ "Cortex-R5 #0*"}
catch {rst -processor} r5_rst
puts "r5 rst result: $r5_rst"
after 1000
catch {stop} r5_stop
puts "r5 stop result: $r5_stop"
dow $firmware
after 1000

puts "=== Starting execution ==="
con
after 2000

puts "=== Done! Firmware running ==="
disconnect
