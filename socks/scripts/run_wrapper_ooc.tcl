# OOC (out-of-context) synthesis + timing harness for a single SOCKS module.
# Skill script of record -- promoted from three per-packet copies that had
# drifted apart (gps_design codex-handoff/{plan-05-l1c-acquisition-axi-wrapper,
# plan-06-l1c-iq-sample-ddr-spill,unified-engine-rtl}/**/run_wrapper_ooc.tcl).
# See references/dsp/timing-closure.md ("OOC harness of record") for when to
# reach for this instead of a fresh per-packet script.
#
# WHY A SHARED SCRIPT: two defects were found independently, fixed locally in
# each copy, and would have rotted the next time a packet forked its own.
# Both are still guarded here and must never regress:
#
#   FIX 1 -- read the module's own constraint file, AFTER create_clock.
#     An OOC project that reads no XDC reports timing failures the full
#     project (which DOES apply the module's constraints/*.xdc, see
#     system_project.tcl-style project builders) would not have -- worse than
#     no OOC at all, because the extra failures look real and cost a closure
#     investigation. The constraints must be read AFTER the clocks are
#     created because same-clock exceptions (multicycle/false-path) resolve
#     against clock objects that must already exist. report_exceptions is
#     dumped so "no unexpected exception applies" is checkable, not asserted.
#
#   FIX 2 -- report_timing_summary with -delay_type min_max, never `max`.
#     `-delay_type max` reports ONLY setup (WNS) and produces no hold (WHS)
#     number at all. Any gate stated as "WNS/WHS >= 0" is unanswerable on the
#     hold half from a max-only report. Hold on an unplaced OOC netlist is
#     not a release gate by itself (hold is dominated by routing, and the
#     routed/implemented build is the timing authority) -- but a bar that
#     names a number must get one.
#
# GENERALIZATION -- sources are read from the module's own socks.json
# (dut.sources), never hardcoded in this script. A hand-written source list
# drifts behind socks.json silently: it either fails to elaborate, or worse,
# synthesizes a DIFFERENT design than the one the sim/vector gates proved and
# reports area/timing for it. This mirrors the fix the unified-engine-rtl
# packet copy had already made independently of the two defects above.
#
# Usage:
#   vivado -mode batch -source run_wrapper_ooc.tcl -tclargs \
#     <repo_root> <module_dir_rel> <top_entity> <out_dir> <clock_spec_csv> \
#     [<constraints_csv>] [<generics_csv>]
#
#   repo_root         Absolute path to the repo checkout (e.g. $WORKBASE/socks).
#   module_dir_rel    Module directory relative to repo_root
#                      (e.g. modules/pl_b2_l1c_acquisition_axi).
#   top_entity        Top-level entity to synthesize
#                      (e.g. pl_b2_l1c_acquisition_axi).
#   out_dir           Output directory for reports + checkpoint (created).
#   clock_spec_csv    Comma-separated <port_name>:<period_ns> pairs, e.g.
#                      "sys_cpu_clk:10.000,sys_dma_clk:4.000". The clock name
#                      is taken to equal the port name. With 2+ clocks, every
#                      pair is put in its own async clock group (matches the
#                      two-clock case below); with exactly 1 clock, no
#                      set_clock_groups call is made.
#   constraints_csv   Optional. Comma-separated XDC paths, each either
#                      absolute or relative to module_dir. Read via read_xdc
#                      AFTER create_clock/set_clock_groups (FIX 1). Default:
#                      every *.xdc directly under module_dir/constraints/.
#   generics_csv      Optional. Comma-separated KEY=VALUE synth_design
#                      generics, e.g. "N_MAX=131072".
#
# Worked examples (the three packet configurations this script replaces):
#   plan-05-l1c-acquisition-axi-wrapper (wrapper-only, pre-DDR-spill):
#     -tclargs $WORKBASE/socks modules/pl_b2_l1c_acquisition_axi \
#       pl_b2_l1c_acquisition_axi <out_dir> \
#       "sys_cpu_clk:10.000,sys_dma_clk:4.000"
#   plan-06-l1c-iq-sample-ddr-spill (+ DDR spill sources; no XDC read, no
#     hold report -- both defects present, now fixed here):
#     -tclargs $WORKBASE/socks modules/pl_b2_l1c_acquisition_axi \
#       pl_b2_l1c_acquisition_axi <out_dir> \
#       "sys_cpu_clk:10.000,sys_dma_clk:4.000"
#   unified-engine-rtl (socks.json-driven sources; optional N_MAX generic for
#     the full 2^17 build's BRAM/timing reconciliation):
#     -tclargs $WORKBASE/socks modules/pl_b2_l1c_acquisition_axi \
#       pl_b2_l1c_acquisition_axi <out_dir> \
#       "sys_cpu_clk:10.000,sys_dma_clk:4.000" "" "N_MAX=131072"

