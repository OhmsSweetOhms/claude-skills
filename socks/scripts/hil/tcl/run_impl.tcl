# run_impl.tcl - Synthesise, implement, generate bitstream and XSA
# Usage: vivado -mode batch -source run_impl.tcl -tclargs <build_dir> <project_name>

if {[llength $argv] < 2} {
    error "Usage: run_impl.tcl <build_dir> <project_name>"
}

set build_dir     [lindex $argv 0]
set project_name  [lindex $argv 1]
set proj_dir      [file join $build_dir vivado_project]

# Open project
open_project [file join $proj_dir ${project_name}.xpr]

# Detect debug mode from project generic
set generics [get_property generic [current_fileset]]
set is_debug [expr {[string match *DEBUG=true* $generics]}]

# Reset stale runs before launching (including DUT OOC synthesis).
# Module references in the block design get their own OOC synthesis run
# (e.g. system_dut_0_synth_1). If RTL sources change after Stage 14,
# the OOC checkpoint goes stale while reset_run synth_1 only resets the
# top-level run. Without this, Vivado silently reuses the old DUT netlist.
foreach run [get_runs -filter {IS_SYNTHESIS && NAME != synth_1}] {
    catch {reset_run $run}
}
reset_run synth_1
catch {reset_run impl_1}

# Synthesise
launch_runs synth_1 -jobs 4
wait_on_run synth_1
if {[get_property STATUS [get_runs synth_1]] ne "synth_design Complete!"} {
    error "Synthesis failed"
}
puts "=== Synthesis complete ==="

# Implement + write bitstream
launch_runs impl_1 -to_step write_bitstream -jobs 4
wait_on_run impl_1
if {[get_property STATUS [get_runs impl_1]] ne "write_bitstream Complete!"} {
    error "Implementation failed"
}
puts "=== Implementation + bitstream complete ==="

# Print timing summary (line-by-line to avoid catastrophic backtracking)
set timing_rpt [file join $proj_dir ${project_name}.runs/impl_1/hil_top_timing_summary_routed.rpt]
if {[file exists $timing_rpt]} {
    set fp [open $timing_rpt r]
    set next_is_wns 0
    set next_is_whs 0
    while {[gets $fp line] >= 0} {
        if {[string match "*WNS(ns)*TNS(ns)*" $line]} {
            set next_is_wns 1
        } elseif {$next_is_wns && [regexp {^\s*(\S+)\s+(\S+)} $line -> wns tns]} {
            puts "  WNS: $wns ns"
            puts "  TNS: $tns ns"
            set next_is_wns 0
        }
        if {[string match "*WHS(ns)*THS(ns)*" $line]} {
            set next_is_whs 1
        } elseif {$next_is_whs && [regexp {^\s*(\S+)\s+(\S+)} $line -> whs ths]} {
            puts "  WHS: $whs ns"
            puts "  THS: $ths ns"
            set next_is_whs 0
        }
    }
    close $fp
}

# Generate XSA (hardware platform for Vitis)
set xsa_path [file join $build_dir system_wrapper.xsa]
write_hw_platform -fixed -include_bit -force $xsa_path
puts "=== XSA: $xsa_path ==="

# Copy ps7_init.tcl to stable path
set ps7_init_glob [glob -nocomplain [file join $proj_dir ${project_name}.runs/impl_1/*/ps7_init.tcl]]
if {$ps7_init_glob eq ""} {
    set ps7_init_glob [glob -nocomplain [file join $proj_dir ${project_name}.gen/sources_1/bd/system/ip/system_ps7_0/ps7_init.tcl]]
}
if {$ps7_init_glob ne ""} {
    set ps7_init_src [lindex $ps7_init_glob 0]
    set ps7_init_dst [file join $build_dir ps7_init.tcl]
    file copy -force $ps7_init_src $ps7_init_dst
    puts "=== ps7_init.tcl copied to: $ps7_init_dst ==="
} else {
    puts "WARNING: ps7_init.tcl not found in gen output"
}

# Verify .ltx exists when debug is enabled
if {$is_debug} {
    set ltx_path [file join $proj_dir ${project_name}.runs/impl_1/hil_top.ltx]
    if {[file exists $ltx_path]} {
        file copy -force $ltx_path [file join $build_dir hil_top.ltx]
        puts "=== Debug probes: $ltx_path -> [file join $build_dir hil_top.ltx] ==="
    } else {
        puts "ERROR: DEBUG=true but no .ltx generated — ILA insertion may have failed"
        puts "       Check implementation log for insert_debug.xdc errors"
    }
}

puts "=== Build complete (debug=$is_debug) ==="
