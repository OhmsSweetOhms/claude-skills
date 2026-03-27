# build_app.template.tcl - Build bare-metal HIL test app using XSCT
# Parameterized by hil_firmware.py via str.replace()

set build_dir  "{{BUILD_DIR}}"
set ws_dir     [file join $build_dir vitis_ws]
set xsa_path   [file join $build_dir system_wrapper.xsa]

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
    -proc ps7_cortexa9_0 -os standalone

# Create application
app create -name hil_app -platform hil_platform \
    -template "Empty Application"

# Import test sources
{{IMPORT_SOURCES_TCL}}

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
