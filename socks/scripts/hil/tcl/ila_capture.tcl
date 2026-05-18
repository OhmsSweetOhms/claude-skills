# ila_capture.tcl - ILA readback for HIL debug
#
# Modes:
#   Single:        vivado -mode batch -source ila_capture.tcl -tclargs <build_dir>
#   Multi:         vivado -mode batch -source ila_capture.tcl -tclargs <build_dir> --plan <plan.json>
#   Interactive:   vivado -mode tcl   -source ila_capture.tcl -tclargs <build_dir> --interactive
#   Capture-only:  vivado -mode batch -source ila_capture.tcl -tclargs <build_dir> --capture-only [--out <dir>] [--program <bit>]
#
# Capture-only mode attaches to an already-booted device (no programming
# unless --program is passed), loads the LTX, sets every probe on every
# ILA to don't-care, arms each ILA, and exports one CSV per ILA. Per-ILA
# failures are reported but do not abort the run -- subsequent ILAs are
# still captured. This is the path used when scope == system and no
# ila_trigger_plan.json is configured (firmware is already running, no
# breakpoint pacing is possible).
#
# Interactive mode reads commands from stdin after programming FPGA and
# discovering ILA. Commands:
#   ARM <probe> <value> <output_csv>   — set trigger, arm, wait, readback
#   QUIT                               — cleanup and exit
#
# Requires: board connected, DEBUG=true bitstream available (single/multi/
# interactive); board connected with a compatible bitstream already loaded
# (capture-only).

# Parse args: first positional arg is build_dir
set build_dir ""
set plan_file ""
set interactive 0
set capture_only 0
set capture_only_out ""
set capture_only_bit ""
for {set i 0} {$i < [llength $argv]} {incr i} {
    set arg [lindex $argv $i]
    if {$arg eq "--plan"} {
        incr i
        set plan_file [lindex $argv $i]
    } elseif {$arg eq "--interactive"} {
        set interactive 1
    } elseif {$arg eq "--capture-only"} {
        set capture_only 1
    } elseif {$arg eq "--out"} {
        incr i
        set capture_only_out [lindex $argv $i]
    } elseif {$arg eq "--program"} {
        incr i
        set capture_only_bit [lindex $argv $i]
    } elseif {$build_dir eq ""} {
        set build_dir $arg
    }
}
if {$build_dir eq ""} {
    error "Usage: ila_capture.tcl <build_dir> \[--plan <plan.json>\] \[--interactive\] \[--capture-only \[--out <dir>\] \[--program <bit>\]\]"
}

# --- JSON parser (minimal, handles our trigger plan format) ---
# Vivado TCL has no built-in JSON. Parse the captures list manually.
proc parse_trigger_plan {json_file} {
    set fp [open $json_file r]
    set data [read $fp]
    close $fp

    set captures {}
    # Match each capture block between { }
    set idx 0
    while {[regexp -start $idx {"name"\s*:\s*"([^"]+)"} $data -> name]} {
        set block_start [string first "\"name\"" $data $idx]
        # Find the enclosing { } for this capture
        set brace_start [string last "\{" $data $block_start]
        set brace_end [string first "\}" $data $block_start]
        set block [string range $data $brace_start $brace_end]

        set cap [dict create name $name]

        # Reject deprecated probe/value fields
        if {[regexp {"probe"\s*:} $block] || [regexp {"value"\s*:} $block]} {
            puts "ERROR: ila_trigger_plan.json uses deprecated probe/value fields. Update to trigger_probe/trigger_value/trigger_compare."
            exit 1
        }

        # Read signal-name-based fields
        if {[regexp {"trigger_probe"\s*:\s*"([^"]+)"} $block -> val]} {
            dict set cap trigger_probe $val
        }
        if {[regexp {"trigger_value"\s*:\s*"([^"]+)"} $block -> val]} {
            dict set cap trigger_value $val
        }
        if {[regexp {"trigger_compare"\s*:\s*"([^"]+)"} $block -> val]} {
            dict set cap trigger_compare $val
        }
        if {[regexp {"output"\s*:\s*"([^"]+)"} $block -> val]} {
            dict set cap output $val
        }
        if {[regexp {"description"\s*:\s*"([^"]+)"} $block -> val]} {
            dict set cap description $val
        }
        lappend captures $cap
        set idx [expr {$brace_end + 1}]
    }
    return $captures
}

