#!/usr/bin/env python3
"""
status.py -- Project status dashboard for SOCKS projects.

Reads socks.json, scans directory structure, parses build reports, and
checks project.json pipeline state. Produces a text-based terminal
dashboard. Runs automatically after Stage 0 passes, or standalone.

Exit codes: 0 = all pass, 2 = warnings only. Never blocks the pipeline.
"""

import argparse
import glob
import json
import os
import subprocess
import sys

# ---------------------------------------------------------------------------
# Bootstrap: add the SOCKS scripts directory to sys.path
# ---------------------------------------------------------------------------
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

from socks_lib import (print_header, print_separator, pass_str, fail_str,
                        yellow, bold, green, red,
                        parse_timing_report, parse_utilization_report)
from project_config import load_project_config, get_scope, get_part, get_entity
from state_manager import StateManager
from session import load_session

# ---------------------------------------------------------------------------
# Local print helper
# ---------------------------------------------------------------------------

def print_status(name, level, detail=""):
    """Print a status line with detail shown for all levels.

    level: 'PASS', 'WARN', 'FAIL', 'INFO'
    """
    tags = {
        "PASS": green("PASS"),
        "WARN": yellow("WARN"),
        "FAIL": red("FAIL"),
        "INFO": bold("INFO"),
    }
    tag = tags.get(level, level)
    detail_str = f"  {detail}" if detail else ""
    print(f"  [{tag}] {name:<25s}{detail_str}")


# ---------------------------------------------------------------------------
# Check: Config (socks.json)
# ---------------------------------------------------------------------------

def check_config(project_dir):
    """Validate socks.json fields. Returns (warns, fails)."""
    warns, fails = 0, 0
    cfg = load_project_config(project_dir)
    if cfg is None:
        print_status("socks.json", "FAIL", "missing or invalid")
        return 0, 1

    # name
    name = cfg.get("name")
    if name:
        print_status("name", "PASS", name)
    else:
        print_status("name", "FAIL", "missing")
        fails += 1

    # scope
    scope = cfg.get("scope")
    valid_scopes = ("module", "block", "system")
    if scope in valid_scopes:
        print_status("scope", "PASS", scope)
    elif scope:
        print_status("scope", "FAIL", f"'{scope}' not in {valid_scopes}")
        fails += 1
    else:
        print_status("scope", "FAIL", "missing")
        fails += 1

    # board.part
    part = cfg.get("board", {}).get("part")
    if part:
        print_status("board.part", "PASS", part)
    else:
        print_status("board.part", "WARN", "missing")
        warns += 1

    # board.preset
    preset = cfg.get("board", {}).get("preset")
    if preset:
        # Check if board reference directory exists
        refs_dir = os.path.join(SCRIPT_DIR, "..", "references", "boards", preset)
        if os.path.isdir(refs_dir):
            print_status("board.preset", "PASS", f"{preset} (board ref found)")
        else:
            print_status("board.preset", "WARN", f"{preset} (board ref not found)")
            warns += 1

    # dut.entity
    entity = cfg.get("dut", {}).get("entity")
    if entity:
        print_status("dut.entity", "PASS", entity)
    else:
        print_status("dut.entity", "WARN", "needed for synthesis")
        warns += 1

    return warns, fails


# ---------------------------------------------------------------------------
# Check: Directory (scope-aware)
# ---------------------------------------------------------------------------

def _count_files(directory, pattern):
    """Count files matching a glob pattern in a directory."""
    return len(glob.glob(os.path.join(directory, pattern)))


