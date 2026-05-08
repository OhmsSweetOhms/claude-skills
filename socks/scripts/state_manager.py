#!/usr/bin/env python3
"""
state_manager.py -- Project state file manager for the SOCKS pipeline.

Manages build/state/project.json as the single source of truth for:
  - Project metadata (name, scope, workflow)
  - Stage results (status, duration, outputs)
  - Input hashes (src/, tb/, docs/, sw/) for incremental detection
  - Next-action suggestions

Replaces session.py for migrated / new projects.  Old projects without
build/state/project.json continue using session.py (legacy path).
"""

import hashlib
import json
import os
import tempfile
from datetime import datetime
from typing import Dict, List, Optional, Tuple


# Directories to hash and their re-entry stages (priority order)
# When multiple directories change, the earliest re-entry stage wins.
HASH_DIRS = {
    "docs": 1,   # architecture / design intent changed -> Stage 1
    "src":  4,   # RTL changed -> Stage 4 (simulation)
    "tb":   4,   # testbench changed -> Stage 4
    "sw":   7,   # C driver changed -> Stage 7 (SV/Xsim uses DPI-C)
}

# Single files to hash and their re-entry stages.
HASH_FILES = {
    "hil.json": 14,  # HIL config changed -> Stage 14 (re-create Vivado project)
}


def _state_path(project_dir: str) -> str:
    """Canonical path for project.json."""
    return os.path.join(project_dir, "build", "state", "project.json")


