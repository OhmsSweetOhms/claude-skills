# build_app.template.tcl - Build bare-metal HIL test app using XSCT
# Parameterized by hil_firmware.py via str.replace()

set build_dir  "{{BUILD_DIR}}"
set ws_dir     [file join $build_dir vitis_ws]
set xsa_path   [file join $build_dir system_wrapper.xsa]
set processor  "{{PROCESSOR}}"
set bsp_config_tcl "{{BSP_CONFIG_TCL}}"

# Parse --debug flag
set debug_mode 0
if {[lsearch -exact $argv "--debug"] >= 0} {
    set debug_mode 1
}

# Remove old workspace if it exists (rebuild scenario)
if {[file exists $ws_dir]} {
    puts "=== Removing old workspace for clean rebuild ==="
    file delete -force $ws_dir
}

# Create workspace
setws $ws_dir

# Create platform from XSA
platform create -name hil_platform -hw $xsa_path \
    -proc $processor -os standalone

# Create application
app create -name hil_app -platform hil_platform \
    -template "Empty Application"

# Optional project-specific BSP configuration. This is used by system HIL
# projects that need a non-default standalone domain, such as R5/lwIP.
if {$bsp_config_tcl ne ""} {
    puts "=== Applying BSP config: $bsp_config_tcl ==="
    platform active hil_platform
    domain active standalone_domain
    source $bsp_config_tcl
    bsp regenerate
    platform generate
}

# Import test sources
{{IMPORT_SOURCES_TCL}}

# Let nested imported source trees include headers from the app source root
# (for example, "net/foo.h" and "drv/bar.h").
configapp -app hil_app -add compiler-misc {-I../src}

# Add debug symbols and disable optimization if --debug
if {$debug_mode} {
    configapp -app hil_app -add compiler-misc {-g -O0 -fno-omit-frame-pointer}
    configapp -app hil_app -add compiler-misc {-DHIL_DEBUG_MODE}
    puts "=== Debug mode: -g -O0 -fno-omit-frame-pointer + HIL_DEBUG_MODE ==="
}

# Build
app build -name hil_app

puts "=== Build complete (debug=$debug_mode) ==="
puts "  ELF: [file join $ws_dir hil_app/Debug/hil_app.elf]"
