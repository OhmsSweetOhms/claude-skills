# ila_capture.tcl - ILA readback for HIL debug
#
# Modes:
#   Single:       vivado -mode batch -source ila_capture.tcl -tclargs <build_dir>
#   Multi:        vivado -mode batch -source ila_capture.tcl -tclargs <build_dir> --plan <plan.json>
#   Interactive:  vivado -mode tcl   -source ila_capture.tcl -tclargs <build_dir> --interactive
#
# Interactive mode reads commands from stdin after programming FPGA and
# discovering ILA. Commands:
#   ARM <probe> <value> <output_csv>   — set trigger, arm, wait, readback
#   QUIT                               — cleanup and exit
#
# Requires: board connected, DEBUG=true bitstream available

# Parse args: first positional arg is build_dir
set build_dir ""
set plan_file ""
set interactive 0
for {set i 0} {$i < [llength $argv]} {incr i} {
    set arg [lindex $argv $i]
    if {$arg eq "--plan"} {
        incr i
        set plan_file [lindex $argv $i]
    } elseif {$arg eq "--interactive"} {
        set interactive 1
    } elseif {$build_dir eq ""} {
        set build_dir $arg
    }
}
if {$build_dir eq ""} {
    error "Usage: ila_capture.tcl <build_dir> [--plan <plan.json>] [--interactive]"
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
        if {[regexp {"probe"\s*:\s*"([^"]+)"} $block -> val]} {
            dict set cap probe $val
        }
        if {[regexp {"operator"\s*:\s*"([^"]+)"} $block -> val]} {
            dict set cap operator $val
        }
        if {[regexp {"value"\s*:\s*"([^"]+)"} $block -> val]} {
            dict set cap value $val
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

# Select FPGA device
set device [get_hw_devices xc7z*]
if {$device eq ""} {
    set device [lindex [get_hw_devices] end]
}
current_hw_device $device
puts "=== Selected device: $device ==="

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
set ltx_path [file join $build_dir hil_top.ltx]

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

# --- Helper: arm ILA on a probe/value, wait, readback CSV ---
proc arm_and_capture {ila probe_name value csv_path {timeout 15}} {
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

    # Find the probe object
    set probe [get_hw_probes $probe_name -of_objects $ila -quiet]
    if {$probe eq ""} {
        set probe [get_hw_probes */$probe_name -of_objects $ila -quiet]
    }
    if {$probe eq ""} {
        puts "ILA_ERROR probe '$probe_name' not found"
        return 0
    }

    # Set trigger
    set_property TRIGGER_COMPARE_VALUE "eq$value" $probe

    # Arm and wait for trigger
    run_hw_ila $ila
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

# Write sentinel: FPGA is programmed, ILA is ready
set ready_file [file join $build_dir .ila_ready]
set fp [open $ready_file w]
puts $fp "ready"
close $fp

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
        } elseif {$cmd eq "ARM"} {
            if {[llength $parts] < 4} {
                puts "ILA_ERROR usage: ARM <probe> <value> <output_csv>"
                flush stdout
                continue
            }
            set probe_name [lindex $parts 1]
            set value [lindex $parts 2]
            set csv_path [lindex $parts 3]
            arm_and_capture $ila $probe_name $value $csv_path
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

    # Wait for CPU boot (sentinel file written by orchestrator)
    set boot_file [file join $build_dir .cpu_booted]
    puts "=== Waiting for CPU boot (sentinel: $boot_file) ==="
    while {![file exists $boot_file]} {
        after 500
    }
    file delete $boot_file
    puts "=== CPU booted — starting captures ==="

    set cap_num 0
    foreach cap $captures {
        incr cap_num
        set name [dict get $cap name]
        set probe_name [dict get $cap probe]
        set value [dict get $cap value]
        set output [dict get $cap output]
        set desc [dict get $cap description]

        puts "\n--- Capture $cap_num: $name ---"
        puts "  $desc"
        puts "  Trigger: $probe_name == $value"

        set csv_path [file join $build_dir $output]
        set ok [arm_and_capture $ila $probe_name $value $csv_path]
        if {$ok} {
            puts "  Captured: $csv_path"
        } else {
            puts "  FAILED"
        }
    }

    puts "\n=== Multi-capture complete: $cap_num captures ==="
}

# Cleanup
catch {file delete [file join $build_dir .ila_ready]}
close_hw_target
disconnect_hw_server
close_hw_manager
