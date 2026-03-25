# insert_debug.xdc - Create ILA core for HIL debug
# Add to project ONLY when building with --debug flag.
# Automatically collects all MARK_DEBUG nets from hil_top.vhd into probe0.
# Signal names and widths are DUT-specific (driven by hil.json wiring config).

# Create ILA core with advanced trigger (supports eq, neq, gt, lt, gteq, lteq)
create_debug_core u_ila_0 ila
set_property C_DATA_DEPTH 4096 [get_debug_cores u_ila_0]
set_property C_TRIGIN_EN false [get_debug_cores u_ila_0]
set_property C_TRIGOUT_EN false [get_debug_cores u_ila_0]
set_property C_ADV_TRIGGER true [get_debug_cores u_ila_0]
set_property C_INPUT_PIPE_STAGES 0 [get_debug_cores u_ila_0]
set_property C_EN_STRG_QUAL false [get_debug_cores u_ila_0]
set_property ALL_PROBE_SAME_MU true [get_debug_cores u_ila_0]
set_property ALL_PROBE_SAME_MU_CNT 1 [get_debug_cores u_ila_0]

# Connect ILA clock to FCLK_CLK0 (clock object: clk_fpga_0)
set_property port_width 1 [get_debug_ports u_ila_0/clk]
connect_debug_port u_ila_0/clk [get_nets -of_objects [get_clocks clk_fpga_0]]

# Connect all MARK_DEBUG nets into probe0
set debug_nets [get_nets -hierarchical -filter {MARK_DEBUG == true}]
set_property port_width [llength $debug_nets] [get_debug_ports u_ila_0/probe0]
connect_debug_port u_ila_0/probe0 $debug_nets