def check_directory(project_dir):
    """Check directory structure based on project scope. Returns (warns, fails)."""
    warns, fails = 0, 0
    scope = get_scope(project_dir) or "module"

    if scope == "system":
        # build/synth/
        synth_dir = os.path.join(project_dir, "build", "synth")
        if os.path.isdir(synth_dir):
            tcl_count = _count_files(synth_dir, "*.tcl")
            xsa_count = _count_files(synth_dir, "*.xsa")
            rpt_count = _count_files(synth_dir, "*.rpt")
            parts = []
            if tcl_count:
                parts.append(f"{tcl_count} TCL")
            if xsa_count:
                parts.append(f"{xsa_count} XSA")
            if rpt_count:
                parts.append(f"{rpt_count} reports")
            print_status("build/synth/", "PASS", ", ".join(parts) if parts else "empty")
        else:
            print_status("build/synth/", "FAIL", "missing")
            fails += 1

        # constraints/
        constr_dir = os.path.join(project_dir, "constraints")
        if os.path.isdir(constr_dir):
            xdc_count = _count_files(constr_dir, "*.xdc")
            print_status("constraints/", "PASS" if xdc_count else "WARN",
                         f"{xdc_count} XDC file{'s' if xdc_count != 1 else ''}")
            if not xdc_count:
                warns += 1
        else:
            print_status("constraints/", "WARN", "missing")
            warns += 1
    else:
        # Module/block scope: src/*.vhd
        src_dir = os.path.join(project_dir, "src")
        if os.path.isdir(src_dir):
            vhd_count = _count_files(src_dir, "*.vhd")
            print_status("src/", "PASS" if vhd_count else "WARN",
                         f"{vhd_count} VHDL file{'s' if vhd_count != 1 else ''}")
            if not vhd_count:
                warns += 1
        else:
            print_status("src/", "FAIL", "missing (required for module scope)")
            fails += 1

        # tb/ (optional)
        tb_dir = os.path.join(project_dir, "tb")
        if os.path.isdir(tb_dir):
            tb_count = _count_files(tb_dir, "*_tb.*")
            print_status("tb/", "PASS", f"{tb_count} testbench file{'s' if tb_count != 1 else ''}")
        else:
            print_status("tb/", "INFO", "not present")

    # Common to all scopes
    docs_dir = os.path.join(project_dir, "docs")
    if os.path.isdir(docs_dir):
        doc_files = []
        for name in ("ARCHITECTURE.md", "DESIGN-INTENT.md"):
            if os.path.isfile(os.path.join(docs_dir, name)):
                doc_files.append(name)
        print_status("docs/", "PASS" if doc_files else "WARN",
                     ", ".join(doc_files) if doc_files else "no key docs")
        if not doc_files:
            warns += 1
    else:
        print_status("docs/", "WARN", "missing")
        warns += 1

    # sw/ (optional)
    sw_dir = os.path.join(project_dir, "sw")
    if os.path.isdir(sw_dir):
        c_count = _count_files(sw_dir, "*.c")
        h_count = _count_files(sw_dir, "*.h")
        parts = []
        if c_count:
            parts.append(f"{c_count} .c")
        if h_count:
            parts.append(f"{h_count} .h")
        print_status("sw/", "PASS", ", ".join(parts) if parts else "empty")
    # sw/ is optional, don't warn if missing

    # CLAUDE.md
    if os.path.isfile(os.path.join(project_dir, "CLAUDE.md")):
        print_status("CLAUDE.md", "PASS", "present")
    else:
        print_status("CLAUDE.md", "WARN", "missing")
        warns += 1

    # .gitignore
    if os.path.isfile(os.path.join(project_dir, ".gitignore")):
        print_status(".gitignore", "PASS", "present")
    else:
        print_status(".gitignore", "WARN", "missing")
        warns += 1

    return warns, fails


# ---------------------------------------------------------------------------
# Check: Build Artifacts
# ---------------------------------------------------------------------------

