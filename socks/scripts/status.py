#!/usr/bin/env python3
"""
status.py -- Project status dashboard for SOCKS projects.

Reads socks.json, scans directory structure, parses build reports, and
checks project.json pipeline state. Produces a text-based terminal
dashboard. Runs automatically after Stage 0 passes, or standalone.

Modes:
  (default)   Terminal-formatted dashboard
  --json      Structured JSON output (for Claude to parse)
  --scan      Scan subdirectories for multi-project workspace summary

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


def _result(name, level, detail=""):
    """Create a structured result dict."""
    return {"name": name, "level": level, "detail": detail}


def get_build_flow(project_dir):
    """Return socks.json build.flow, defaulting to native Vivado."""
    cfg = load_project_config(project_dir) or {}
    return cfg.get("build", {}).get("flow", "vivado_native")


# ---------------------------------------------------------------------------
# Check: Config (socks.json)
# ---------------------------------------------------------------------------

def check_config(project_dir, output="terminal"):
    """Validate socks.json fields. Returns (results, warns, fails)."""
    results = []
    warns, fails = 0, 0
    cfg = load_project_config(project_dir)
    if cfg is None:
        results.append(_result("socks.json", "FAIL", "missing or invalid"))
        if output == "terminal":
            print_status("socks.json", "FAIL", "missing or invalid")
        return results, 0, 1

    # name
    name = cfg.get("name")
    if name:
        results.append(_result("name", "PASS", name))
    else:
        results.append(_result("name", "FAIL", "missing"))
        fails += 1

    # scope
    scope = cfg.get("scope")
    valid_scopes = ("module", "system")
    if scope in valid_scopes:
        results.append(_result("scope", "PASS", scope))
    elif scope:
        results.append(_result("scope", "FAIL", f"'{scope}' not in {valid_scopes}"))
        fails += 1
    else:
        results.append(_result("scope", "FAIL", "missing"))
        fails += 1

    # board.part
    part = cfg.get("board", {}).get("part")
    if part:
        results.append(_result("board.part", "PASS", part))
    else:
        results.append(_result("board.part", "WARN", "missing"))
        warns += 1

    # board.preset
    preset = cfg.get("board", {}).get("preset")
    if preset:
        refs_dir = os.path.join(SCRIPT_DIR, "..", "references", "boards", preset)
        if os.path.isdir(refs_dir):
            results.append(_result("board.preset", "PASS", f"{preset} (board ref found)"))
        else:
            results.append(_result("board.preset", "WARN", f"{preset} (board ref not found)"))
            warns += 1

    # dut.entity
    entity = cfg.get("dut", {}).get("entity")
    if entity:
        results.append(_result("dut.entity", "PASS", entity))
    else:
        results.append(_result("dut.entity", "WARN", "needed for synthesis"))
        warns += 1

    if output == "terminal":
        for r in results:
            print_status(r["name"], r["level"], r["detail"])

    return results, warns, fails


# ---------------------------------------------------------------------------
# Check: Directory (scope-aware)
# ---------------------------------------------------------------------------

def _count_files(directory, pattern):
    """Count files matching a glob pattern in a directory."""
    return len(glob.glob(os.path.join(directory, pattern)))


def check_directory(project_dir, output="terminal"):
    """Check directory structure based on project scope. Returns (results, warns, fails)."""
    results = []
    warns, fails = 0, 0
    scope = get_scope(project_dir) or "module"
    build_flow = get_build_flow(project_dir)

    if scope == "system":
        # build/synth/
        synth_dir = os.path.join(project_dir, "build", "synth")
        if build_flow == "adi_make":
            results.append(_result(
                "build/synth/",
                "INFO",
                "not required; ADI Make stages artifacts under build/hil/",
            ))
        elif os.path.isdir(synth_dir):
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
            results.append(_result("build/synth/", "PASS", ", ".join(parts) if parts else "empty"))
        else:
            results.append(_result("build/synth/", "FAIL", "missing"))
            fails += 1

        # constraints/
        constr_dir = os.path.join(project_dir, "constraints")
        if build_flow == "adi_make":
            results.append(_result(
                "constraints/",
                "INFO",
                "owned by ADI Make reference project",
            ))
        elif os.path.isdir(constr_dir):
            xdc_count = _count_files(constr_dir, "*.xdc")
            level = "PASS" if xdc_count else "WARN"
            results.append(_result("constraints/", level,
                                   f"{xdc_count} XDC file{'s' if xdc_count != 1 else ''}"))
            if not xdc_count:
                warns += 1
        else:
            results.append(_result("constraints/", "WARN", "missing"))
            warns += 1
    else:
        # Module scope: src/*.vhd
        src_dir = os.path.join(project_dir, "src")
        if os.path.isdir(src_dir):
            vhd_count = _count_files(src_dir, "*.vhd")
            level = "PASS" if vhd_count else "WARN"
            results.append(_result("src/", level,
                                   f"{vhd_count} VHDL file{'s' if vhd_count != 1 else ''}"))
            if not vhd_count:
                warns += 1
        else:
            results.append(_result("src/", "FAIL", "missing (required for module scope)"))
            fails += 1

        # tb/ (optional)
        tb_dir = os.path.join(project_dir, "tb")
        if os.path.isdir(tb_dir):
            tb_count = _count_files(tb_dir, "*_tb.*")
            results.append(_result("tb/", "PASS", f"{tb_count} testbench file{'s' if tb_count != 1 else ''}"))
        else:
            results.append(_result("tb/", "INFO", "not present"))

    # Common to all scopes
    docs_dir = os.path.join(project_dir, "docs")
    if os.path.isdir(docs_dir):
        doc_files = []
        for fname in ("ARCHITECTURE.md", "DESIGN-INTENT.md"):
            if os.path.isfile(os.path.join(docs_dir, fname)):
                doc_files.append(fname)
        level = "PASS" if doc_files else "WARN"
        results.append(_result("docs/", level,
                               ", ".join(doc_files) if doc_files else "no key docs"))
        if not doc_files:
            warns += 1
    else:
        results.append(_result("docs/", "WARN", "missing"))
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
        results.append(_result("sw/", "PASS", ", ".join(parts) if parts else "empty"))

    # CLAUDE.md
    if os.path.isfile(os.path.join(project_dir, "CLAUDE.md")):
        results.append(_result("CLAUDE.md", "PASS", "present"))
    else:
        results.append(_result("CLAUDE.md", "WARN", "missing"))
        warns += 1

    # .gitignore
    if os.path.isfile(os.path.join(project_dir, ".gitignore")):
        results.append(_result(".gitignore", "PASS", "present"))
    else:
        results.append(_result(".gitignore", "WARN", "missing"))
        warns += 1

    if output == "terminal":
        for r in results:
            print_status(r["name"], r["level"], r["detail"])

    return results, warns, fails


# ---------------------------------------------------------------------------
# Check: Build Artifacts
# ---------------------------------------------------------------------------

def check_build(project_dir, output="terminal"):
    """Check build artifacts and freshness. Returns (results, warns, fails)."""
    results = []
    warns, fails = 0, 0
    scope = get_scope(project_dir) or "module"
    build_flow = get_build_flow(project_dir)
    synth_dir = os.path.join(project_dir, "build", "synth")

    # timing.rpt
    timing_path = os.path.join(synth_dir, "timing.rpt")
    if os.path.isfile(timing_path):
        timing_results = parse_timing_report(timing_path)
        if timing_results:
            all_met = all(r.met for r in timing_results)
            def _short_check(name):
                if "(" in name and ")" in name:
                    return name.split("(")[1].rstrip(")")
                return name
            slacks = ", ".join(f"{_short_check(r.check)} {r.slack_ns} ns" for r in timing_results)
            if all_met:
                results.append(_result("timing.rpt", "PASS", f"MET ({slacks})"))
            else:
                results.append(_result("timing.rpt", "FAIL", f"VIOLATED ({slacks})"))
                fails += 1
        else:
            results.append(_result("timing.rpt", "WARN", "present but no timing data parsed"))
            warns += 1
    else:
        results.append(_result("timing.rpt", "INFO", "not present"))

    # utilization.rpt
    util_path = os.path.join(synth_dir, "utilization.rpt")
    if os.path.isfile(util_path):
        rows = parse_utilization_report(util_path)
        if rows:
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
            results.append(_result("utilization.rpt", "PASS",
                                   ", ".join(summary_parts) if summary_parts else "present"))
        else:
            results.append(_result("utilization.rpt", "WARN", "present but no data parsed"))
            warns += 1
    else:
        results.append(_result("utilization.rpt", "INFO", "not present"))

    # XSA / bitstream
    xsa_files = glob.glob(os.path.join(synth_dir, "*.xsa"))
    bit_files = glob.glob(os.path.join(synth_dir, "*.bit"))
    if build_flow == "adi_make":
        hil_dir = os.path.join(project_dir, "build", "hil")
        xsa_files.extend(glob.glob(os.path.join(hil_dir, "*.xsa")))
        bit_files.extend(glob.glob(
            os.path.join(hil_dir, "vivado_project", "**", "*.bit"),
            recursive=True,
        ))
    if xsa_files:
        results.append(_result("*.xsa", "PASS", f"{len(xsa_files)} present"))
    if bit_files:
        results.append(_result("*.bit", "PASS", f"{len(bit_files)} present"))
    if not xsa_files and not bit_files:
        results.append(_result("XSA/bitstream", "INFO", "not present"))

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
                newer = [os.path.basename(f) for f in source_files
                         if os.path.getmtime(f) > output_mtime]
                results.append(_result("Freshness", "WARN",
                                       f"output older than {', '.join(newer[:3])}"))
                warns += 1
            else:
                results.append(_result("Freshness", "PASS", "outputs up to date"))

    if output == "terminal":
        for r in results:
            print_status(r["name"], r["level"], r["detail"])

    return results, warns, fails


# ---------------------------------------------------------------------------
# Check: Pipeline (project.json)
# ---------------------------------------------------------------------------

def check_pipeline(project_dir, output="terminal"):
    """Check project.json pipeline state. Returns (results, warns, fails)."""
    results = []
    warns, fails = 0, 0
    sm = StateManager(project_dir)
    state = sm.load()

    if state is None:
        results.append(_result("project.json", "INFO", "no pipeline state yet"))
        if output == "terminal":
            print_status("project.json", "INFO", "no pipeline state yet")
        return results, 0, 0

    project = state.get("project", {})

    # Last workflow
    workflow = project.get("last_workflow")
    if workflow:
        results.append(_result("Last workflow", "PASS", workflow))

    # Per-stage status
    stages = state.get("stages", {})
    for snum in sorted(stages.keys(), key=lambda x: int(x)):
        entry = stages[snum]
        status = entry.get("status", "").upper()
        name = entry.get("name", f"Stage {snum}")
        if status == "PASS":
            results.append(_result(f"Stage {snum}", "PASS", name))
        elif status == "FAIL":
            results.append(_result(f"Stage {snum}", "FAIL", name))
            fails += 1
        elif status == "WAITING":
            results.append(_result(f"Stage {snum}", "WARN", f"{name} (WAITING)"))
            warns += 1
        else:
            results.append(_result(f"Stage {snum}", "INFO", f"{name} ({status})"))

    # Next action
    next_action = state.get("next_action")
    if next_action:
        suggested = next_action.get("suggested", "")
        if "FAIL" in suggested.upper():
            results.append(_result("Next action", "WARN", suggested))
            warns += 1
        else:
            results.append(_result("Next action", "PASS", suggested))

    # Input hash staleness
    changed, re_entry = sm.detect_changes()
    changed_dirs = [name for name, is_changed in changed.items() if is_changed]
    if changed_dirs:
        results.append(_result("Input hashes", "WARN", f"changed: {', '.join(changed_dirs)}"))
        warns += 1
    else:
        results.append(_result("Input hashes", "PASS", "all current"))

    if output == "terminal":
        for r in results:
            print_status(r["name"], r["level"], r["detail"])

    return results, warns, fails


# ---------------------------------------------------------------------------
# Check: Run History (session.json + logs)
# ---------------------------------------------------------------------------

def check_history(project_dir, output="terminal"):
    """Check session.json and pipeline logs. Returns (results, warns, fails)."""
    results = []
    warns, fails = 0, 0
    session = load_session(project_dir)

    if session is None:
        results.append(_result("Session", "INFO", "no run history"))
        if output == "terminal":
            print_status("Session", "INFO", "no run history")
        return results, 0, 0

    # Session ID
    session_id = session.get("session_id", "unknown")
    results.append(_result("Session", "PASS", session_id))

    # Stage entries breakdown
    entries = session.get("stages", [])
    if entries:
        total = len(entries)
        pass_count = sum(1 for e in entries if e.get("status") == "pass")
        fail_count = sum(1 for e in entries if e.get("status") == "fail")
        results.append(_result("Stage executions", "PASS",
                               f"{total} total ({pass_count} pass, {fail_count} fail)"))

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
                results.append(_result(f"Stage {snum}", lvl,
                                       f"run {count}x, last {last.upper()}"))
                if last == "fail":
                    warns += 1
    else:
        results.append(_result("Stage executions", "INFO", "none recorded"))

    # Pipeline log files
    logs_dir = os.path.join(project_dir, "build", "logs")
    log_files = sorted(glob.glob(os.path.join(logs_dir, "pipeline_*.log")))
    if log_files:
        results.append(_result("Pipeline runs", "PASS",
                               f"{len(log_files)} log{'s' if len(log_files) != 1 else ''} in build/logs/"))
        latest = log_files[-1]
        results.append(_result("Latest log", "PASS", os.path.basename(latest)))
    else:
        results.append(_result("Pipeline logs", "INFO", "none found"))

    if output == "terminal":
        for r in results:
            print_status(r["name"], r["level"], r["detail"])

    return results, warns, fails


# ---------------------------------------------------------------------------
# Check: Git
# ---------------------------------------------------------------------------

def check_git(project_dir, output="terminal"):
    """Check git working tree status. Returns (results, warns, fails)."""
    results = []
    warns, fails = 0, 0
    git_dir = os.path.join(project_dir, ".git")
    if not os.path.exists(git_dir):
        results.append(_result("Git", "INFO", "not a git repository"))
        if output == "terminal":
            print_status("Git", "INFO", "not a git repository")
        return results, 0, 0

    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True, text=True, cwd=project_dir, timeout=10)
        if result.returncode != 0:
            results.append(_result("Git", "WARN", "git status failed"))
            warns += 1
            if output == "terminal":
                for r in results:
                    print_status(r["name"], r["level"], r["detail"])
            return results, warns, fails

        lines = [l for l in result.stdout.strip().splitlines() if l.strip()]
        if not lines:
            results.append(_result("Working tree", "PASS", "clean"))
        else:
            modified = sum(1 for l in lines if l[0:2].strip() and l[0] != '?')
            untracked = sum(1 for l in lines if l.startswith('?'))
            parts = []
            if modified:
                parts.append(f"{modified} modified")
            if untracked:
                parts.append(f"{untracked} untracked")
            results.append(_result("Working tree", "WARN", ", ".join(parts)))
            warns += 1
    except (subprocess.TimeoutExpired, FileNotFoundError):
        results.append(_result("Git", "INFO", "git not available"))

    if output == "terminal":
        for r in results:
            print_status(r["name"], r["level"], r["detail"])

    return results, warns, fails


# ---------------------------------------------------------------------------
# Suggestions: compute contextual next actions from structured results
# ---------------------------------------------------------------------------

def compute_suggestions(project_dir, sections):
    """Compute priority-ordered suggestions from structured check results.

    Returns a list of suggestion dicts: {action, reason} or
    {action, stage, reason} for stage-specific actions.
    """
    suggestions = []
    seen_actions = set()

    # Parse pipeline results for stage failures and input hash changes
    pipeline_results = sections.get("pipeline", [])
    has_pipeline = False
    all_stages_pass = True
    has_hil_config = os.path.isfile(os.path.join(project_dir, "hil.json"))

    for r in pipeline_results:
        if r["name"] == "project.json" and r["level"] == "INFO":
            # No pipeline state yet
            break
        if r["name"].startswith("Stage "):
            has_pipeline = True
            if r["level"] == "FAIL":
                all_stages_pass = False
                # Extract stage number
                try:
                    snum = int(r["name"].split()[1])
                except (IndexError, ValueError):
                    snum = None
                if snum is not None and "rerun_stage" not in seen_actions:
                    stage_name = r["detail"]
                    suggestions.append({
                        "action": "rerun_stage",
                        "stage": snum,
                        "priority": "recommended",
                        "reason": f"Stage {snum} ({stage_name}) FAILED"
                    })
                    seen_actions.add("rerun_stage")
            elif r["level"] == "WARN":
                all_stages_pass = False

        # Input hash staleness -> re-run from re-entry stage
        if r["name"] == "Input hashes" and r["level"] == "WARN":
            sm = StateManager(project_dir)
            changed, re_entry = sm.detect_changes()
            changed_dirs = [name for name, is_changed in changed.items() if is_changed]
            if re_entry is not None and "rerun_changed" not in seen_actions:
                suggestions.append({
                    "action": "rerun_stage",
                    "stage": re_entry,
                    "priority": "recommended",
                    "reason": f"Inputs changed ({', '.join(changed_dirs)}) — re-run from Stage {re_entry}"
                })
                seen_actions.add("rerun_changed")

    # Build freshness
    build_results = sections.get("build", [])
    for r in build_results:
        if r["name"] == "Freshness" and r["level"] == "WARN":
            if "rebuild" not in seen_actions:
                suggestions.append({
                    "action": "rebuild",
                    "priority": "recommended",
                    "reason": f"Sources changed since last build ({r['detail']})"
                })
                seen_actions.add("rebuild")

    # No pipeline state at all
    if not has_pipeline:
        if "design" not in seen_actions:
            suggestions.append({
                "action": "design",
                "priority": "recommended",
                "reason": "No pipeline runs yet — start design workflow"
            })
            seen_actions.add("design")

    # All green — promote test/hil to recommended
    if has_pipeline and all_stages_pass:
        suggestions.append({
            "action": "test",
            "priority": "recommended",
            "reason": "All stages passing — run tests?"
        })
        seen_actions.add("test")
        if has_hil_config:
            suggestions.append({
                "action": "hil",
                "priority": "recommended",
                "reason": "All stages passing — ready for HIL"
            })
            seen_actions.add("hil")

    # Always-available workflows — all orchestrator commands, unfiltered
    available = [
        {"action": "test", "priority": "available",
         "reason": "Run simulation tests"},
        {"action": "design", "priority": "available",
         "reason": "Run full design workflow"},
        {"action": "architecture", "priority": "available",
         "reason": "Re-architecture workflow"},
        {"action": "bughunt", "priority": "available",
         "reason": "Bug hunt + verify"},
        {"action": "hil", "priority": "available",
         "reason": "Hardware-in-the-loop test"},
        {"action": "validate", "priority": "available",
         "reason": "Full end-to-end validation"},
        {"action": "migrate", "priority": "available",
         "reason": "Migrate project layout"},
    ]

    # Deduplicate: don't repeat actions already in recommendations
    for a in available:
        if a["action"] not in seen_actions:
            suggestions.append(a)
            seen_actions.add(a["action"])

    return suggestions


# ---------------------------------------------------------------------------
# Scan: multi-project workspace
# ---------------------------------------------------------------------------

def scan_workspace(workspace_dir):
    """Scan immediate subdirectories for socks.json, return summary array."""
    projects = []
    try:
        entries = sorted(os.listdir(workspace_dir))
    except OSError:
        return projects

    for entry in entries:
        subdir = os.path.join(workspace_dir, entry)
        if not os.path.isdir(subdir):
            continue
        socks_json = os.path.join(subdir, "socks.json")
        if not os.path.isfile(socks_json):
            continue

        cfg = load_project_config(subdir)
        if cfg is None:
            projects.append({
                "dir": entry,
                "name": entry,
                "scope": "unknown",
                "pass": 0, "warn": 0, "fail": 1,
                "last_workflow": None
            })
            continue

        # Lightweight status: config + pipeline only
        config_results, c_warns, c_fails = check_config(subdir, output="json")
        pipeline_results, p_warns, p_fails = check_pipeline(subdir, output="json")

        total_pass = sum(1 for r in config_results + pipeline_results if r["level"] == "PASS")
        total_warn = c_warns + p_warns
        total_fail = c_fails + p_fails

        # Extract last workflow from pipeline results
        last_workflow = None
        for r in pipeline_results:
            if r["name"] == "Last workflow":
                last_workflow = r["detail"]
                break

        projects.append({
            "dir": entry,
            "name": cfg.get("name", entry),
            "scope": cfg.get("scope", "unknown"),
            "pass": total_pass,
            "warn": total_warn,
            "fail": total_fail,
            "last_workflow": last_workflow
        })

    return projects


# ---------------------------------------------------------------------------
# Full status collection (JSON mode)
# ---------------------------------------------------------------------------

def collect_full_status(project_dir):
    """Run all checks and return structured JSON dict."""
    cfg = load_project_config(project_dir)
    project_name = cfg.get("name", os.path.basename(project_dir)) if cfg else os.path.basename(project_dir)
    scope = cfg.get("scope", "unknown") if cfg else "unknown"

    sections = {}
    total_pass, total_warn, total_fail, total_info = 0, 0, 0, 0

    def _run_section(name, check_fn):
        nonlocal total_pass, total_warn, total_fail, total_info
        results, w, f = check_fn(project_dir, output="json")
        sections[name] = results
        total_warn += w
        total_fail += f
        total_pass += sum(1 for r in results if r["level"] == "PASS")
        total_info += sum(1 for r in results if r["level"] == "INFO")

    _run_section("config", check_config)

    if cfg is not None:
        _run_section("directory", check_directory)
        _run_section("build", check_build)
        _run_section("pipeline", check_pipeline)
        _run_section("history", check_history)
        _run_section("git", check_git)

    suggestions = compute_suggestions(project_dir, sections) if cfg else []

    return {
        "project_dir": project_dir,
        "name": project_name,
        "scope": scope,
        "summary": {
            "pass": total_pass,
            "warn": total_warn,
            "fail": total_fail,
            "info": total_info
        },
        "sections": sections,
        "suggestions": suggestions
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="SOCKS project status dashboard")
    parser.add_argument("--project-dir", type=str, default=".",
                        help="Path to project root (or workspace for --scan)")
    parser.add_argument("--json", action="store_true",
                        help="Output structured JSON instead of terminal formatting")
    parser.add_argument("--scan", action="store_true",
                        help="Scan subdirectories for multi-project workspace summary")
    args = parser.parse_args()

    project_dir = os.path.abspath(args.project_dir)

    # --scan mode: multi-project workspace
    if args.scan:
        projects = scan_workspace(project_dir)
        print(json.dumps(projects, indent=2))
        return 0

    # --json mode: structured single-project output
    if args.json:
        result = collect_full_status(project_dir)
        print(json.dumps(result, indent=2))
        if result["summary"]["fail"]:
            return 2
        return 0

    # Terminal mode (default, unchanged behavior)
    cfg = load_project_config(project_dir)
    project_name = cfg.get("name", os.path.basename(project_dir)) if cfg else os.path.basename(project_dir)

    total_warns = 0
    total_fails = 0

    # Collect section output, then print header with result color
    import io as _io
    buf = _io.StringIO()
    _real_print = print  # keep ref to builtin

    def _buf_print(*args, **kwargs):
        kwargs["file"] = buf
        _real_print(*args, **kwargs)

    # Temporarily redirect check output to buffer
    import builtins
    _orig = builtins.print
    builtins.print = _buf_print

    try:
        # 1. Config
        _buf_print(f"\n  {bold('Config (socks.json):')}")
        _, w, f = check_config(project_dir, output="terminal")
        total_warns += w
        total_fails += f

        # If no config, skip remaining checks
        if cfg is None:
            builtins.print = _orig
            _real_print()
            print_header(f"SOCKS Project Status \u2014 {project_name}")
            _real_print(buf.getvalue(), end="")
            _real_print()
            print_separator()
            _real_print(f"  {bold('RESULT:')} Cannot proceed without socks.json")
            print_separator()
            _real_print()
            return 2

        # 2. Directory
        scope = cfg.get("scope", "module")
        _buf_print(f"\n  {bold(f'Directory ({scope} scope):')}")
        _, w, f = check_directory(project_dir, output="terminal")
        total_warns += w
        total_fails += f

        # 3. Build Artifacts
        _buf_print(f"\n  {bold('Build Artifacts:')}")
        _, w, f = check_build(project_dir, output="terminal")
        total_warns += w
        total_fails += f

        # 4. Pipeline
        _buf_print(f"\n  {bold('Pipeline (project.json):')}")
        _, w, f = check_pipeline(project_dir, output="terminal")
        total_warns += w
        total_fails += f

        # 5. Run History
        _buf_print(f"\n  {bold('Run History:')}")
        _, w, f = check_history(project_dir, output="terminal")
        total_warns += w
        total_fails += f

        # 6. Git
        _buf_print(f"\n  {bold('Git:')}")
        _, w, f = check_git(project_dir, output="terminal")
        total_warns += w
        total_fails += f
    finally:
        builtins.print = _orig

    # Now print header with color based on result
    title = f"SOCKS Project Status \u2014 {project_name}"
    sep = "=" * 72
    if total_fails:
        color = red
    elif total_warns:
        color = yellow
    else:
        color = green

    print()
    print(color(sep))
    print(f"  {color(title)}")
    print(color(sep))

    # Print buffered section output
    print(buf.getvalue(), end="")

    # Summary
    print()
    parts = []
    if total_warns:
        parts.append(f"{total_warns} warning{'s' if total_warns != 1 else ''}")
    if total_fails:
        parts.append(f"{total_fails} failure{'s' if total_fails != 1 else ''}")
    if not parts:
        parts.append("all checks passed")
    print(color(sep))
    summary_str = ", ".join(parts)
    print(f"  {bold('RESULT:')} {color(summary_str)}")
    print(color(sep))
    print()

    if total_fails:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