# --- Connect to hardware ---
open_hw_manager
connect_hw_server -allow_non_jtag
open_hw_target

# Select FPGA device (Zynq-7000 by default; fall back to any device for
# Zynq UltraScale+ where xczu* names vary).
set device [get_hw_devices xc7z*]
if {$device eq ""} {
    set device [lindex [get_hw_devices] end]
}
current_hw_device $device
puts "=== Selected device: $device ==="

set ltx_path [file join $build_dir hil_top.ltx]

if {$capture_only} {
    # --- Capture-only mode: attach without programming (unless --program) ---
    # The firmware is assumed to already be running on the board. We only
    # load the LTX so the existing ILA cores can be discovered, set every
    # probe to don't-care, arm each ILA, and export one CSV per core.
    # Per-ILA failures do not abort the run.
    if {$capture_only_out eq ""} {
        set capture_only_out [file join $build_dir "ila-capture-only"]
    }
    file mkdir $capture_only_out

    if {[file exists $ltx_path]} {
        set_property PROBES.FILE $ltx_path $device
    } else {
        puts "WARN: hil_top.ltx not found at $ltx_path -- ILA probe names will be raw"
    }

    if {$capture_only_bit ne ""} {
        if {![file exists $capture_only_bit]} {
            error "Bitstream not found: $capture_only_bit"
        }
        set_property PROGRAM.FILE $capture_only_bit $device
        program_hw_devices $device
        puts "=== Device programmed: $capture_only_bit ==="
    } else {
        puts "=== Attaching without programming (capture-only) ==="
    }
    refresh_hw_device $device

    set ilas [get_hw_ilas]
    if {[llength $ilas] == 0} {
        puts "ERROR: No ILA cores discovered. Check that the loaded bitstream matches $ltx_path"
        close_hw_target
        disconnect_hw_server
        close_hw_manager
        exit 1
    }

    puts "=== ILAs discovered: [llength $ilas] ==="
    set capture_ok 0
    set capture_fail 0
    set idx 0
    foreach ila $ilas {
        set ila_name [get_property NAME $ila]
        set safe_name [string map {"/" "_" "\\" "_" ":" "_"} $ila_name]
        set csv_path [file join $capture_only_out "ila_${idx}_${safe_name}.csv"]
        puts "--- ILA $idx: $ila_name ---"

        # Set every probe to don't-care so arm triggers immediately
        if {[catch {
            foreach p [get_hw_probes -of_objects $ila] {
                set w [get_property WIDTH $p]
                if {$w == 1} {
                    set_property TRIGGER_COMPARE_VALUE "eq1'bX" $p
                } else {
                    set nibbles [expr {($w + 3) / 4}]
                    set_property TRIGGER_COMPARE_VALUE "eq${w}'h[string repeat X $nibbles]" $p
                }
            }
            set_property CONTROL.DATA_DEPTH 4096 $ila
            set_property CONTROL.TRIGGER_POSITION 512 $ila
        } setup_msg]} {
            puts "ILA_ERROR $ila setup: $setup_msg"
            incr capture_fail
            incr idx
            continue
        }

        if {[catch {run_hw_ila $ila} arm_msg]} {
            puts "ILA_ERROR $ila run_hw_ila: $arm_msg"
            incr capture_fail
            incr idx
            continue
        }

        set t0 [clock seconds]
        set done 0
        while {[expr {[clock seconds] - $t0}] < 15} {
            if {[catch {set status [get_property STATUS.CORE_STATUS $ila]} stat_msg]} {
                puts "ILA_ERROR $ila status: $stat_msg"
                break
            }
            if {$status eq "IDLE" || $status eq "FULL"} {
                set done 1
                break
            }
            after 100
        }

        if {!$done} {
            puts "ILA_TIMEOUT $ila"
            incr capture_fail
            incr idx
            continue
        }

        if {[catch {
            write_hw_ila_data -csv_file -force $csv_path [upload_hw_ila_data $ila]
        } write_msg]} {
            puts "ILA_ERROR $ila upload/write: $write_msg"
            incr capture_fail
            incr idx
            continue
        }
        puts "ILA_DONE $ila $csv_path"
        incr capture_ok
        incr idx
    }

    close_hw_target
    disconnect_hw_server
    close_hw_manager

    puts "=== Capture-only complete: $capture_ok captured, $capture_fail failed, out_dir=$capture_only_out ==="
    if {$capture_ok == 0} {
        exit 1
    }
    exit 0
}

