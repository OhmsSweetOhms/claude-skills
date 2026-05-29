# build_app.template.tcl - Build bare-metal HIL test app using XSCT
# Parameterized by hil_firmware.py via str.replace()
#
# Phases (--phase <name>, default "all"):
#   create -- setws, platform, BSP, app create, importsources, configapp;
#             stops BEFORE `app build` so the Python wrapper can rewrite
#             the Vitis-generated lscript.ld between platform link and
#             final link. Triggered by hil_firmware.py when hil.json has
#             a firmware.linker_placement entry.
#   build  -- setws, then `app build` only. Used as the second phase
#             after `create` + Python linker rewrite.
#   all    -- create + build in one XSCT invocation (the historical
#             default; what runs when firmware.linker_placement is
#             absent).

set build_dir  "{{BUILD_DIR}}"
set ws_dir     "{{WS_DIR}}"
set xsa_path   "{{XSA_PATH}}"
set processor  "{{PROCESSOR}}"
set platform_name "{{PLATFORM_NAME}}"
set app_name "{{APP_NAME}}"
set bsp_config_tcl "{{BSP_CONFIG_TCL}}"

# Parse --debug flag
set debug_mode 0
if {[lsearch -exact $argv "--debug"] >= 0} {
    set debug_mode 1
}

# Parse --phase flag (default "all")
set phase "all"
set phase_idx [lsearch -exact $argv "--phase"]
if {$phase_idx >= 0 && $phase_idx + 1 < [llength $argv]} {
    set phase [lindex $argv [expr {$phase_idx + 1}]]
}
if {$phase ni {create build all}} {
    error "build_app.template.tcl: --phase must be create|build|all, got '$phase'"
}

set do_create [expr {$phase in {create all}}]
set do_build  [expr {$phase in {build all}}]

if {$do_create} {
    # Remove old workspace if it exists (rebuild scenario). Only the
    # create phase wipes the workspace -- the build phase must preserve
    # the workspace state produced by an earlier create phase (and the
    # linker script the Python wrapper rewrote between phases).
    if {[file exists $ws_dir]} {
        puts "=== Removing old workspace for clean rebuild ==="
        file delete -force $ws_dir
    }
}

# Create workspace (both phases need setws to attach)
setws $ws_dir

if {$do_create} {
    # Create platform from XSA
    platform create -name $platform_name -hw $xsa_path \
        -proc $processor -os standalone

    # Create application
    app create -name $app_name -platform $platform_name \
        -template "Empty Application"

    # Optional project-specific BSP configuration. This is used by system HIL
    # projects that need a non-default standalone domain, such as R5/lwIP.
    if {$bsp_config_tcl ne ""} {
        puts "=== Applying BSP config: $bsp_config_tcl ==="
        platform active $platform_name
        domain active standalone_domain
        source $bsp_config_tcl
        bsp regenerate
        platform generate
    }

    # Import test sources
    {{IMPORT_SOURCES_TCL}}

    # Let nested imported source trees include headers from the app source root
    # (for example, "net/foo.h" and "drv/bar.h").
    configapp -app $app_name -add compiler-misc {-I../src}

    # Add debug symbols and disable optimization if --debug
    if {$debug_mode} {
        configapp -app $app_name -add compiler-misc {-g -O0 -fno-omit-frame-pointer}
        configapp -app $app_name -add compiler-misc {-DHIL_DEBUG_MODE}
        puts "=== Debug mode: -g -O0 -fno-omit-frame-pointer + HIL_DEBUG_MODE ==="
    }
}

if {$do_build} {
    app build -name $app_name
    puts "=== Build complete (phase=$phase, debug=$debug_mode) ==="
    puts "  ELF: [file join $ws_dir $app_name/Debug/$app_name.elf]"
} else {
    puts "=== Phase complete: $phase (no app build) ==="
    puts "  Workspace: $ws_dir"
    puts "  lscript:   [file join $ws_dir $app_name/src/lscript.ld]"
}
