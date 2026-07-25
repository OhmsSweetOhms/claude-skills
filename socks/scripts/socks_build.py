#!/usr/bin/env python3
"""
socks_build.py -- the `socks build <recipe>` driver (plan-01 skeleton).

One command, one recipe format, no code-path picking: the recipe IS the
build. The recipe file must validate against
<socks-root>/platforms/schemas/build-recipe.schema.json, which admits
exactly two document shapes; the driver derives its branch from which
shape the recipe is:

  * ADI profile manifest (ADI/projects/<proj>/profiles/<prof>/manifest.json)
        -> HDL + no-OS branch (materialize -> patch -> make), reusing the
           proven adi_profile_apply.py + Stage-14 engine unchanged.
  * <profile>-linux-boot-chain-rebuild-recipe.json
        -> Linux boot-chain branch. --plan emits the recipe's verbatim
           assembly.command contract; --execute is a defined-interface
           stub until the Linux implementation hop (plan-02) lands.

Modes:
  --plan     (default) Emit the exact commands + tree state the build
             would run. No Vivado license needed -- this output is the
             Codex handback form.
  --execute  Run the build on a licensed host, then emit a SHA256
             manifest of the produced artifacts.

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

try:
    import jsonschema
except ImportError:
    print("ERROR: python3-jsonschema is required (schema validation is not optional).")
    sys.exit(2)

SCHEMA_REL = "platforms/schemas/build-recipe.schema.json"


# ---------------------------------------------------------------- loading

def load_and_validate(recipe_path, socks_root):
    """Parse the recipe and validate against the canonical schema.

    Fails loud at parse time -- a recipe that does not validate is not a
    recipe (CLAUDE.md Hard Requirement 5: no legacy shapes admitted).
    Returns (recipe_dict, branch) where branch is 'hdl_no_os' | 'linux_boot'.
    """
    schema_path = os.path.join(socks_root, SCHEMA_REL)
    if not os.path.isfile(schema_path):
        raise SystemExit(f"ERROR: schema not found: {schema_path}")
    with open(schema_path) as f:
        schema = json.load(f)
    with open(recipe_path) as f:
        recipe = json.load(f)

    validator = jsonschema.Draft7Validator(schema)
    errors = sorted(validator.iter_errors(recipe), key=lambda e: list(e.absolute_path))
    if errors:
        print(f"ERROR: {recipe_path} does not validate against {SCHEMA_REL}:")
        for err in errors[:10]:
            loc = "/".join(str(p) for p in err.absolute_path) or "<root>"
            print(f"  at {loc}: {err.message[:200]}")
        raise SystemExit(1)

    if str(recipe.get("artifact_kind", "")).endswith("-linux-boot-chain-rebuild-recipe"):
        return recipe, "linux_boot"
    return recipe, "hdl_no_os"


# ------------------------------------------------------- HDL/no-OS branch

def plan_hdl_no_os(recipe, recipe_path, socks_root, project_dir):
    """Emit the exact materialize/patch/make sequence the engine runs."""
    profile_dir = os.path.dirname(os.path.abspath(recipe_path))
    profile = os.path.basename(profile_dir)
    pin = recipe["upstream_pin"]
    env = dict(recipe.get("build_validation", {}).get("instrumented_env", {}))
    env.pop("note", None)

    adi_root, adi_project = "<adi_root>", recipe["hdl_project"]
    if project_dir:
        with open(os.path.join(project_dir, "socks.json")) as f:
            build_cfg = json.load(f).get("build", {})
        adi_root = build_cfg.get("adi_root", adi_root)
        adi_project = build_cfg.get("project_dir", adi_project)

    print(f"# socks build --plan : HDL + no-OS branch")
    print(f"# profile:      {profile}")
    print(f"# upstream pin: hdl {pin['hdl_repo']} @ {pin['hdl_sha']}")
    print(f"#               no-OS {pin['no_os_repo']} @ {pin['no_os_sha']}")
    print(f"# 1. materialize + patch (engine: scripts/hil/adi_profile_apply.py)")
    for entry in recipe["patches"]["hdl"]:
        print(f"#    hdl patch:   {entry['file']} -> {entry['applies_to']}")
    for entry in recipe["patches"]["no_os"]:
        print(f"#    no-os patch: {entry['file']} -> {entry['applies_to']}")
    print(f"python3 {os.path.join(SCRIPT_DIR, 'hil', 'adi_profile_apply.py')} "
          f"--project-dir {project_dir or '<socks-project-dir>'}")
    print(f"# 2. build (Stage-14 contract, executed verbatim)")
    for key, val in sorted(env.items()):
        print(f"export {key}={val}")
    print(f'source "<Vivado settings64.sh>" && make -C "{os.path.join(adi_root, adi_project)}"')


def execute_hdl_no_os(recipe, recipe_path, socks_root, project_dir):
    """Thin, faithful wrapper over the proven engine -- no behavior change."""
    if not project_dir:
        raise SystemExit("ERROR: --execute needs --project-dir (the SOCKS "
                         "project whose socks.json::adi.active_profile is this recipe's profile)")
    from adi_profile_apply import apply_active_profile

    profile = os.path.basename(os.path.dirname(os.path.abspath(recipe_path)))
    with open(os.path.join(project_dir, "socks.json")) as f:
        socks_cfg = json.load(f)
    active = socks_cfg.get("adi", {}).get("active_profile")
    if active != profile:
        raise SystemExit(f"ERROR: recipe profile '{profile}' is not the active profile "
                         f"'{active}' in {project_dir}/socks.json -- one authority, no overrides; "
                         f"update socks.json::adi.active_profile deliberately or use the right recipe")

    result = apply_active_profile(project_dir)
    if result.get("status") != "applied":
        raise SystemExit(f"ERROR: profile apply did not complete: {result}")

    # Stage-14 make: delegate to the existing HIL stage runner.
    from hil_project import run_adi_make_stage14, find_vivado_settings
    build_dir = os.path.join(project_dir, "build", "hil")
    rc = run_adi_make_stage14(project_dir, build_dir,
                              socks_cfg.get("build", {}), find_vivado_settings())
    if rc != 0:
        raise SystemExit(rc)
    emit_sha256_manifest(build_dir)


# ------------------------------------------------------ Linux boot branch

def plan_linux_boot(recipe, recipe_path):
    """Emit the boot-chain assembly contract; command is authoritative."""
    print(f"# socks build --plan : Linux boot-chain branch")
    print(f"# recipe:  {recipe['artifact_kind']}")
    print(f"# board:   {recipe['board']}   profile: {recipe['profile']}")
    print(f"# toolchain pins: " + ", ".join(
        f"{k}={v}" for k, v in sorted(recipe["toolchain"].items())))
    print(f"# inputs (must exist at their pinned paths):")
    for role, path in sorted(recipe["inputs"].items()):
        if role.endswith(("_note", "_provenance", "_command")):
            continue
        print(f"#    {role}: {path}")
    print(f"# assembly (verbatim; placeholder substitution per the "
          f"cold_rebuild.py stage_d contract):")
    print(recipe["assembly"]["command"])
    print(f"# output: {recipe['assembly']['output']}")


def execute_linux_boot(recipe, recipe_path):
    """Defined-interface stub: implementation is the plan-02 Codex hop.

    Interface (settled plan-01): three stages driven from this recipe +
    its input_manifests -- (a) kernel per Image.build-manifest.json pins,
    (b) device tree, (c) boot-package via assembly.command executed
    verbatim with the stage_d placeholder substitutions. Until plan-02
    lands, execute via platforms/tools/cold_rebuild.py.
    """
    raise SystemExit("ERROR: Linux --execute is not implemented yet (plan-02 hop). "
                     "Use --plan for the command contract, or platforms/tools/cold_rebuild.py.")


# ---------------------------------------------------------------- outputs

def emit_sha256_manifest(build_dir):
    """SHA256 every produced artifact so a build is checkably comparable."""
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
        description="Drive a build from a schema-validated recipe (the recipe IS the build)")
    parser.add_argument("recipe", help="Path to the recipe JSON")
    parser.add_argument("--socks-root", required=True,
                        help="socks monorepo checkout root (holds platforms/schemas/)")
    parser.add_argument("--project-dir", default=None,
                        help="SOCKS project dir (socks.json home); required for HDL/no-OS --execute")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--plan", action="store_true", default=True,
                      help="Emit exact commands + tree state (default; no license needed)")
    mode.add_argument("--execute", dest="plan", action="store_false",
                      help="Run the build on a licensed host")
    args = parser.parse_args()

    recipe, branch = load_and_validate(args.recipe, args.socks_root)
    if branch == "hdl_no_os":
        if args.plan:
            plan_hdl_no_os(recipe, args.recipe, args.socks_root, args.project_dir)
        else:
            execute_hdl_no_os(recipe, args.recipe, args.socks_root, args.project_dir)
    else:
        if args.plan:
            plan_linux_boot(recipe, args.recipe)
        else:
            execute_linux_boot(recipe, args.recipe)
    return 0


if __name__ == "__main__":
    sys.exit(main())