# Find bitstream dynamically (project name varies per DUT)
set bit_path ""
foreach f [glob -nocomplain [file join $build_dir vivado_project *.runs/impl_1/hil_top.bit]] {
    set bit_path $f
    break
}
if {$bit_path eq ""} {
    puts "ERROR: No bitstream found in $build_dir/vivado_project/*/impl_1/"
    exit 1
}

set_property PROGRAM.FILE $bit_path $device
if {[file exists $ltx_path]} {
    set_property PROBES.FILE $ltx_path $device
}
program_hw_devices $device
refresh_hw_device $device
puts "=== Device programmed ==="

# Get ILA core
set ila [lindex [get_hw_ilas] 0]
if {$ila eq ""} {
    puts "ERROR: No ILA cores found"
    close_hw_target
    disconnect_hw_server
    close_hw_manager
    exit 1
}
puts "=== ILA: $ila ==="

# List probes
puts "=== Probes ==="
foreach p [get_hw_probes -of_objects $ila] {
    puts "  [get_property NAME $p]  width=[get_property WIDTH $p]"
}

# Common ILA settings
set_property CONTROL.DATA_DEPTH 4096 $ila
set_property CONTROL.TRIGGER_POSITION 512 $ila

# --- Helper: JTAG-to-AXI register dump (bypasses ARM core) ---
proc dump_axi_via_jtag {base_addr num_regs csv_path} {
    # Find the hw_axi core (jtag_axi IP discovered after FPGA programming)
    set axi_core [lindex [get_hw_axis] 0]
    if {$axi_core eq ""} {
        puts "ILA_ERROR no JTAG-to-AXI core found (add jtag_axi IP to block design)"
        return
    }

    set fp [open $csv_path w]
    puts $fp "offset,value"
    for {set i 0} {$i < $num_regs} {incr i} {
        set addr [format 0x%08X [expr {$base_addr + $i * 4}]]
        create_hw_axi_txn rd_txn $axi_core -address $addr \
            -len 1 -type READ -force
        run_hw_axi rd_txn
        set val [get_property DATA [get_hw_axi_txns rd_txn]]
        puts $fp "[format 0x%02X [expr {$i * 4}]],$val"
        delete_hw_axi_txn rd_txn
    }
    close $fp
    puts "DUMP_AXI_DONE $csv_path"
}

# --- Helper: arm ILA on a signal-name probe, wait, readback CSV ---
proc arm_and_capture {ila probe_name value compare csv_path {timeout 15}} {
    # Reset ALL probes to don't-care
    foreach p [get_hw_probes -of_objects $ila] {
        set w [get_property WIDTH $p]
        if {$w == 1} {
            set_property TRIGGER_COMPARE_VALUE "eq1'bX" $p
        } else {
            set dontcare "eq${w}'h[string repeat X [expr {($w+3)/4}]]"
            set_property TRIGGER_COMPARE_VALUE $dontcare $p
        }
    }

    # Signal-name-based probe lookup
    set probe [get_hw_probes */$probe_name -of_objects $ila -quiet]
    if {$probe eq ""} {
        set probe [get_hw_probes $probe_name -of_objects $ila -quiet]
    }
    if {$probe eq ""} {
        puts "ILA_ERROR probe '$probe_name' not found"
        return 0
    }

    # Auto-derive width from probe WIDTH property
    set w [get_property WIDTH $probe]

    # Build compare string: "${compare}${width}'b${value}"
    set trigger_str "${compare}${w}'b${value}"
    set_property TRIGGER_COMPARE_VALUE $trigger_str $probe

    # Arm and signal ready
    run_hw_ila $ila
    puts "ILA_ARMED"
    flush stdout
    set t0 [clock seconds]
    set triggered 0
    while {[expr {[clock seconds] - $t0}] < $timeout} {
        set status [get_property STATUS.CORE_STATUS $ila]
        if {$status eq "IDLE" || $status eq "FULL"} {
            set triggered 1
            break
        }
        after 100
    }

    if {!$triggered} {
        puts "ILA_TIMEOUT"
        return 0
    }

    # Upload and export
    write_hw_ila_data -csv_file -force $csv_path [upload_hw_ila_data $ila]
    puts "ILA_DONE $csv_path"
    return 1
}

