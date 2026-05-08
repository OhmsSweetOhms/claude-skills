#!/usr/bin/env python3
"""Apply an ADI MxFE profile selected by socks.json::adi."""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime


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


def _run(cmd, cwd):
    result = subprocess.run(
        cmd,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"Command failed in {cwd}: {' '.join(cmd)}\n{result.stdout}")
    return result.stdout


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


def _profile_project_dir(profile_dir):
    # .../<project>/profiles/<profile-name>
    return os.path.abspath(os.path.join(profile_dir, os.pardir, os.pardir))


def _manifest_matches_build_project(manifest, manifest_path, build_project_dir):
    if not build_project_dir:
        return False
    normalized = build_project_dir.replace("\\", "/").strip("/")
    if normalized.startswith("projects/"):
        normalized = normalized[len("projects/"):]
    hdl_project = str(manifest.get("hdl_project", "")).replace("\\", "/").strip("/")
    if hdl_project == normalized:
        return True
    project_dir = _profile_project_dir(os.path.dirname(manifest_path))
    return project_dir.replace("\\", "/").endswith("/projects/" + normalized)


def find_profile_manifest(project_dir, socks_cfg):
    adi_cfg = socks_cfg.get("adi", {})
    active = adi_cfg.get("active_profile")
    if not active:
        raise ValueError("socks.json::adi.active_profile is required")

    matches = []
    for search in adi_cfg.get("profile_search_path", []):
        search_dir = _resolve_config_path(project_dir, search)
        manifest_path = os.path.join(search_dir, active, "manifest.json")
        if os.path.isfile(manifest_path):
            manifest = _load_json(manifest_path)
            matches.append((manifest_path, manifest))

    if not matches:
        raise FileNotFoundError(
            f"Active ADI profile '{active}' not found in profile_search_path")

    build_project_dir = socks_cfg.get("build", {}).get("project_dir")
    preferred = [
        item for item in matches
        if _manifest_matches_build_project(item[1], item[0], build_project_dir)
    ]
    if len(preferred) == 1:
        return preferred[0]
    if len(matches) == 1:
        return matches[0]

    choices = ", ".join(_rel(path, _repo_root(project_dir)) for path, _ in matches)
    raise ValueError(
        f"Profile '{active}' is ambiguous for build.project_dir="
        f"{build_project_dir!r}; matches: {choices}")


def _copy_pristine_hdl_files(hdl_project_dir, manifest):
    copied = []
    upstream_dir = os.path.join(hdl_project_dir, "upstream")
    if not os.path.isdir(upstream_dir):
        raise FileNotFoundError(f"HDL upstream directory not found: {upstream_dir}")

    for patch in manifest.get("patches", {}).get("hdl", []):
        rel = patch.get("applies_to")
        if not rel:
            raise ValueError("HDL patch entry missing applies_to")
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


def _apply_no_os_patches(no_os_build_root, no_os_subtree, manifest):
    applied = []
    patches_dir = os.path.join(no_os_subtree, "patches")
    for patch in manifest.get("patches", {}).get("no_os", []):
        patch_file = patch.get("file")
        if not patch_file:
            raise ValueError("no-OS patch entry missing file")
        patch_path = os.path.join(patches_dir, patch_file)
        if not os.path.isfile(patch_path):
            raise FileNotFoundError(f"no-OS patch not found: {patch_path}")
        output = _git_apply_to_dir(no_os_build_root, patch_path)
        applied.append({
            "file": patch_file,
            "sha256": _sha256(patch_path),
            "applies_to": patch.get("applies_to"),
            "output": output.strip(),
        })
    return applied


def _apply_hdl_patches(hdl_project_dir, profile_dir, manifest):
    applied = []
    for patch in manifest.get("patches", {}).get("hdl", []):
        patch_file = patch.get("file")
        if not patch_file:
            raise ValueError("HDL patch entry missing file")
        patch_path = os.path.join(profile_dir, patch_file)
        if not os.path.isfile(patch_path):
            raise FileNotFoundError(f"HDL patch not found: {patch_path}")
        output = _git_apply_to_dir(hdl_project_dir, patch_path)
        applied.append({
            "file": patch_file,
            "sha256": _sha256(patch_path),
            "applies_to": patch.get("applies_to"),
            "output": output.strip(),
        })
    return applied


def apply_active_profile(project_dir):
    """Apply the active ADI profile. Returns a structured result dict.

    Projects without socks.json::adi are a no-op.
    """
    project_dir = os.path.abspath(project_dir)
    root = _repo_root(project_dir)
    socks_path = os.path.join(project_dir, "socks.json")
    if not os.path.isfile(socks_path):
        raise FileNotFoundError(f"socks.json not found: {socks_path}")
    socks_cfg = _load_json(socks_path)
    adi_cfg = socks_cfg.get("adi")
    if not adi_cfg:
        return {"status": "skipped", "reason": "socks.json has no adi section"}

    manifest_path, manifest = find_profile_manifest(project_dir, socks_cfg)
    profile_dir = os.path.dirname(manifest_path)
    hdl_project_dir = _profile_project_dir(profile_dir)
    no_os_subtree = _resolve_config_path(project_dir, adi_cfg.get("no_os_subtree"))

    build_dir = os.path.join(project_dir, "build", "hil")
    state_dir = os.path.join(project_dir, "build", "state")
    os.makedirs(build_dir, exist_ok=True)
    os.makedirs(state_dir, exist_ok=True)

    print(f"  ADI profile: {adi_cfg['active_profile']}")
    print(f"  Manifest:    {_rel(manifest_path, root)}")
    print(f"  HDL project: {_rel(hdl_project_dir, root)}")

    copied_hdl = _copy_pristine_hdl_files(hdl_project_dir, manifest)
    no_os_build_root = _materialize_no_os(no_os_subtree, build_dir)
    no_os_patches = _apply_no_os_patches(no_os_build_root, no_os_subtree, manifest)
    hdl_patches = _apply_hdl_patches(hdl_project_dir, profile_dir, manifest)

    result = {
        "status": "applied",
        "timestamp": datetime.now().isoformat(),
        "active_profile": adi_cfg["active_profile"],
        "manifest_path": _rel(manifest_path, root),
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
    parser = argparse.ArgumentParser(description="Apply socks.json::adi active profile")
    parser.add_argument("--project-dir", required=True, help="SOCKS project root")
    args = parser.parse_args()
    try:
        result = apply_active_profile(args.project_dir)
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
