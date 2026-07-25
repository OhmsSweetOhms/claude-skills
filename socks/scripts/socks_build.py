#!/usr/bin/env python3
"""
socks_build.py -- the `socks build <recipe>` driver (unified staged recipes).

One command, one recipe format, no code-path picking: the recipe IS the
build. A recipe is a profile's unified platforms/profiles/<p>/build-recipe.json
(schema_version 3, staged shape), schema-validated at load. Selection is
one-pointer (operator ruling 2026-07-25): an explicit recipe argument
ALWAYS wins; with no argument, socks.json::build.recipe in --project-dir
is the per-project default. There is no active_profile machinery and no
mismatch to police -- a path cannot disagree with itself.

Modes:
  --plan      (default) Emit the exact commands + tree state per stage.
              No Vivado license needed -- the Codex handback form.
  --execute   Run on a licensed host (HDL/no-OS wraps the proven
              apply_recipe + Stage-14 engine; Linux is a defined-interface
              stub until plan-03 ports the cold_rebuild stages + gates).
  --describe  Human-readable "how is this profile built": identity, pins,
              toolchain, operating point + bands, stages, verification --
              including a live mxfe_rate_math re-derivation of every NCO word.

Stages: --stage hdl|linux|all (default all).

Distinct from build.py (single-module clean-and-rebuild pipeline).
Origin thread: cross-cutting/20260703-socks-canonical-build-driver.
"""

import argparse
import hashlib
import json
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(SCRIPT_DIR, "hil"))

from adi_profile_apply import load_recipe, apply_recipe, _repo_root  # noqa: E402
from mxfe_rate_math import verify_operating_point  # noqa: E402


# ---------------------------------------------------------------- helpers

def resolve(args):
    """Resolve the recipe (explicit path wins; else project default)."""
    project_dir = os.path.abspath(args.project_dir) if args.project_dir else os.getcwd()
    recipe_abs, recipe = load_recipe(project_dir, recipe_path=args.recipe)
    return project_dir, recipe_abs, recipe


def stage_selected(args, stage):
    return args.stage in ("all", stage)


# ------------------------------------------------------------------- plan

def plan_hdl(recipe, recipe_abs, project_dir):
    stage = recipe["stages"]["hdl_no_os"]
    pin = recipe["upstream_pin"]
    print(f"# stage hdl_no_os : materialize -> patch -> make")
    print(f"# upstream pin: hdl {pin['hdl_repo']} @ {pin['hdl_sha']}")
    print(f"#               no-OS {pin['no_os_repo']} @ {pin['no_os_sha']}")
    for kind in ("hdl", "no_os"):
        for e in stage["patches"][kind]:
            print(f"#    {kind} patch: {e['file']} -> {e['applies_to']}")
    print(f"python3 {os.path.join(SCRIPT_DIR, 'hil', 'adi_profile_apply.py')} "
          f"--project-dir {project_dir} --recipe {recipe_abs}")
    print(f"# build (Stage-14 contract; version pin derived from the toolchain slot)")
    print(f"export REQUIRED_VIVADO_VERSION={recipe['toolchain']['vivado']}")
    for k, v in sorted(stage["build_env"].items()):
        print(f"export {k}={v}")
    print(f'source "<Vivado-{recipe["toolchain"]["vivado"]} settings64.sh>" && '
          f'make -C "<adi_root>/{stage["hdl_project"]}"')


def plan_linux(recipe):
    lx = recipe["stages"]["linux"]
    print(f"# stage linux : kernel -> dt -> boot assembly")
    k = lx["kernel"]
    print(f"# kernel {k['release']} from {k['source']['repo']} @ {k['source']['commit']}")
    for c in k["commands"]:
        print(c)
    print(f"# dt: {lx['dt']['source']} -> {lx['dt']['dtb']}")
    if "pl" in lx:
        print(f"# pl: from stage hdl_no_os (internal edge)")
    else:
        print(f"# pl_source: pinned external derivation -- see recipe stages.linux.pl_source")
    ba = lx["boot_assembly"]
    print(f"# boot assembly (verbatim; stage_d placeholder substitution contract):")
    print(ba["command"])
    print(f"# output: {ba['output']}")


# --------------------------------------------------------------- describe

