# gen_hil_top.tcl - Generate hil_top.vhd from the actual system_wrapper ports
#
# Called after make_wrapper.  Reads the generated Verilog wrapper, extracts
# every port, then emits hil_top.vhd that:
#   - Passes through DDR/FIXED_IO ports to top-level I/O
#   - Wires loopback output -> loopback input internally (one or more pairs)
#   - Connects monitor/irq ports to internal signals with MARK_DEBUG
#   - Ties unused input ports to '0' (tristate enables, unused channels)
#
# Port classification is driven by hil.json (via template parameters):
#   loopback_out       — prefix for the primary loopback output port
#   loopback_in        — prefix for the primary loopback input port
#   monitor_prefixes   — space-separated prefixes for monitor ports
#   extra_lb_pairs     — space-separated "out:in" pairs for additional loopback
#   tie_low_ports      — space-separated port names to tie to '0'
#
# Usage (from create_project.tcl):
#   source gen_hil_top.tcl
#   gen_hil_top $wrapper_file $output_vhd $enable_debug $lb_out $lb_in $mon_pfx $extra_lb $tie_low

proc gen_hil_top {wrapper_file output_vhd enable_debug loopback_out loopback_in monitor_prefixes {extra_lb_pairs ""} {tie_low_ports ""}} {

    # --- Parse wrapper ports from Verilog ---
    set fp [open $wrapper_file r]
    set src [read $fp]
    close $fp

    # Collect ports: {direction name msb lsb} or {direction name -1 -1} for scalars
    set ports {}
    foreach line [split $src \n] {
        set line [string trim $line]
        regsub {[,;)\s]+$} $line {} line
        if {[regexp {^\s*(input|output|inout)\s+\[(\d+):(\d+)\]\s*(\w+)} $line -> dir msb lsb name]} {
            lappend ports [list $dir $name $msb $lsb]
        } elseif {[regexp {^\s*(input|output|inout)\s+(\w+)} $line -> dir name]} {
            lappend ports [list $dir $name -1 -1]
        }
    }

    if {[llength $ports] == 0} {
        error "gen_hil_top: no ports parsed from $wrapper_file"
    }

    puts "gen_hil_top: parsed [llength $ports] ports from wrapper"

    # --- Build loopback pair list ---
    # Primary pair
    set lb_pairs [list [list $loopback_out $loopback_in]]
    # Extra pairs from "out:in out:in" string
    foreach pair $extra_lb_pairs {
        if {$pair eq ""} continue
        set parts [split $pair ":"]
        if {[llength $parts] == 2} {
            lappend lb_pairs [list [lindex $parts 0] [lindex $parts 1]]
        }
    }

    # --- Build tie-low set ---
    set tie_low_set {}
    foreach p $tie_low_ports {
        if {$p ne ""} {
            lappend tie_low_set $p
        }
    }

    # --- Classify ports ---
    set passthrough {}
    set internal_out {}     ;# monitor/irq ports we capture internally
    set loopback_ports {}   ;# list of {out_name in_name msb lsb} for each pair
    set tie_low_list {}     ;# ports to tie to '0': {name msb lsb}

    # Helper: check if port name matches a loopback pair
    proc match_lb_port {name lb_pairs} {
        foreach pair $lb_pairs {
            lassign $pair lb_out lb_in
            if {[string match "${lb_out}_*" $name] || $name eq $lb_out ||
                [string match "${lb_out}0" [string map {"_" ""} $name]]} {
                return [list "out" $pair]
            }
            if {[string match "${lb_in}_*" $name] || $name eq $lb_in ||
                [string match "${lb_in}0" [string map {"_" ""} $name]]} {
                return [list "in" $pair]
            }
        }
        return ""
    }

    # Helper: check if port name matches tie-low list
    proc match_tie_low {name tie_low_set} {
        foreach p $tie_low_set {
            if {[string match "${p}_*" $name] || $name eq $p ||
                [string match "${p}0" [string map {"_" ""} $name]]} {
                return 1
            }
        }
        return 0
    }

    # Track which loopback pairs have been matched
    array set lb_out_matched {}
    array set lb_in_matched {}

    foreach p $ports {
        lassign $p dir name msb lsb

        # Check loopback
        set lb_match [match_lb_port $name $lb_pairs]
        if {$lb_match ne ""} {
            lassign $lb_match role pair
            lassign $pair lb_out lb_in
            set key "${lb_out}:${lb_in}"
            if {$role eq "out"} {
                set lb_out_matched($key) [list $name $msb $lsb]
            } else {
                set lb_in_matched($key) [list $name $msb $lsb]
            }
            continue
        }

        # Check tie-low
        if {[match_tie_low $name $tie_low_set]} {
            lappend tie_low_list [list $name $msb $lsb]
            continue
        }

        # Check monitor or irq
        set is_internal 0
        foreach pfx $monitor_prefixes {
            if {[string match "${pfx}*" $name]} {
                set is_internal 1
                break
            }
        }
        # Also capture irq ports internally
        if {[string match "irq*" $name]} {
            set is_internal 1
        }
        if {$is_internal} {
            lappend internal_out [list $dir $name $msb $lsb]
            continue
        }

        # Everything else is passthrough (DDR, FIXED_IO)
        lappend passthrough [list $dir $name $msb $lsb]
    }

    # Validate loopback pairs
    foreach pair $lb_pairs {
        lassign $pair lb_out lb_in
        set key "${lb_out}:${lb_in}"
        if {![info exists lb_out_matched($key)] || ![info exists lb_in_matched($key)]} {
            puts "WARNING: gen_hil_top: loopback pair ${lb_out}:${lb_in} not fully matched in wrapper"
        }
    }

    # --- Helper: VHDL type string ---
    proc vhdl_type {msb lsb} {
        if {$msb == -1} {
            return "std_logic"
        } else {
            return "std_logic_vector($msb downto $lsb)"
        }
    }

    # --- Emit VHDL ---
    set out {}
    # Get primary loopback names for description
    set primary_out [lindex [array get lb_out_matched] 1]
    set primary_in  [lindex [array get lb_in_matched] 1]
    set primary_out_name [lindex $primary_out 0]
    set primary_in_name  [lindex $primary_in 0]

    lappend out "-- ============================================================================="
    lappend out "-- Module:       hil_top"
    lappend out "-- Description:  HIL test top-level — loopback ${primary_out_name} -> ${primary_in_name}"
    lappend out "-- ============================================================================="
    lappend out "-- AUTO-GENERATED by gen_hil_top.tcl from system_wrapper ports."
    lappend out "-- Do not hand-edit — re-run create_project.tcl to regenerate."
    lappend out "-- ============================================================================="
    lappend out ""
    lappend out "library ieee;"
    lappend out "use ieee.std_logic_1164.all;"
    lappend out ""
    lappend out "entity hil_top is"
    lappend out "    generic ("
    lappend out "        DEBUG : boolean := false"
    lappend out "    );"
    lappend out "    port ("

    # Emit passthrough ports only (should be DDR + FIXED_IO = no PL I/O)
    set last_idx [expr {[llength $passthrough] - 1}]
    for {set i 0} {$i <= $last_idx} {incr i} {
        lassign [lindex $passthrough $i] dir name msb lsb
        set vtype [vhdl_type $msb $lsb]
        switch $dir {
            inout  {set vdir "inout"}
            input  {set vdir "in   "}
            output {set vdir "out  "}
        }
        set trail [expr {$i < $last_idx ? ";" : ""}]
        lappend out [format "        %-22s : %s %s%s" $name $vdir $vtype $trail]
    }

    lappend out "    );"
    lappend out "end entity hil_top;"
    lappend out ""
    lappend out "architecture rtl of hil_top is"
    lappend out ""

    # Loopback signals
    foreach {key val} [array get lb_out_matched] {
        lassign $val name msb lsb
        set sig_name "[string trimright $name 0123456789_]_loopback"
        regsub {_+loopback$} $sig_name "_loopback" sig_name
        lappend out "    signal [format %-22s $sig_name] : [vhdl_type $msb $lsb];"
    }

    # Internal signals for monitor/irq ports
    foreach p $internal_out {
        lassign $p dir name msb lsb
        set sig_name "[string trimright $name 0123456789_]_s"
        regsub {_+s$} $sig_name "_s" sig_name
        set vtype [vhdl_type $msb $lsb]
        lappend out "    signal [format %-22s $sig_name] : $vtype;"
    }

    lappend out ""
    lappend out "    attribute MARK_DEBUG : string;"

    # MARK_DEBUG on loopback signals
    foreach {key val} [array get lb_out_matched] {
        lassign $val name msb lsb
        set sig_name "[string trimright $name 0123456789_]_loopback"
        regsub {_+loopback$} $sig_name "_loopback" sig_name
        lappend out "    attribute MARK_DEBUG of [format %-22s $sig_name] : signal is \"true\";"
    }

    # MARK_DEBUG on each monitor/irq signal
    foreach p $internal_out {
        lassign $p dir name msb lsb
        set sig_name "[string trimright $name 0123456789_]_s"
        regsub {_+s$} $sig_name "_s" sig_name
        lappend out "    attribute MARK_DEBUG of [format %-22s $sig_name] : signal is \"true\";"
    }

    lappend out ""
    lappend out "begin"
    lappend out ""
    lappend out "    u_system : entity work.system_wrapper"
    lappend out "        port map ("

    # All passthrough ports: name => name
    foreach p $passthrough {
        lassign $p dir name msb lsb
        lappend out [format "            %-26s => %s," $name $name]
    }

    # Loopback pairs: output drives signal, input reads it
    foreach {key val} [array get lb_out_matched] {
        lassign $val out_name out_msb out_lsb
        set sig_name "[string trimright $out_name 0123456789_]_loopback"
        regsub {_+loopback$} $sig_name "_loopback" sig_name
        lappend out [format "            %-26s => %s," $out_name $sig_name]

        if {[info exists lb_in_matched($key)]} {
            lassign $lb_in_matched($key) in_name in_msb in_lsb
            lappend out [format "            %-26s => %s," $in_name $sig_name]
        }
    }

    # Tie-low ports: connect to '0'
    foreach p $tie_low_list {
        lassign $p name msb lsb
        if {$msb == -1} {
            lappend out [format "            %-26s => '0'," $name]
        } else {
            lappend out [format "            %-26s => (others => '0')," $name]
        }
    }

    # Monitor/irq ports
    set all_internal $internal_out
    set last_int [expr {[llength $all_internal] - 1}]
    for {set i 0} {$i <= $last_int} {incr i} {
        lassign [lindex $all_internal $i] dir name msb lsb
        set sig_name "[string trimright $name 0123456789_]_s"
        regsub {_+s$} $sig_name "_s" sig_name
        set trail [expr {$i < $last_int ? "," : ""}]
        lappend out [format "            %-26s => %s%s" $name $sig_name $trail]
    }

    lappend out "        );"
    lappend out ""
    lappend out "end architecture rtl;"
    lappend out ""

    # Write file
    set fp [open $output_vhd w]
    puts $fp [join $out \n]
    close $fp

    set n_lb [expr {[array size lb_out_matched]}]
    set n_tie [llength $tie_low_list]
    puts "gen_hil_top: wrote $output_vhd ([llength $passthrough] passthrough, [llength $internal_out] monitor, $n_lb loopback, $n_tie tie-low)"
}
