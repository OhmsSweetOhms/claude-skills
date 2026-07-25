#!/usr/bin/env python3
"""Apply the MxFE build recipe named by socks.json::build.recipe.

One-pointer contract (operator ruling 2026-07-25, gps_design thread
cross-cutting/20260703-socks-canonical-build-driver): the retired
socks.json::adi selection block (active_profile + profile_search_path +
build.project_dir disambiguation) is replaced by a single build.recipe
path to the profile's unified platforms/profiles/<p>/build-recipe.json.
The recipe is schema-validated at load (fail loud at parse time) and its
stages.hdl_no_os section drives materialize -> patch: pristine HDL files
are restored from <hdl_project>/upstream/, then the recipe's explicit
repo-relative patch paths are applied (no implicit join conventions).

The state file build/state/adi-profile-apply.json keeps its historical
key names (active_profile / manifest_path / ...) -- hil_run's UART
marker config and bench tools consume that contract; manifest_path now
points at the unified recipe.
"""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime

RECIPE_SCHEMA_REL = "platforms/schemas/build-recipe.schema.json"


def _repo_root(project_dir):
    try:
        result = subprocess.run(
            ["git", "-C", project_dir, "rev-parse", "--show-toplevel"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            check=True,
        )
        return os.path.abspath(result.stdout.strip())
    except subprocess.CalledProcessError:
        return os.path.abspath(project_dir)


def _rel(path, base):
    try:
        return os.path.relpath(path, base)
    except ValueError:
        return path


def _load_json(path):
    with open(path, "r") as f:
        return json.load(f)


def _resolve_config_path(project_dir, value, must_exist=True):
    if not value:
        return None
    if os.path.isabs(value):
        return os.path.abspath(value)

    root = _repo_root(project_dir)
    candidates = [
        os.path.abspath(os.path.join(project_dir, value)),
        os.path.abspath(os.path.join(root, value)),
    ]
    for candidate in candidates:
        if os.path.exists(candidate):
            return candidate
    if must_exist:
        raise FileNotFoundError(
            f"Configured path does not exist relative to project or repo root: {value}")
    return candidates[-1]


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _git_apply_to_dir(target_dir, patch_path):
    repo = _repo_root(target_dir)
    prefix = os.path.relpath(target_dir, repo)
    cmd = ["git", "-C", repo, "apply", "--whitespace=nowarn"]
    if prefix != ".":
        cmd.append(f"--directory={prefix}")
    cmd.append(patch_path)
    result = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"Patch failed for {target_dir}: {patch_path}\n{result.stdout}")
    return result.stdout


def load_recipe(project_dir, socks_cfg=None, recipe_path=None):
    """Resolve, load, and schema-validate the unified build recipe.

    Explicit recipe_path wins; otherwise socks.json::build.recipe is the
    per-project default. Returns (recipe_abspath, recipe_dict).
    """
    if recipe_path is None:
        if socks_cfg is None:
            socks_cfg = _load_json(os.path.join(project_dir, "socks.json"))
        recipe_path = socks_cfg.get("build", {}).get("recipe")
        if not recipe_path:
            raise ValueError(
                "socks.json::build.recipe is required (the adi selection block is "
                "retired -- one-pointer ruling 2026-07-25; point build.recipe at "
                "platforms/profiles/<profile>/build-recipe.json)")
    recipe_abs = _resolve_config_path(project_dir, recipe_path)
    recipe = _load_json(recipe_abs)

    recipe_repo = _repo_root(os.path.dirname(recipe_abs))
    schema_path = os.path.join(recipe_repo, RECIPE_SCHEMA_REL)
    try:
        import jsonschema
    except ImportError as exc:
        raise RuntimeError("python3-jsonschema is required: recipe validation is not optional") from exc
    if not os.path.isfile(schema_path):
        raise FileNotFoundError(f"recipe schema not found: {schema_path}")
    jsonschema.validate(recipe, _load_json(schema_path))
    return recipe_abs, recipe


def _copy_pristine_hdl_files(hdl_project_dir, stage):
    copied = []
    upstream_dir = os.path.join(hdl_project_dir, "upstream")
    if not os.path.isdir(upstream_dir):
        raise FileNotFoundError(f"HDL upstream directory not found: {upstream_dir}")

    for patch in stage["patches"]["hdl"]:
        rel = patch["applies_to"]
        src = os.path.join(upstream_dir, rel)
        dst = os.path.join(hdl_project_dir, rel)
        if not os.path.isfile(src):
            raise FileNotFoundError(f"HDL pristine source missing: {src}")
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)
        copied.append(dst)
    return copied