def describe(recipe, recipe_abs):
    op = recipe["operating_point"]
    tc = recipe["toolchain"]
    pin = recipe["upstream_pin"]
    st = recipe["stages"]
    print(f"{recipe['name']}  [{recipe['board']} / {recipe['device']}]")
    print(f"  {recipe['description']}")
    print(f"  recipe:    {recipe_abs}")
    print(f"  toolchain: vivado {tc['vivado']} · vitis {tc['vitis']} · "
          f"bootgen {tc['bootgen']} · xsct {tc['xsct']}")
    print(f"  pins:      hdl @ {pin['hdl_sha'][:12]}  no-OS @ {pin['no_os_sha'][:12]}")
    print(f"  jesd:      {op['jesd_variant']} {op['encoding']}")
    rx, tx = op.get("rx", {}), op.get("tx", {})
    if "adc_frequency_hz" in rx:
        print(f"  rx:        ADC {rx['adc_frequency_hz']/1e9:.5f} GHz, "
              f"decim {rx.get('main_decimation','?')}x{rx.get('channel_decimation','?')}, "
              f"{len(rx.get('bands',[]))} bands")
        for b in rx.get("bands", []):
            print(f"             {b['name']:16s} {b['center_hz']/1e6:10.3f} MHz  "
                  f"ftw_48 {b['ftw_48']}")
    if "dac_frequency_hz" in tx:
        print(f"  tx:        DAC {tx['dac_frequency_hz']/1e9:.5f} GHz, "
              f"main NCO {tx.get('main_nco_shift_hz',0)/1e6:.3f} MHz")
    print(f"  stage hdl_no_os: project {st['hdl_no_os']['hdl_project']}, "
          f"{len(st['hdl_no_os']['patches']['hdl'])} hdl + "
          f"{len(st['hdl_no_os']['patches']['no_os'])} no-os patches, "
          f"env {sorted(st['hdl_no_os']['build_env'])}")
    k = st["linux"]["kernel"]
    print(f"  stage linux:     kernel {k['release']}, "
          f"{len(k['required_patches'])} kernel patches, "
          f"boot -> {st['linux']['boot_assembly']['output']}")
    print(f"  verification:    {len(recipe['verification']['uart_pass_markers'])} UART pass markers")
    findings = verify_operating_point(op)
    if findings:
        for x in findings:
            print(f"  RATE-MATH FINDING: {x}")
        return 1
    print(f"  rate math: ALL NCO words re-derive bit-exact")
    return 0


# ---------------------------------------------------------------- execute

def execute_hdl(recipe_abs, project_dir):
    result = apply_recipe(project_dir, recipe_path=recipe_abs)
    if result.get("status") != "applied":
        raise SystemExit(f"ERROR: recipe apply did not complete: {result}")
    from hil_project import run_adi_make_stage14, find_vivado_settings
    with open(os.path.join(project_dir, "socks.json")) as f:
        build_cfg = json.load(f).get("build", {})
    build_dir = os.path.join(project_dir, "build", "hil")
    rc = run_adi_make_stage14(project_dir, build_dir, build_cfg, find_vivado_settings())
    if rc != 0:
        raise SystemExit(rc)
    emit_sha256_manifest(build_dir)


def execute_linux(recipe):
    raise SystemExit("ERROR: Linux --execute is not implemented yet (plan-03: port the "
                     "cold_rebuild.py kernel/dt/boot stages + reproducibility gates). "
                     "Use --plan for the command contract, or platforms/tools/cold_rebuild.py.")


def emit_sha256_manifest(build_dir):
    exts = (".bit", ".xsa", ".dcp", ".elf")
    entries = []
    for dirpath, _dirnames, filenames in os.walk(build_dir):
        for name in filenames:
            if name.endswith(exts) or name == "BOOT.BIN":
                path = os.path.join(dirpath, name)
                digest = hashlib.sha256(open(path, "rb").read()).hexdigest()
                entries.append({"file": os.path.relpath(path, build_dir),
                                "sha256": digest})
    out = os.path.join(build_dir, "socks-build-artifacts.sha256.json")
    with open(out, "w") as f:
        json.dump({"artifacts": entries}, f, indent=2)
        f.write("\n")
    print(f"artifact manifest: {out} ({len(entries)} artifacts)")


# ------------------------------------------------------------------- main

def main():
    parser = argparse.ArgumentParser(
        description="Drive a build from a schema-validated unified recipe (the recipe IS the build)")
    parser.add_argument("recipe", nargs="?", default=None,
                        help="Recipe path (wins over socks.json::build.recipe)")
    parser.add_argument("--project-dir", default=None,
                        help="SOCKS project dir; source of the build.recipe default and of "
                             "adi_root/no_os_subtree for --execute")
    parser.add_argument("--stage", choices=["hdl", "linux", "all"], default="all")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--plan", dest="mode", action="store_const", const="plan", default="plan",
                      help="Emit exact commands + tree state (default; no license needed)")
    mode.add_argument("--execute", dest="mode", action="store_const", const="execute",
                      help="Run the build on a licensed host")
    mode.add_argument("--describe", dest="mode", action="store_const", const="describe",
                      help="Human-readable summary of how this profile is built")
    args = parser.parse_args()

    project_dir, recipe_abs, recipe = resolve(args)

    if args.mode == "describe":
        return describe(recipe, recipe_abs)

    if args.mode == "plan":
        print(f"# socks build --plan : {recipe['name']} (stage={args.stage})")
        if stage_selected(args, "hdl"):
            plan_hdl(recipe, recipe_abs, project_dir)
        if stage_selected(args, "linux"):
            plan_linux(recipe)
        return 0

    # execute
    if stage_selected(args, "hdl"):
        execute_hdl(recipe_abs, project_dir)
    if stage_selected(args, "linux"):
        execute_linux(recipe)
    return 0


if __name__ == "__main__":
    sys.exit(main())