if {[llength $argv] < 5 || [llength $argv] > 7} {
  error "usage: vivado -mode batch -source run_wrapper_ooc.tcl -tclargs <repo_root> <module_dir_rel> <top_entity> <out_dir> <clock_spec_csv> ?<constraints_csv>? ?<generics_csv>?"
}
set repo_root       [file normalize [lindex $argv 0]]
set module_dir_rel  [lindex $argv 1]
set top_entity      [lindex $argv 2]
set out_dir         [file normalize [lindex $argv 3]]
set clock_spec_csv  [lindex $argv 4]
set constraints_csv ""
set generics_csv    ""
if {[llength $argv] >= 6} { set constraints_csv [lindex $argv 5] }
if {[llength $argv] >= 7} { set generics_csv    [lindex $argv 6] }

set module_dir [file join $repo_root $module_dir_rel]
if {![file isdirectory $module_dir]} {
  error "run_wrapper_ooc.tcl: module directory not found: $module_dir"
}
file mkdir $out_dir
set_param general.maxThreads 1

# ---------------------------------------------------------------------------
# Sources -- pulled from the module's socks.json (dut.sources) rather than
# hardcoded, so this script cannot drift behind the module's real file list.
# No JSON parser dependency: socks.json is machine-written with one quoted
# path per line inside the sources array, so a regex extraction is exact and
# avoids adding a Tcl JSON package requirement to the harness.
#
# Every quoted *.vhd entry is taken verbatim, in manifest order, and resolved
# relative to module_dir. Entries may therefore point OUTSIDE the module
# (e.g. "../common_sdf_fft_core/src/common_sdf_stage.vhd") so a shared source
# library compiles from the JSON authority exactly as written. The former
# extraction matched only "src/<name>.vhd" and silently truncated any other
# path to the consumer's own src/, which made a shared-source module
# uncompilable by this harness (common-SDF-FFT core hop, 2026-09-02). A
# missing file is an error here, not a downstream read_vhdl surprise.
# ---------------------------------------------------------------------------
set socks_json_path [file join $module_dir socks.json]
if {![file exists $socks_json_path]} {
  error "run_wrapper_ooc.tcl: socks.json not found: $socks_json_path"
}
set fh [open $socks_json_path r]
set json [read $fh]
close $fh
if {![regexp {"sources"\s*:\s*\[(.*?)\]} $json -> sources_blob]} {
  error "run_wrapper_ooc.tcl: could not find dut.sources in $socks_json_path"
}
set sources {}
foreach {_full source_entry} [regexp -all -inline {"([^"]+\.vhd)"} $sources_blob] {
  lappend sources $source_entry
}
if {[llength $sources] < 1} {
  error "run_wrapper_ooc.tcl: dut.sources is empty or unparseable in $socks_json_path"
}
puts "run_wrapper_ooc: [llength $sources] sources from socks.json"

foreach source $sources {
  set source_path [file normalize [file join $module_dir $source]]
  if {![file exists $source_path]} {
    error "run_wrapper_ooc.tcl: dut.sources entry not found: $source (resolved $source_path)"
  }
  puts "  read_vhdl $source"
  read_vhdl -vhdl2008 $source_path
}