def check_build(project_dir):
    """Check build artifacts and freshness. Returns (warns, fails)."""
    warns, fails = 0, 0
    scope = get_scope(project_dir) or "module"
    synth_dir = os.path.join(project_dir, "build", "synth")

    # timing.rpt
    timing_path = os.path.join(synth_dir, "timing.rpt")
    if os.path.isfile(timing_path):
        results = parse_timing_report(timing_path)
        if results:
            all_met = all(r.met for r in results)
            # Extract short names: "Setup (WNS)" -> "WNS"
            def _short_check(name):
                if "(" in name and ")" in name:
                    return name.split("(")[1].rstrip(")")
                return name
            slacks = ", ".join(f"{_short_check(r.check)} {r.slack_ns} ns" for r in results)
            if all_met:
                print_status("timing.rpt", "PASS", f"MET ({slacks})")
            else:
                print_status("timing.rpt", "FAIL", f"VIOLATED ({slacks})")
                fails += 1
        else:
            print_status("timing.rpt", "WARN", "present but no timing data parsed")
            warns += 1
    else:
        print_status("timing.rpt", "INFO", "not present")

    # utilization.rpt
    util_path = os.path.join(synth_dir, "utilization.rpt")
    if os.path.isfile(util_path):
        rows = parse_utilization_report(util_path)
        if rows:
            # Pick key resources for summary
            summary_parts = []
            seen = set()
            for r in rows:
                if r.resource == "Slice LUTs" and "LUT" not in seen:
                    summary_parts.append(f"LUT {r.used}")
                    seen.add("LUT")
                elif r.resource in ("Slice Registers", "Register as Flip Flop") and "FF" not in seen:
                    summary_parts.append(f"FF {r.used}")
                    seen.add("FF")
                elif r.resource in ("Block RAM Tile",) and "BRAM" not in seen:
                    summary_parts.append(f"BRAM {r.used}")
                    seen.add("BRAM")
                elif r.resource in ("DSPs", "DSP48E1") and "DSP" not in seen:
                    summary_parts.append(f"DSP {r.used}")
                    seen.add("DSP")
            print_status("utilization.rpt", "PASS",
                         ", ".join(summary_parts) if summary_parts else "present")
        else:
            print_status("utilization.rpt", "WARN", "present but no data parsed")
            warns += 1
    else:
        print_status("utilization.rpt", "INFO", "not present")

    # XSA / bitstream
    xsa_files = glob.glob(os.path.join(synth_dir, "*.xsa"))
    bit_files = glob.glob(os.path.join(synth_dir, "*.bit"))
    if xsa_files:
        print_status("*.xsa", "PASS", f"{len(xsa_files)} present")
    if bit_files:
        print_status("*.bit", "PASS", f"{len(bit_files)} present")
    if not xsa_files and not bit_files:
        print_status("XSA/bitstream", "INFO", "not present")

    # Freshness: compare output artifacts vs source files
    output_paths = xsa_files + bit_files
    if output_paths:
        output_mtime = max(os.path.getmtime(f) for f in output_paths)
        source_patterns = []
        if scope == "system":
            source_patterns = [
                os.path.join(synth_dir, "*.tcl"),
                os.path.join(project_dir, "constraints", "*.xdc"),
            ]
        else:
            source_patterns = [
                os.path.join(project_dir, "src", "*.vhd"),
                os.path.join(project_dir, "constraints", "*.xdc"),
            ]
        source_files = []
        for pat in source_patterns:
            source_files.extend(glob.glob(pat))

        if source_files:
            newest_source = max(os.path.getmtime(f) for f in source_files)
            if newest_source > output_mtime:
                # Find which file is newer
                newer = [os.path.basename(f) for f in source_files
                         if os.path.getmtime(f) > output_mtime]
                print_status("Freshness", "WARN",
                             f"output older than {', '.join(newer[:3])}")
                warns += 1
            else:
                print_status("Freshness", "PASS", "outputs up to date")

    return warns, fails


# ---------------------------------------------------------------------------
# Check: Pipeline (project.json)
# ---------------------------------------------------------------------------

def check_pipeline(project_dir):
    """Check project.json pipeline state. Returns (warns, fails)."""
    warns, fails = 0, 0
    sm = StateManager(project_dir)
    state = sm.load()

    if state is None:
        print_status("project.json", "INFO", "no pipeline state yet")
        return 0, 0

    project = state.get("project", {})

    # Last workflow
    workflow = project.get("last_workflow")
    if workflow:
        print_status("Last workflow", "PASS", workflow)

    # Per-stage status
    stages = state.get("stages", {})
    for snum in sorted(stages.keys(), key=lambda x: int(x)):
        entry = stages[snum]
        status = entry.get("status", "").upper()
        name = entry.get("name", f"Stage {snum}")
        if status == "PASS":
            print_status(f"Stage {snum}", "PASS", name)
        elif status == "FAIL":
            print_status(f"Stage {snum}", "FAIL", name)
            fails += 1
        elif status == "WAITING":
            print_status(f"Stage {snum}", "WARN", f"{name} (WAITING)")
            warns += 1
        else:
            print_status(f"Stage {snum}", "INFO", f"{name} ({status})")

    # Next action
    next_action = state.get("next_action")
    if next_action:
        suggested = next_action.get("suggested", "")
        if "FAIL" in suggested.upper():
            print_status("Next action", "WARN", suggested)
            warns += 1
        else:
            print_status("Next action", "PASS", suggested)

    # Input hash staleness
    changed, re_entry = sm.detect_changes()
    changed_dirs = [name for name, is_changed in changed.items() if is_changed]
    if changed_dirs:
        print_status("Input hashes", "WARN", f"changed: {', '.join(changed_dirs)}")
        warns += 1
    else:
        print_status("Input hashes", "PASS", "all current")

    return warns, fails


# ---------------------------------------------------------------------------
# Check: Run History (session.json + logs)
# ---------------------------------------------------------------------------