def _atomic_write(path: str, data: dict) -> None:
    """Write JSON atomically: temp file then rename."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(
        dir=os.path.dirname(path), suffix=".tmp", prefix=".state_")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=4)
            f.write("\n")
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


class StateManager:
    """Read / write build/state/project.json."""

    def __init__(self, project_dir: str):
        self.project_dir = os.path.abspath(project_dir)
        self.state_file = _state_path(self.project_dir)
        self._state: Optional[dict] = None

    # ------------------------------------------------------------------
    # Load / save
    # ------------------------------------------------------------------

    def load(self) -> Optional[dict]:
        """Load project.json. Returns None if missing or corrupt."""
        if self._state is not None:
            return self._state
        if not os.path.isfile(self.state_file):
            return None
        try:
            with open(self.state_file, "r") as f:
                self._state = json.load(f)
            return self._state
        except (json.JSONDecodeError, OSError):
            return None

    def save(self) -> None:
        """Persist current in-memory state to disk."""
        if self._state is None:
            return
        self._state["project"]["timestamp_last_modified"] = \
            datetime.now().isoformat()
        _atomic_write(self.state_file, self._state)

    def exists(self) -> bool:
        """True if project.json exists on disk."""
        return os.path.isfile(self.state_file)

    # ------------------------------------------------------------------
    # Initialisation
    # ------------------------------------------------------------------

    def ensure_state(self, name: Optional[str] = None,
                     scope: Optional[str] = None,
                     workflow: Optional[str] = None) -> dict:
        """Load existing state or create a fresh one.

        Safe to call every run -- only creates if missing.
        """
        state = self.load()
        if state is not None:
            # Migrate v1 format: "project" was a string, now a dict
            if isinstance(state.get("project"), str):
                old_name = state["project"]
                state["project"] = {
                    "name": old_name,
                    "scope": scope,
                    "last_workflow": workflow,
                    "timestamp_last_modified": datetime.now().isoformat(),
                }
                state["version"] = 2
            # Update workflow / scope if provided
            if workflow:
                state["project"]["last_workflow"] = workflow
            if scope:
                state["project"]["scope"] = scope
            self._state = state
            self.save()
            return state

        self._state = {
            "version": 2,
            "project": {
                "name": name or os.path.basename(self.project_dir),
                "scope": scope,
                "last_workflow": workflow,
                "timestamp_last_modified": datetime.now().isoformat(),
            },
            "design_intent": {
                "intent_file": None,
                "scope": scope,
                "status": None,
            },
            "stages": {},
            "inputs_hash": {},
            "next_action": None,
        }
        self.save()
        return self._state

    # ------------------------------------------------------------------
    # Hash computation
    # ------------------------------------------------------------------

    @staticmethod
    def _hash_file(filepath: str) -> str:
        """SHA-256 of a single file's contents."""
        h = hashlib.sha256()
        try:
            with open(filepath, "rb") as f:
                while True:
                    chunk = f.read(65536)
                    if not chunk:
                        break
                    h.update(chunk)
        except (OSError, PermissionError):
            pass
        return h.hexdigest()

    def compute_dir_hash(self, rel_dir: str) -> Optional[str]:
        """SHA-256 over all files in a project sub-directory.

        Files are sorted by relative path for determinism.
        Returns None if the directory doesn't exist.
        """
        abs_dir = os.path.join(self.project_dir, rel_dir)
        if not os.path.isdir(abs_dir):
            return None

        h = hashlib.sha256()
        found = False
        for root, _dirs, files in os.walk(abs_dir):
            for fname in sorted(files):
                fpath = os.path.join(root, fname)
                rel = os.path.relpath(fpath, abs_dir)
                # Include the path in the hash so renames are detected
                h.update(rel.encode("utf-8"))
                h.update(b"\x00")
                h.update(self._hash_file(fpath).encode("utf-8"))
                found = True

        return h.hexdigest() if found else None

    def compute_file_hash(self, rel_file: str) -> Optional[str]:
        """SHA-256 of a single tracked file. Returns None if missing."""
        abs_file = os.path.join(self.project_dir, rel_file)
        if not os.path.isfile(abs_file):
            return None
        return self._hash_file(abs_file)

    def compute_all_hashes(self) -> Dict[str, Optional[str]]:
        """Compute hashes for all tracked project directories and files."""
        hashes = {d: self.compute_dir_hash(d) for d in HASH_DIRS}
        for f in HASH_FILES:
            hashes[f] = self.compute_file_hash(f)
        return hashes

    # ------------------------------------------------------------------
    # Change detection
    # ------------------------------------------------------------------

    def detect_changes(self) -> Tuple[Dict[str, bool], Optional[int]]:
        """Compare current hashes against stored hashes.

        Returns:
            changed: dict of {name: True/False} for each tracked dir/file
            re_entry_stage: earliest stage to re-enter, or None if cached
        """
        state = self.load()
        if state is None:
            # No state = first run, everything is "changed"
            all_keys = dict(HASH_DIRS)
            all_keys.update(HASH_FILES)
            return {d: True for d in all_keys}, 0

        stored = state.get("inputs_hash", {})
        current = self.compute_all_hashes()

        changed = {}
        re_entry = None

        # Combine HASH_DIRS and HASH_FILES for unified iteration
        all_tracked = dict(HASH_DIRS)
        all_tracked.update(HASH_FILES)

        for name, stage in sorted(all_tracked.items(), key=lambda x: x[1]):
            cur = current.get(name)
            sto = stored.get(name)

            if cur is None and sto is None:
                changed[name] = False
            elif cur != sto:
                changed[name] = True
                if re_entry is None or stage < re_entry:
                    re_entry = stage
            else:
                changed[name] = False

        return changed, re_entry

    # ------------------------------------------------------------------
    # Stage updates
    # ------------------------------------------------------------------

    def update_stage(self, stage_num: int, status: str,
                     duration_seconds: Optional[float] = None,
                     source: str = "script",
                     note: Optional[str] = None,
                     **extra) -> None:
        """Record a stage result in the state file.

        status: "PASS", "FAIL", "SKIP", "VIOLATED", "UNKNOWN"
        extra: arbitrary keys like test_results, timing_results, utilization
        """
        state = self.load()
        if state is None:
            return

        entry = {
            "name": extra.pop("name", f"Stage {stage_num}"),
            "status": status.upper(),
            "timestamp": datetime.now().isoformat(),
            "source": source,
        }
        if duration_seconds is not None:
            entry["duration_seconds"] = round(duration_seconds, 2)
        if note:
            entry["note"] = note
        entry.update(extra)

        state["stages"][str(stage_num)] = entry
        self._state = state
        self.save()

    # ------------------------------------------------------------------
    # Hash persistence
    # ------------------------------------------------------------------

    def update_hashes(self) -> Dict[str, Optional[str]]:
        """Recompute and store current hashes. Returns the new hashes."""
        state = self.load()
        if state is None:
            return {}
        hashes = self.compute_all_hashes()
        state["inputs_hash"] = hashes
        self._state = state
        self.save()
        return hashes

    # ------------------------------------------------------------------
    # Next action
    # ------------------------------------------------------------------

    def set_next_action(self, suggested: str,
                        blocked_stages: Optional[List[int]] = None,
                        can_retry_from: Optional[int] = None) -> None:
        """Update the next-action suggestion."""
        state = self.load()
        if state is None:
            return
        state["next_action"] = {
            "suggested": suggested,
            "blocked_stages": blocked_stages or [],
            "can_retry_from": can_retry_from,
        }
        self._state = state
        self.save()

    def clear_next_action(self) -> None:
        """Clear the next-action field (all stages passed)."""
        state = self.load()
        if state is None:
            return
        state["next_action"] = None
        self._state = state
        self.save()

    # ------------------------------------------------------------------
    # Hardware capabilities
    # ------------------------------------------------------------------

    def set_hardware_capabilities(self, jtag_detected: bool,
                                  uart_detected: bool,
                                  uart_port: Optional[str] = None,
                                  jtag_target: Optional[str] = None,
                                  uart_candidates: Optional[List[dict]] = None) -> None:
        """Persist hardware detection results from Stage 0."""
        state = self.load()
        if state is None:
            return
        state["hardware"] = {
            "jtag_detected": jtag_detected,
            "uart_detected": uart_detected,
            "uart_port": uart_port,
            "uart_candidates": uart_candidates or [],
            "jtag_target": jtag_target,
            "timestamp": datetime.now().isoformat(),
        }
        self._state = state
        self.save()

    def get_hardware_capabilities(self) -> Optional[dict]:
        """Read hardware capabilities. Returns None if never probed."""
        state = self.load()
        if state is None:
            return None
        return state.get("hardware")