if {$interactive} {
    # --- Interactive mode: read commands from stdin ---
    puts "ILA_READY"
    flush stdout

    while {1} {
        if {[gets stdin line] < 0} break
        set line [string trim $line]
        if {$line eq ""} continue

        set parts [split $line]
        set cmd [string toupper [lindex $parts 0]]

        if {$cmd eq "QUIT"} {
            puts "ILA_QUIT"
            flush stdout
            break
        } elseif {$cmd eq "DUMP_AXI"} {
            if {[llength $parts] < 4} {
                puts "ILA_ERROR usage: DUMP_AXI <base_addr> <num_regs> <csv_path>"
                flush stdout
                continue
            }
            set base [expr [lindex $parts 1]]
            set nregs [lindex $parts 2]
            set csv [lindex $parts 3]
            dump_axi_via_jtag $base $nregs $csv
            flush stdout
        } elseif {$cmd eq "ARM"} {
            if {[llength $parts] < 5} {
                puts "ILA_ERROR usage: ARM <probe> <value> <compare> <output_csv>"
                flush stdout
                continue
            }
            set probe_name [lindex $parts 1]
            set value [lindex $parts 2]
            set compare [lindex $parts 3]
            set csv_path [lindex $parts 4]
            arm_and_capture $ila $probe_name $value $compare $csv_path
            flush stdout
        } else {
            puts "ILA_ERROR unknown command: $cmd"
            flush stdout
        }
    }

} elseif {$plan_file eq ""} {
    # --- Single capture mode (immediate trigger) ---
    puts "=== Single capture (immediate trigger) ==="
    run_hw_ila $ila -trigger_now
    wait_on_hw_ila $ila

    set csv_path [file join $build_dir ila_capture.csv]
    write_hw_ila_data -csv_file -force $csv_path [upload_hw_ila_data $ila]
    puts "=== Exported: $csv_path ==="

} else {
    # --- Multi-capture mode (trigger plan) ---
    puts "=== Multi-capture from: $plan_file ==="
    set captures [parse_trigger_plan $plan_file]
    puts "  [llength $captures] captures defined"

    set cap_num 0
    foreach cap $captures {
        incr cap_num
        set name [dict get $cap name]
        set probe_name [dict get $cap trigger_probe]
        set value [dict get $cap trigger_value]
        set compare "eq"
        if {[dict exists $cap trigger_compare]} {
            set compare [dict get $cap trigger_compare]
        }
        set output [dict get $cap output]
        set desc ""
        if {[dict exists $cap description]} {
            set desc [dict get $cap description]
        }

        puts "\n--- Capture $cap_num: $name ---"
        if {$desc ne ""} { puts "  $desc" }
        puts "  Trigger: $probe_name $compare $value"

        set csv_path [file join $build_dir $output]
        set ok [arm_and_capture $ila $probe_name $value $compare $csv_path]
        if {$ok} {
            puts "  Captured: $csv_path"
        } else {
            puts "  FAILED"
        }
    }

    puts "\n=== Multi-capture complete: $cap_num captures ==="
}

# Cleanup
close_hw_target
disconnect_hw_server
close_hw_manager
