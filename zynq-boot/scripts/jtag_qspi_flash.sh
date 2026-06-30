#!/usr/bin/env bash
# jtag_qspi_flash.sh — wrapper for jtag_qspi_flash.tcl.
#
# Boot-mode-independent QSPI flash over JTAG (no UART) for a Zynq/ZynqMP board you
# CANNOT put into JTAG boot mode (e.g. hardwired-QSPI straps). See the companion
# reference: references/jtag-flash-bootmode-independent.md.
#
# STATUS: authored, NOT yet hardware-verified.
#
# This wrapper exists to honor two zynq-boot rules: (1) source the Vitis toolchain so
# xsct is on PATH, and (2) RESOLVE ALL PATHS TO ABSOLUTE here — xsct does NOT expand
# $USER or shell vars, so a var left in a path reaches the tool verbatim and fails.
#
# Usage (PS init: provide ONE of --psinit / --fsbl):
#   jtag_qspi_flash.sh --arch zynq   --psinit /abs/ps7_init.tcl --uboot /abs/uboot.elf
#   jtag_qspi_flash.sh --arch zynq   --fsbl   /abs/fsbl.elf     --uboot /abs/uboot.elf   # FSBL does the init
#   jtag_qspi_flash.sh --arch zynqmp --psinit /abs/psu_init.tcl --uboot /abs/uboot.elf  --pmufw /abs/pmufw.elf
#
#   --psinit  ps7_init.tcl / psu_init.tcl — direct register writes, preferred (no side effects).
#   --fsbl    an FSBL ELF — does ps7_init by executing; the tcl runs it only until DDR is up,
#             then halts before it boots from QSPI. Handy when you have the FSBL but no tcl.
#
# By DEFAULT --uboot should point at a DCC-console U-Boot so the flow is UART-free:
#   - the cfgmem helper itself: <Vitis>/data/xicom/cfgmem/uboot/zynq_qspi_x1_single.bin
#     (it IS an ELF; arm_dcc is its only console — no UART driver), or your
#     JEDEC-patched rebuild of it; or
#   - a custom U-Boot built with CONFIG_ARM_DCC=y and stdin/stdout/stderr=dcc.
# A stock ttyPS u-boot.elf will need the serial UART instead (this script can't help there).
set -euo pipefail

ARCH="" ; PSINIT="" ; UBOOT="" ; FSBL="" ; PMUFW="" ; TGT="" ; URL="tcp:localhost:3121"
VITIS="${ZB_VITIS:-}"     # e.g. /tools/Xilinx/Vitis/2023.2 ; if unset, auto-pick newest

usage() { sed -n '2,22p' "$0" ; exit "${1:-1}" ; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --arch)   ARCH="$2";   shift 2 ;;
    --psinit) PSINIT="$2"; shift 2 ;;
    --uboot)  UBOOT="$2";  shift 2 ;;
    --fsbl)   FSBL="$2";   shift 2 ;;
    --pmufw)  PMUFW="$2";  shift 2 ;;
    --tgt)    TGT="$2";    shift 2 ;;
    --url)    URL="$2";    shift 2 ;;
    --vitis)  VITIS="$2";  shift 2 ;;
    -h|--help) usage 0 ;;
    *) echo "unknown arg: $1" >&2 ; usage ;;
  esac
done

[[ -n "$ARCH" && -n "$UBOOT" && ( -n "$PSINIT" || -n "$FSBL" ) ]] || usage
[[ "$ARCH" == "zynq" || "$ARCH" == "zynqmp" ]] || { echo "--arch must be zynq|zynqmp" >&2; exit 1; }

# Default target filter per arch (override with --tgt).
if [[ -z "$TGT" ]]; then
  if [[ "$ARCH" == "zynq" ]]; then TGT='*Cortex-A9*#0'; else TGT='*A53*#0'; fi
fi

# Resolve toolchain.
if [[ -z "$VITIS" ]]; then
  VITIS="$(ls -d /tools/Xilinx/Vitis/* 2>/dev/null | sort -V | tail -1 || true)"
fi
[[ -n "$VITIS" && -d "$VITIS" ]] || { echo "Vitis not found; set --vitis or ZB_VITIS" >&2; exit 1; }
# shellcheck disable=SC1091
source "$VITIS/.settings64-Vitis.sh"

# Resolve every path to absolute (the $USER-non-expansion rule). realpath fails loudly
# if a file is missing, which is what we want before handing it to xsct.
abspath() { realpath -e "$1"; }
export ZB_ARCH="$ARCH"
export ZB_TGT="$TGT"
export ZB_URL="$URL"
export ZB_UBOOT="$(abspath "$UBOOT")"
[[ -n "$PSINIT" ]] && export ZB_PSINIT="$(abspath "$PSINIT")"
[[ -n "$FSBL"  ]] && export ZB_FSBL="$(abspath "$FSBL")"
[[ -n "$PMUFW" ]] && export ZB_PMUFW="$(abspath "$PMUFW")"

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
echo "== arch=$ARCH tgt='$TGT' vitis=$VITIS =="
echo "== psinit=$ZB_PSINIT =="
echo "== uboot=$ZB_UBOOT =="
exec xsct "$HERE/jtag_qspi_flash.tcl"