# ---------------------------------------------------------------------------
# Synthesis
# ---------------------------------------------------------------------------
set generic_args {}
if {$generics_csv ne ""} {
  foreach kv [split $generics_csv ","] {
    if {$kv eq ""} { continue }
    lappend generic_args -generic $kv
  }
}
if {[llength $generic_args] > 0} {
  puts "run_wrapper_ooc: synth_design generics: $generic_args"
  synth_design -top $top_entity \
    -part xczu9eg-ffvb1156-2-e -mode out_of_context {*}$generic_args
} else {
  synth_design -top $top_entity \
    -part xczu9eg-ffvb1156-2-e -mode out_of_context
}

# ---------------------------------------------------------------------------
# Clocks -- parsed from clock_spec_csv as <port>:<period_ns> pairs. The clock
# name is taken to equal the port name (true for every module this script
# has been pointed at so far).
# ---------------------------------------------------------------------------
set clock_names {}
foreach pair [split $clock_spec_csv ","] {
  if {$pair eq ""} { continue }
  set parts [split $pair ":"]
  if {[llength $parts] != 2} {
    error "run_wrapper_ooc.tcl: malformed clock_spec entry '$pair' (want <port>:<period_ns>)"
  }
  set clk_port   [lindex $parts 0]
  set clk_period [lindex $parts 1]
  create_clock -name $clk_port -period $clk_period [get_ports $clk_port]
  lappend clock_names $clk_port
}
if {[llength $clock_names] < 1} {
  error "run_wrapper_ooc.tcl: clock_spec_csv produced no clocks: '$clock_spec_csv'"
}
if {[llength $clock_names] >= 2} {
  set group_args {}
  foreach clk $clock_names {
    lappend group_args -group [get_clocks $clk]
  }
  set_clock_groups -asynchronous {*}$group_args
}

# ---------------------------------------------------------------------------
# FIX 1: the module's own constraint file(s), read AFTER create_clock /
# set_clock_groups -- same-clock exceptions (multicycle, false path) resolve
# against clock objects that must already exist. Defaults to every *.xdc
# directly under module_dir/constraints/ when constraints_csv is empty, so
# a module with the standard layout needs no explicit list.
# ---------------------------------------------------------------------------
set constraint_files {}
if {$constraints_csv ne ""} {
  foreach entry [split $constraints_csv ","] {
    if {$entry eq ""} { continue }
    if {[file pathtype $entry] eq "absolute"} {
      lappend constraint_files [file normalize $entry]
    } else {
      lappend constraint_files [file normalize [file join $module_dir $entry]]
    }
  }
} else {
  set default_constraints_dir [file join $module_dir constraints]
  if {[file isdirectory $default_constraints_dir]} {
    foreach f [glob -nocomplain -directory $default_constraints_dir *.xdc] {
      lappend constraint_files [file normalize $f]
    }
  }
}
if {[llength $constraint_files] < 1} {
  puts "run_wrapper_ooc: WARNING no constraint files found/specified -- OOC envelope carries only create_clock, nothing from the module's own XDC. This is the exact gap FIX 1 exists to close; pass constraints_csv or add module_dir/constraints/*.xdc."
} else {
  foreach cf $constraint_files {
    if {![file exists $cf]} {
      error "run_wrapper_ooc.tcl: constraint file not found: $cf"
    }
    read_xdc $cf
    puts "run_wrapper_ooc: applied [file tail $cf]"
  }
}
report_exceptions -file [file join $out_dir wrapper_exceptions.txt]

# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------
report_utilization -file [file join $out_dir wrapper_utilization.txt]
# FIX 2: min_max, not max. `-delay_type max` reports setup (WNS) only and
# produces NO hold (WHS) number at all, while a bar stated as "WNS/WHS >= 0"
# needs both. Hold at an unplaced OOC netlist is not a release gate on its
# own (hold is dominated by routing; the routed/implemented build is the
# authority) -- but a bar that names a number should get one.
report_timing_summary -delay_type min_max -report_unconstrained -check_timing_verbose \
  -file [file join $out_dir wrapper_timing_summary.txt]
report_timing -max_paths 50 -sort_by group \
  -file [file join $out_dir wrapper_timing_paths.txt]
write_checkpoint -force [file join $out_dir ${top_entity}_synth.dcp]