def _materialize_no_os(no_os_subtree, build_dir):
    upstream = os.path.join(no_os_subtree, "upstream")
    if not os.path.isdir(upstream):
        raise FileNotFoundError(f"no-OS upstream directory not found: {upstream}")

    work_dir = os.path.join(no_os_subtree, "work")
    dst = os.path.join(work_dir, "active")
    os.makedirs(work_dir, exist_ok=True)
    if os.path.commonpath([os.path.abspath(no_os_subtree), os.path.abspath(dst)]) != os.path.abspath(no_os_subtree):
        raise ValueError(f"Refusing to materialize no-OS outside subtree: {dst}")
    if os.path.isdir(dst):
        shutil.rmtree(dst)
    shutil.copytree(
        upstream,
        dst,
        ignore=shutil.ignore_patterns("build", "tmp", ".git", "__pycache__"),
    )
    return dst


def _apply_patch_series(target_dir, repo_root, entries):
    """Apply recipe patch entries; each entry.file is repo-relative."""
    applied = []
    for patch in entries:
        patch_path = os.path.join(repo_root, patch["file"])
        if not os.path.isfile(patch_path):
            raise FileNotFoundError(f"recipe-declared patch not found: {patch_path}")
        output = _git_apply_to_dir(target_dir, patch_path)
        applied.append({
            "file": patch["file"],
            "sha256": _sha256(patch_path),
            "applies_to": patch.get("applies_to"),
            "output": output.strip(),
        })
    return applied


def apply_recipe(project_dir, recipe_path=None):
    """Materialize + patch per the unified recipe. Returns a result dict.

    Projects without socks.json::build.recipe (and no explicit path) are
    a no-op skip, mirroring the old adi-absent behavior for plain modules.
    """
    project_dir = os.path.abspath(project_dir)
    root = _repo_root(project_dir)
    socks_path = os.path.join(project_dir, "socks.json")
    if not os.path.isfile(socks_path):
        raise FileNotFoundError(f"socks.json not found: {socks_path}")
    socks_cfg = _load_json(socks_path)
    build_cfg = socks_cfg.get("build", {})
    if recipe_path is None and not build_cfg.get("recipe"):
        return {"status": "skipped", "reason": "socks.json has no build.recipe"}

    recipe_abs, recipe = load_recipe(project_dir, socks_cfg, recipe_path)
    recipe_repo = _repo_root(os.path.dirname(recipe_abs))
    stage = recipe["stages"]["hdl_no_os"]

    adi_root = _resolve_config_path(project_dir, build_cfg.get("adi_root"))
    if not adi_root or not build_cfg.get("project_dir"):
        raise ValueError("socks.json build.adi_root and build.project_dir are required for adi_make")
    hdl_project_dir = os.path.abspath(os.path.join(adi_root, build_cfg["project_dir"]))
    no_os_subtree = _resolve_config_path(project_dir, build_cfg.get("no_os_subtree"))
    if not no_os_subtree:
        raise ValueError("socks.json build.no_os_subtree is required (e.g. 'ADI/no-OS/')")

    build_dir = os.path.join(project_dir, "build", "hil")
    state_dir = os.path.join(project_dir, "build", "state")
    os.makedirs(build_dir, exist_ok=True)
    os.makedirs(state_dir, exist_ok=True)

    print(f"  Recipe:      {_rel(recipe_abs, root)} ({recipe['name']})")
    print(f"  HDL project: {_rel(hdl_project_dir, root)}")

    copied_hdl = _copy_pristine_hdl_files(hdl_project_dir, stage)
    no_os_build_root = _materialize_no_os(no_os_subtree, build_dir)
    no_os_patches = _apply_patch_series(no_os_build_root, recipe_repo, stage["patches"]["no_os"])
    hdl_patches = _apply_patch_series(hdl_project_dir, recipe_repo, stage["patches"]["hdl"])

    result = {
        "status": "applied",
        "timestamp": datetime.now().isoformat(),
        "active_profile": recipe["name"],
        "manifest_path": _rel(recipe_abs, root),
        "hdl_project_dir": _rel(hdl_project_dir, root),
        "no_os_subtree": _rel(no_os_subtree, root),
        "no_os_build_root": no_os_build_root,
        "copied_hdl_files": [_rel(path, root) for path in copied_hdl],
        "patches": {
            "no_os": no_os_patches,
            "hdl": hdl_patches,
        },
    }
    state_path = os.path.join(state_dir, "adi-profile-apply.json")
    with open(state_path, "w") as f:
        json.dump(result, f, indent=2)
        f.write("\n")
    print(f"  State:       {_rel(state_path, root)}")
    return result


def main():
    parser = argparse.ArgumentParser(description="Apply the socks.json::build.recipe build recipe")
    parser.add_argument("--project-dir", required=True, help="SOCKS project root")
    parser.add_argument("--recipe", default=None,
                        help="Explicit recipe path (wins over socks.json::build.recipe)")
    args = parser.parse_args()
    try:
        result = apply_recipe(args.project_dir, recipe_path=args.recipe)
    except Exception as e:
        print(f"ERROR: {e}")
        return 1
    print(json.dumps({k: v for k, v in result.items() if k != "patches"}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
