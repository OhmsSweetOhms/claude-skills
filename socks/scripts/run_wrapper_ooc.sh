#!/usr/bin/env bash
# Shell driver for run_wrapper_ooc.tcl (see that file's header for the fixes
# it guards and full argument semantics). Takes the SOCKS build-class lock,
# same as every other build-class job in the skill (references/jobs.md).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SETTINGS="${SETTINGS:-/tools/Xilinx/Vivado/2022.2/.settings64-Vivado.sh}"
LOCK_FILE="${SOCKS_BUILD_CLASS_LOCK:-/tmp/socks-build-class.lock}"
LOCK_WAIT="${LOCK_WAIT:-300}"

die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

if [[ $# -lt 5 || $# -gt 7 ]]; then
  die "usage: run_wrapper_ooc.sh <repo_root> <module_dir_rel> <top_entity> <run_root> <clock_spec_csv> [<constraints_csv>] [<generics_csv>]

  repo_root         Absolute path to the repo checkout (e.g. \$WORKBASE/socks).
  module_dir_rel    Module directory relative to repo_root
                     (e.g. modules/pl_b2_l1c_acquisition_axi).
  top_entity        Top-level entity to synthesize.
  run_root          Output directory for reports + checkpoint. Must not
                     already exist.
  clock_spec_csv    Comma-separated <port_name>:<period_ns> pairs, e.g.
                     \"sys_cpu_clk:10.000,sys_dma_clk:4.000\".
  constraints_csv   Optional. Comma-separated XDC paths (relative to
                     module_dir, or absolute). Pass \"\" to keep the default
                     (module_dir/constraints/*.xdc) while still supplying
                     generics_csv.
  generics_csv      Optional. Comma-separated KEY=VALUE synth_design
                     generics, e.g. \"N_MAX=131072\".

Example (pl_b2_l1c_acquisition_axi, the module this harness was proved on):
  run_wrapper_ooc.sh \$WORKBASE/socks modules/pl_b2_l1c_acquisition_axi \\
    pl_b2_l1c_acquisition_axi build/ooc-proof-run \\
    \"sys_cpu_clk:10.000,sys_dma_clk:4.000\""
fi

REPO_ROOT="$(cd "$1" && pwd)"
MODULE_DIR_REL="$2"
TOP_ENTITY="$3"
RUN_ROOT="$4"
CLOCK_SPEC_CSV="$5"
CONSTRAINTS_CSV="${6:-}"
GENERICS_CSV="${7:-}"

[[ -d "$REPO_ROOT/$MODULE_DIR_REL" ]] || die "module directory not found: $REPO_ROOT/$MODULE_DIR_REL"
[[ "$RUN_ROOT" = /* ]] || RUN_ROOT="$REPO_ROOT/$RUN_ROOT"
[[ ! -e "$RUN_ROOT" ]] || die "run root already exists; choose a unique directory: $RUN_ROOT"
[[ -f "$SETTINGS" ]] || die "Vivado settings not found: $SETTINGS"

exec 9>"$LOCK_FILE"
flock -w "$LOCK_WAIT" 9 || die "timed out waiting for the build-class lock $LOCK_FILE"

cd "$REPO_ROOT"
# shellcheck disable=SC1090
source "$SETTINGS"
mkdir -p "$RUN_ROOT"
# -tclargs consumes EVERY remaining argument, so it must come last -- putting
# -log/-journal after it hands them to the script as argv and the arity
# check inside the .tcl fails instantly.
vivado -mode batch -notrace \
    -log "$RUN_ROOT/vivado.log" -journal "$RUN_ROOT/vivado.jou" \
    -source "$SCRIPT_DIR/run_wrapper_ooc.tcl" \
    -tclargs "$REPO_ROOT" "$MODULE_DIR_REL" "$TOP_ENTITY" "$RUN_ROOT" \
              "$CLOCK_SPEC_CSV" "$CONSTRAINTS_CSV" "$GENERICS_CSV"
