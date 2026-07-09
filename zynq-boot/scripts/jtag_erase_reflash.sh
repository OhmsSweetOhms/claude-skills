#!/usr/bin/env bash
# jtag_erase_reflash.sh — THIN Linux convenience wrapper for jtag_erase_reflash.tcl.
# The TCL is the product (identical invocation on Windows/Linux once xsdb is on PATH);
# this wrapper only puts xsdb on PATH and forwards every argument untouched.
#
#   jtag_erase_reflash.sh erase        --ps7 /abs/ps7_init.tcl
#   jtag_erase_reflash.sh erase+flash  /abs/boot.mcs --ps7 /abs/ps7_init.tcl
#
# Toolchain pick mirrors jtag_qspi_flash.sh: ZB_VITIS env var wins, else the newest
# install under /tools/Xilinx/Vitis. If xsdb is ALREADY on PATH (shell pre-sourced),
# nothing is sourced at all.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if ! command -v xsdb >/dev/null 2>&1; then
    VITIS="${ZB_VITIS:-$(ls -d /tools/Xilinx/Vitis/* 2>/dev/null | sort -V | tail -1 || true)}"
    [[ -n "$VITIS" && -d "$VITIS" ]] || { echo "Vitis not found; set ZB_VITIS or source settings64.sh first" >&2; exit 1; }
    # Installs ship the settings file under either name — take whichever exists.
    for f in "$VITIS/settings64.sh" "$VITIS/.settings64-Vitis.sh"; do
        [[ -f "$f" ]] && { source "$f"; break; }
    done
fi

exec xsdb "$HERE/jtag_erase_reflash.tcl" "$@"