def check_history(project_dir):
    """Check session.json and pipeline logs. Returns (warns, fails)."""
    warns, fails = 0, 0
    session = load_session(project_dir)

    if session is None:
        print_status("Session", "INFO", "no run history")
        return 0, 0

    # Session ID
    session_id = session.get("session_id", "unknown")
    print_status("Session", "PASS", session_id)

    # Stage entries breakdown
    entries = session.get("stages", [])
    if entries:
        total = len(entries)
        pass_count = sum(1 for e in entries if e.get("status") == "pass")
        fail_count = sum(1 for e in entries if e.get("status") == "fail")
        print_status("Stage executions", "PASS",
                     f"{total} total ({pass_count} pass, {fail_count} fail)")

        # Per-stage iteration counts
        stage_runs = {}
        for e in entries:
            snum = e.get("stage")
            if snum is not None:
                stage_runs.setdefault(snum, []).append(e.get("status"))
        for snum in sorted(stage_runs.keys(), key=lambda x: int(x)):
            runs = stage_runs[snum]
            count = len(runs)
            last = runs[-1] if runs else "unknown"
            if count > 1 or last == "fail":
                lvl = "WARN" if last == "fail" else "INFO"
                print_status(f"Stage {snum}", lvl,
                             f"run {count}x, last {last.upper()}")
                if last == "fail":
                    warns += 1
    else:
        print_status("Stage executions", "INFO", "none recorded")

    # Pipeline log files
    logs_dir = os.path.join(project_dir, "build", "logs")
    log_files = sorted(glob.glob(os.path.join(logs_dir, "pipeline_*.log")))
    if log_files:
        print_status("Pipeline runs", "PASS",
                     f"{len(log_files)} log{'s' if len(log_files) != 1 else ''} in build/logs/")
        latest = log_files[-1]
        print_status("Latest log", "PASS", os.path.basename(latest))
    else:
        print_status("Pipeline logs", "INFO", "none found")

    return warns, fails


# ---------------------------------------------------------------------------
# Check: Git
# ---------------------------------------------------------------------------

def check_git(project_dir):
    """Check git working tree status. Returns (warns, fails)."""
    warns, fails = 0, 0
    git_dir = os.path.join(project_dir, ".git")
    if not os.path.exists(git_dir):
        print_status("Git", "INFO", "not a git repository")
        return 0, 0

    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True, text=True, cwd=project_dir, timeout=10)
        if result.returncode != 0:
            print_status("Git", "WARN", "git status failed")
            warns += 1
            return warns, fails

        lines = [l for l in result.stdout.strip().splitlines() if l.strip()]
        if not lines:
            print_status("Working tree", "PASS", "clean")
        else:
            modified = sum(1 for l in lines if l[0:2].strip() and l[0] != '?')
            untracked = sum(1 for l in lines if l.startswith('?'))
            parts = []
            if modified:
                parts.append(f"{modified} modified")
            if untracked:
                parts.append(f"{untracked} untracked")
            print_status("Working tree", "WARN", ", ".join(parts))
            warns += 1
    except (subprocess.TimeoutExpired, FileNotFoundError):
        print_status("Git", "INFO", "git not available")

    return warns, fails


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="SOCKS project status dashboard")
    parser.add_argument("--project-dir", type=str, default=".",
                        help="Path to project root")
    args = parser.parse_args()

    project_dir = os.path.abspath(args.project_dir)

    # Load config for project name
    cfg = load_project_config(project_dir)
    project_name = cfg.get("name", os.path.basename(project_dir)) if cfg else os.path.basename(project_dir)

    total_warns = 0
    total_fails = 0

    print()
    print_header(f"SOCKS Project Status \u2014 {project_name}")

    # 1. Config
    print(f"\n  {bold('Config (socks.json):')}")
    w, f = check_config(project_dir)
    total_warns += w
    total_fails += f

    # If no config, skip remaining checks
    if cfg is None:
        print()
        print_separator()
        print(f"  {bold('RESULT:')} Cannot proceed without socks.json")
        print_separator()
        print()
        return 2

    # 2. Directory
    scope = cfg.get("scope", "module")
    print(f"\n  {bold(f'Directory ({scope} scope):')}")
    w, f = check_directory(project_dir)
    total_warns += w
    total_fails += f

    # 3. Build Artifacts
    print(f"\n  {bold('Build Artifacts:')}")
    w, f = check_build(project_dir)
    total_warns += w
    total_fails += f

    # 4. Pipeline
    print(f"\n  {bold('Pipeline (project.json):')}")
    w, f = check_pipeline(project_dir)
    total_warns += w
    total_fails += f

    # 5. Run History
    print(f"\n  {bold('Run History:')}")
    w, f = check_history(project_dir)
    total_warns += w
    total_fails += f

    # 6. Git
    print(f"\n  {bold('Git:')}")
    w, f = check_git(project_dir)
    total_warns += w
    total_fails += f

    # Summary
    print()
    parts = []
    if total_warns:
        parts.append(f"{total_warns} warning{'s' if total_warns != 1 else ''}")
    if total_fails:
        parts.append(f"{total_fails} failure{'s' if total_fails != 1 else ''}")
    if not parts:
        parts.append("all checks passed")
    print_separator()
    print(f"  {bold('RESULT:')} {', '.join(parts)}")
    print_separator()
    print()

    if total_fails:
        return 2
    if total_warns:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
