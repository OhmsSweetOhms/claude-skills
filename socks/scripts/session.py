#!/usr/bin/env python3
"""
session.py -- Session manifest read/write helpers for the SOCKS pipeline.

The session manifest (build/logs/session.json) tracks every stage run
across a pipeline session, including both scripted and guidance stages.

Shared by socks.py and log_stage.py (legacy path).
"""

import json
import os
import tempfile
from datetime import datetime


def _session_path(project_dir):
    """Return the canonical session.json path for a project."""
    return os.path.join(project_dir, "build", "logs", "session.json")


def _new_session(project_dir, max_iterations=0):
    """Create a fresh session manifest dict.

    max_iterations: design-loop iteration cap (0 = unlimited).
    """
    return {
        "session_id": datetime.now().strftime("%Y%m%d_%H%M%S"),
        "project": project_dir,
        "max_iterations": max_iterations,
        "stages": [],
    }


def _atomic_write(path, data):
    """Write JSON atomically: write to temp file, then rename."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(
        dir=os.path.dirname(path), suffix=".tmp", prefix=".session_")
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


def load_session(project_dir):
    """Load session.json, or return None if it doesn't exist."""
    path = _session_path(project_dir)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def create_session(project_dir, max_iterations=0):
    """Create a new session.json, overwriting any existing one.

    max_iterations: design-loop iteration cap (0 = unlimited).
    """
    session = _new_session(project_dir, max_iterations=max_iterations)
    _atomic_write(_session_path(project_dir), session)
    return session


def iterations_exhausted(project_dir):
    """Check if the design-loop iteration cap has been reached.

    Returns True if max_iterations > 0 and any design-loop stage (2-9)
    has been run max_iterations times. Returns False if cap is 0 (unlimited)
    or not yet reached.
    """
    session = load_session(project_dir)
    if session is None:
        return False
    cap = session.get("max_iterations", 0)
    if cap <= 0:
        return False
    # Check design-loop stages (2-9)
    for stage_num in range(2, 10):
        count = sum(1 for e in session["stages"] if e["stage"] == stage_num)
        if count >= cap:
            return True
    return False


def append_session_entry(project_dir, stage_num, status, source,
                         note=None, files=None, log_file=None):
    """Append a stage entry to session.json (read-modify-write).

    If session.json doesn't exist, creates one with a new session_id.
    The iteration field auto-increments per stage number.
    """
    path = _session_path(project_dir)
    session = load_session(project_dir)
    if session is None:
        session = _new_session(project_dir)

    # Compute iteration: count prior entries for this stage number
    prior = sum(1 for e in session["stages"] if e["stage"] == stage_num)
    iteration = prior + 1

    entry = {
        "stage": stage_num,
        "time": datetime.now().strftime("%H:%M:%S"),
        "status": status,
        "source": source,
        "note": note,
        "files": files or [],
        "iteration": iteration,
        "log_file": log_file,
    }
    session["stages"].append(entry)
    _atomic_write(path, session)
    return entry
