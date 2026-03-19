#!/usr/bin/env python3
"""
project_config.py -- Reader/writer for socks.json (project root).

socks.json stores design decisions from discovery that survive build/ clean:
  - name, scope, dut entity, board part/preset, sub-designs

Separate from build/state/project.json which holds ephemeral pipeline state.
"""

import json
import os
from typing import Dict, List, Optional


SOCKS_JSON = "socks.json"


def _socks_json_path(project_dir: str) -> str:
    return os.path.join(project_dir, SOCKS_JSON)


def load_project_config(project_dir: str) -> Optional[dict]:
    """Load socks.json from project root. Returns dict or None if missing."""
    path = _socks_json_path(project_dir)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def create_project_config(
    project_dir: str,
    name: str,
    scope: str,
    part: Optional[str] = None,
    preset: Optional[str] = None,
    entity: Optional[str] = None,
) -> dict:
    """Create socks.json in project root. Returns the config dict."""
    config = {
        "name": name,
        "scope": scope,
        "dut": {},
        "board": {},
        "sub_designs": [],
    }
    if entity:
        config["dut"]["entity"] = entity
    if part:
        config["board"]["part"] = part
    if preset:
        config["board"]["preset"] = preset

    path = _socks_json_path(project_dir)
    with open(path, "w") as f:
        json.dump(config, f, indent=4)
        f.write("\n")
    return config


def update_project_config(project_dir: str, updates: dict) -> Optional[dict]:
    """Merge updates into existing socks.json. Returns updated config or None."""
    config = load_project_config(project_dir)
    if config is None:
        return None

    for key, val in updates.items():
        if isinstance(val, dict) and isinstance(config.get(key), dict):
            config[key].update(val)
        else:
            config[key] = val

    path = _socks_json_path(project_dir)
    with open(path, "w") as f:
        json.dump(config, f, indent=4)
        f.write("\n")
    return config


def get_scope(project_dir: str) -> Optional[str]:
    """Shorthand: return scope string from socks.json, or None."""
    config = load_project_config(project_dir)
    if config:
        return config.get("scope")
    return None


def get_part(project_dir: str) -> Optional[str]:
    """Shorthand: return board.part from socks.json, or None."""
    config = load_project_config(project_dir)
    if config:
        return config.get("board", {}).get("part")
    return None


def get_entity(project_dir: str) -> Optional[str]:
    """Shorthand: return dut.entity from socks.json, or None."""
    config = load_project_config(project_dir)
    if config:
        return config.get("dut", {}).get("entity")
    return None
