#!/usr/bin/env python3
"""
socks.py -- SOCKS Pipeline Orchestrator

Runs pipeline stages in sequence or individually.

Workflow entry points:
    python scripts/socks.py --project-dir . --design --scope system
    python scripts/socks.py --project-dir . --test
    python scripts/socks.py --project-dir . --architecture --scope module
    python scripts/socks.py --project-dir . --bughunt
    python scripts/socks.py --project-dir . --migrate
    python scripts/socks.py --project-dir . --hil --top usart_frame_ctrl
    python scripts/socks.py --project-dir . --validate

Legacy / explicit stage control:
    python scripts/socks.py --project-dir . --stages automated
    python scripts/socks.py --project-dir . --stages 0,4,7
    python scripts/socks.py --project-dir . --stages 4 --files src/*.vhd

Workflows:
    --design        Full design: stages 0,1,3,4,5,7,8,9,10,11,13
    --test          Simulation only: stages 4,5,7,8,9
    --architecture  Re-architecture: stages 0,1,3,4,5,7,8,9,10,11,13
    --bughunt       Bug fix + verify: stages 3,4,5,7,8,9,10
    --migrate       Migrate old log-based project to state file format
    --hil           Hardware-in-the-loop: stages 0,10,14,15,16,17,18,19 (requires --top)
    --validate      Full validation: env + sim + synth + audit + HIL (skips HIL if no hardware)

Stage keywords:
    automated   All stages with scripts (default)
    0,4,7       Specific stages (comma-separated, no auto-expansion)

Stages 2-9 form the design loop. Claude decides re-entry on failure.
Guidance-only stages (2, 6, 12) are driven by Claude reading SKILL.md,
not by this orchestrator.
"""

import argparse
import glob
import json
import os
import subprocess
import sys
from collections import namedtuple
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from socks_lib import print_header, print_separator, pass_str, fail_str, yellow, bold, green
from session import load_session, create_session, append_session_entry
from state_manager import StateManager, HASH_DIRS
from project_config import load_project_config, get_scope, get_part, get_entity

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# Collected during the run, written to logs/ at the end
_transition_log = []

# ---------------------------------------------------------------------------
# Unified stage definitions
# ---------------------------------------------------------------------------

StageDef = namedtuple("StageDef",
    ["label", "script", "guidance", "required_files"],
    defaults=[None, None, None])

# Exit code returned when a guidance stage is waiting for Claude to author files.
WAITING = 2

STAGES = {
    0:  StageDef("Environment Setup",       script="env.py"),
    1:  StageDef("Architecture Analysis",    script="architecture.py",
                 guidance="RTL + TB architecture, Mermaid diagrams, rate analysis (read references/architecture-diagrams.md). Enter plan mode for user approval before proceeding."),
    2:  StageDef("Write/Modify RTL",
                 guidance="read VHDL coding patterns from training data",
                 required_files=["src/*.vhd"]),
    3:  StageDef("VHDL Linter",              script="linter.py",
                 guidance="read references/linter.md"),
    4:  StageDef("Synthesis Audit",          script="audit.py"),
    5:  StageDef("Python Testbench",         script="python_rerun.py",
                 guidance="Write/update cycle-accurate Python model (read references/python-testbench.md)"),
    6:  StageDef("Bare-Metal C Driver",
                 guidance="read references/baremetal.md",
                 required_files=["sw/*.c", "sw/*.h"]),
    7:  StageDef("SV/Xsim Testbench",        script="xsim.py",
                 guidance="Write/update SV testbench (read references/xsim.md)"),
    8:  StageDef("VCD Verification",         script="vcd_verify.py"),
    9:  StageDef("CSV Cross-Check",          script="csv_crosscheck.py"),
    10: StageDef("Vivado Synthesis",         script="synth.py"),
    11: StageDef("Bash Audit",               script="bash_audit.py"),
    12: StageDef("CLAUDE.md Documentation",
                 guidance="read references/structure-module.md, references/claude_notes.md",
                 required_files=["CLAUDE.md"]),
    13: StageDef("SOCKS Self-Audit",         script="self_audit.py"),
    14: StageDef("HIL: Vivado Project",      script="hil/hil_project.py"),
    15: StageDef("HIL: Implementation",      script="hil/hil_impl.py"),
    16: StageDef("HIL: Firmware Build",      script="hil/hil_firmware.py",
                 guidance="Claude writes sw/hil_test_main.c (read references/hil.md § Firmware Authoring Guide)",
                 required_files=["sw/hil_test_main.c"]),
    17: StageDef("HIL: Program + Test",      script="hil/hil_run.py"),
    18: StageDef("HIL: ILA Capture",         script="hil/hil_ila.py"),
    19: StageDef("HIL: ILA Verify",          script="hil/hil_verify.py"),
    20: StageDef("System Design Loop",
                 guidance="read references/design-loop-system.md",
                 required_files=[
                     "build/synth/create_bd.tcl",
                     "build/synth/build_bitstream.tcl",
                     "constraints/*.xdc",
                     "docs/ARCHITECTURE.md",
                 ]),
}

DESIGN_LOOP = [2, 3, 4, 5, 6, 7, 8, 9]  # informational only, not mechanical

# Workflow-to-stages mapping (automated stages only; guidance stages handled by Claude)
WORKFLOW_STAGES = {
    "design":        [0, 1, 3, 4, 5, 7, 8, 9, 10, 11, 12, 13],
    "design_system": [0, 1, 20, 10, 11, 12, 13],
    "test":          [4, 5, 7, 8, 9],
    "architecture":  [0, 1, 3, 4, 5, 7, 8, 9, 10, 11, 12, 13],
    "bughunt":       [3, 4, 5, 7, 8, 9, 10],
    "hil":              [0, 10, 14, 15, 16, 17, 18, 19, 11, 12, 13],
    "validate":         [0, 1, 3, 4, 5, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19],
    "validate_system":  [0, 1, 20, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19],
}


# ---------------------------------------------------------------------------
# Session summary
# ---------------------------------------------------------------------------

def print_session_summary(project_dir):
    """Print a compact text summary of the current session manifest."""
    session = load_session(project_dir)
    if session is None:
        print("  No session manifest found.")
        return

    print(f"SOCKS Pipeline Session: {session['session_id']}")
    print(f"Project: {session['project']}")
    print()

    stages_list = session["stages"]
    if not stages_list:
        print("  (no stages recorded)")
        return

    n_pass = n_fail = n_skip = 0
    max_iteration = 0

    for entry in stages_list:
        stage_num = entry["stage"]
        label = STAGES[stage_num].label if stage_num in STAGES else f"Stage {stage_num}"
        status = entry["status"].upper()
        time_str = entry.get("time", "")
        note = entry.get("note", "") or ""
        iteration = entry.get("iteration", 1)

        # Iteration badge
        if iteration > 1:
            iter_badge = f" [{iteration}]"
        else:
            iter_badge = ""

        # Status colour
        if status == "PASS":
            status_display = pass_str()
            n_pass += 1
        elif status == "FAIL":
            status_display = fail_str()
            n_fail += 1
        else:
            status_display = yellow(status)
            n_skip += 1

        if iteration > max_iteration:
            max_iteration = iteration

        # Format: Stage  N: Name              [iter] STATUS  time  note
        note_trunc = note[:50] if note else ""
        print(f"  Stage {stage_num:2d}: {label:<28s}{iter_badge:>4s} "
              f"{status_display}   {time_str}   {note_trunc}")

    # Compute iterations (max iteration across all stages)
    iter_count = max_iteration if max_iteration > 1 else 0

    total = n_pass + n_fail + n_skip
    print()
    print(f"Result: {n_pass}/{total} stages passed", end="")
    if iter_count > 0:
        print(f" ({iter_count} design-loop iterations)")
    else:
        print()


def log_transition(stage_num, reason, extra_args, project_dir):
    """Log what stage is about to run, why, and what it receives."""
    label = STAGES[stage_num].label if stage_num in STAGES else f"Stage {stage_num}"
    # Build display args
    display_args = []
    if extra_args:
        for a in extra_args:
            if isinstance(a, str) and a.startswith(project_dir):
                display_args.append(os.path.relpath(a, project_dir))
            else:
                display_args.append(str(a))

    # Record for log file
    _transition_log.append({
        "stage": stage_num,
        "label": label,
        "reason": reason,
        "args": display_args,
        "time": datetime.now().strftime("%H:%M:%S"),
    })

    # Print to console
    print(f"\n  {bold('>>>')} Stage {stage_num}: {label}")
    print(f"      Reason: {reason}")
    if display_args:
        print(f"      Args:   {' '.join(display_args)}")
    else:
        print(f"      Args:   (none)")


def parse_stages(stages_str):
    """Parse stage specification.

    Keywords:
        automated  - all stages with scripts (default)

    Otherwise: comma-separated stage numbers (no auto-expansion).
    """
    keyword = stages_str.strip().lower()
    if keyword == "automated":
        return sorted(k for k, v in STAGES.items() if v.script)
    # Comma-separated numbers -- no auto-expansion
    return [int(p.strip()) for p in stages_str.split(",") if p.strip()]


def find_vhdl_files(project_dir):
    """Find VHDL files in project src/ directory."""
    src_dir = os.path.join(project_dir, "src")
    if os.path.isdir(src_dir):
        return sorted(glob.glob(os.path.join(src_dir, "*.vhd")))
    return sorted(glob.glob(os.path.join(project_dir, "*.vhd")))


def find_python_tb(project_dir):
    """Find Python testbench in project tb/ directory."""
    tb_dir = os.path.join(project_dir, "tb")
    if os.path.isdir(tb_dir):
        candidates = glob.glob(os.path.join(tb_dir, "*_tb.py"))
        if candidates:
            return candidates[0]
    return None


def _has_dynamic_required(project_dir, stage_num):
    """True if project.json has required_files for this stage."""
    state_file = os.path.join(project_dir, "build", "state", "project.json")
    if not os.path.isfile(state_file):
        return False
    try:
        with open(state_file) as f:
            state = json.load(f)
        return bool(state.get("stages", {})
                         .get(str(stage_num), {})
                         .get("required_files"))
    except (json.JSONDecodeError, OSError):
        return False


def check_required_files(project_dir, stage_num):
    """Check required files for a guidance stage.

    Merges two sources:
      1. Static glob patterns from StageDef.required_files
      2. Dynamic file lists from project.json stages.<N>.required_files

    Returns (present, missing) where each is a list of patterns/paths.
    """
    stage = STAGES.get(stage_num)
    patterns = list(stage.required_files) if stage and stage.required_files else []

    # Merge dynamic required_files from project.json
    state_file = os.path.join(project_dir, "build", "state", "project.json")
    if os.path.isfile(state_file):
        try:
            with open(state_file) as f:
                state = json.load(f)
            dynamic = (state.get("stages", {})
                            .get(str(stage_num), {})
                            .get("required_files", []))
            # Dynamic files are exact paths, not globs — add without duplicates
            for df in dynamic:
                if df not in patterns:
                    patterns.append(df)
        except (json.JSONDecodeError, OSError):
            pass

    present = []
    missing = []
    for pattern in patterns:
        if glob.glob(os.path.join(project_dir, pattern)):
            present.append(pattern)
        else:
            missing.append(pattern)

    return present, missing


def run_stage(stage_num, project_dir, extra_args=None, script_override=None):
    """Run a pipeline stage. Returns exit code.

    Guidance-only stages gate on required files:
      - All present -> PASS (0)
      - Any missing -> WAITING (2), pipeline stops
    script_override: use this script instead of the STAGES default.
    """
    if stage_num not in STAGES:
        print(f"\n  ERROR: Unknown stage {stage_num}")
        return 1

    stage = STAGES[stage_num]

    # Guidance-only stage with no script (and no override)
    if not stage.script and not script_override:
        print(f"\n  Stage {stage_num}: {stage.label}")

        # Check required files (static from StageDef + dynamic from project.json)
        if stage.required_files or _has_dynamic_required(project_dir, stage_num):
            present, missing = check_required_files(project_dir, stage_num)
            if missing:
                print(f"  {yellow('WAITING')} -- required files not found:")
                for f in missing:
                    print(f"    - {f}")
                if present:
                    print(f"  Already present:")
                    for f in present:
                        print(f"    + {f}")
                if stage.guidance:
                    print(f"\n  Action: {stage.guidance}")
                print(f"\n  Re-run the orchestrator after authoring these files.")
                return WAITING
            else:
                print(f"  All {len(present)} required file(s) present -- {pass_str()}")
                return 0
        else:
            print(f"  (Guidance-only -- no required files defined)")
            return 0

    script_path = os.path.join(SCRIPT_DIR, script_override or stage.script)

    if not os.path.isfile(script_path):
        print(f"\n  ERROR: Script not found: {script_path}")
        return 1

    cmd = [sys.executable, script_path]
    if extra_args:
        cmd.extend(extra_args)

    env = os.environ.copy()
    env["PYTHONPYCACHEPREFIX"] = os.path.join(project_dir, "build", "py")
    result = subprocess.run(cmd, cwd=project_dir, env=env)
    return result.returncode


def main() -> int:
    parser = argparse.ArgumentParser(
        description="SOCKS Pipeline Orchestrator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__)
    parser.add_argument("--project-dir", type=str, default=".",
                        help="Project root directory (default: current dir)")

    # Workflow entry points (mutually exclusive with each other)
    workflow = parser.add_mutually_exclusive_group()
    workflow.add_argument("--design", action="store_true",
                          help="Full design workflow: discovery + stages 0-13")
    workflow.add_argument("--test", action="store_true",
                          help="Test workflow: simulation stages (4,5,7,8,9)")
    workflow.add_argument("--architecture", action="store_true",
                          help="Architecture workflow: stages 0-13 (re-architecture)")
    workflow.add_argument("--bughunt", action="store_true",
                          help="Bug hunt workflow: sim + synthesis (3-10)")
    workflow.add_argument("--migrate", action="store_true",
                          help="Migrate old log-based project to state file format")
    workflow.add_argument("--hil", action="store_true",
                          help="Hardware-in-the-loop: stages 0,10,14-19 (requires --top)")
    workflow.add_argument("--validate", action="store_true",
                          help="Full validation: all stages including HIL (skips if no hardware)")

    parser.add_argument("--scope", type=str, default=None,
                        choices=["module", "block", "system"],
                        help="Design scope (module/block/system)")
    parser.add_argument("--stages", type=str, default="automated",
                        help="Stages to run: 'automated' or comma-separated (e.g. '0,4,9')")
    parser.add_argument("--files", type=str, nargs="*", default=None,
                        help="Specific files to pass to stage scripts")
    parser.add_argument("--top", type=str, default=None,
                        help="Top-level entity name (required for --hil, also used by stages 1, 10)")
    parser.add_argument("--part", type=str, default=None,
                        help="FPGA part override (default: from socks.json board.part)")
    parser.add_argument("--settings", type=str, default=None,
                        help="Path to Vivado settings64.sh")
    parser.add_argument("--clean", action="store_true",
                        help="Clean build artifacts before running pipeline")
    parser.add_argument("--new-session", action="store_true",
                        help="Create a fresh session.json before running")
    parser.add_argument("--max-iter", type=int, default=0,
                        help="Design-loop iteration cap (0 = unlimited)")
    parser.add_argument("--summary", action="store_true",
                        help="Print session summary and exit")
    args = parser.parse_args()

    project_dir = os.path.abspath(args.project_dir)

    # Scope conflict check: --scope must agree with socks.json if both present
    json_scope = get_scope(project_dir)
    if args.scope and json_scope and args.scope != json_scope:
        print(f"ERROR: --scope {args.scope} conflicts with socks.json scope \"{json_scope}\".")
        print(f"  A {json_scope}-scope project cannot be treated as {args.scope}.")
        return 1

    # --summary: print and exit
    if args.summary:
        print_session_summary(project_dir)
        return 0

    # --migrate: Claude-driven workflow (no automated stages)
    if args.migrate:
        print_header("SOCKS Migration")
        print(f"\n  Project: {project_dir}")

        project_scope = args.scope or get_scope(project_dir)
        if not project_scope:
            print("\n  ERROR: Scope not determined. Use --scope or create socks.json first.")
            print("  --scope module  (single entity or multi-module block)")
            print("  --scope system  (Xilinx IP block design, no custom RTL)")
            return 1

        if project_scope == "system":
            print(f"\n  Scope: system")
            print(f"\n  This is a Claude-driven workflow. Read references/migration-system.md")
            print(f"  and follow the steps:")
            print()
            print("  1. Classify: raw Vivado project or flat TCL/XDC")
            print("  2. Inventory TCL scripts, XDC, C drivers")
            print("  3. Present migration plan for approval")
            print("  4. Create directory structure and move files")
            print("  5. Update TCL path references (script_dir depth change)")
            print("  6. Create socks.json, .gitignore, docs")
            print(f"  7. Validate: python scripts/socks.py --project-dir {project_dir} --validate --clean")
        else:
            print(f"\n  Scope: {project_scope}")
            print(f"\n  This is a Claude-driven workflow. Read references/migration-module.md")
            print(f"  and follow the steps:")
            print()
            print("  1. Classify: legacy SOCKS or flat/3rd-party")
            print("  2. Clean generated artifacts (scripts/clean.py --project-dir . --all)")
            print("  3. Inventory and investigate")
            print("  4. Present migration plan for approval")
            print("  5. Apply migrations (use socks_lib.migrate_project() for state file stub)")
            print(f"  6. Validate: python scripts/socks.py --project-dir {project_dir} --validate --clean")

        print(f"\n  After completing all steps, run validation:")
        print(f"    python scripts/socks.py --project-dir {project_dir} --validate --clean")
        return 0

    # Determine stages from workflow flag or --stages
    active_workflow = None
    if args.design:
        active_workflow = "design"
    elif args.test:
        active_workflow = "test"
    elif args.architecture:
        active_workflow = "architecture"
    elif args.bughunt:
        active_workflow = "bughunt"
    elif args.hil:
        active_workflow = "hil"
        # --top is optional for system scope (uses dut.entity from socks.json)
        project_scope = args.scope or get_scope(project_dir)
        if not args.top and project_scope != "system":
            print("ERROR: --top is required for --hil (unless scope is system). "
                  "Provide the DUT entity name (e.g. --top uart_axi).")
            return 1
    elif args.validate:
        active_workflow = "validate"

    if active_workflow:
        # System scope uses *_system stage list
        if active_workflow in ("design", "validate"):
            project_scope = args.scope or get_scope(project_dir)
            if project_scope == "system":
                stages = WORKFLOW_STAGES[f"{active_workflow}_system"]
            else:
                stages = WORKFLOW_STAGES[active_workflow]
        else:
            stages = WORKFLOW_STAGES[active_workflow]
    else:
        stages = parse_stages(args.stages)

    print_header("SOCKS Pipeline Orchestrator")
    print(f"\n  Project: {project_dir}")
    if active_workflow:
        scope_str = f" ({args.scope})" if args.scope else ""
        print(f"  Workflow: --{active_workflow}{scope_str}")
    print(f"  Stages: {', '.join(str(s) for s in stages)}")

    if args.clean:
        clean_script = os.path.join(SCRIPT_DIR, "clean.py")
        if os.path.isfile(clean_script):
            print(f"\n  Cleaning build artifacts...")
            rc = subprocess.run(
                [sys.executable, clean_script, "--project-dir", project_dir],
                cwd=project_dir,
            ).returncode
            if rc != 0:
                print(f"  WARNING: Clean exited with code {rc}")
        else:
            print(f"  WARNING: clean.py not found at {clean_script}")

    # --- socks.json gate: all workflows except --design require it ---
    if active_workflow and active_workflow != "design":
        socks_cfg = load_project_config(project_dir)
        if socks_cfg is None:
            print(f"\n  ERROR: socks.json not found. Run --design first to create a "
                  f"project, or --migrate to import an existing one.")
            return 1

    # Resolve part: CLI override > socks.json > None
    resolved_part = args.part or get_part(project_dir)

    # --- State file & hash check ---
    sm = None
    if active_workflow:
        sm = StateManager(project_dir)
        # Read scope from socks.json if not provided via CLI
        effective_scope = args.scope or get_scope(project_dir)
        sm.ensure_state(scope=effective_scope, workflow=active_workflow)

        changed, re_entry = sm.detect_changes()

        if re_entry is None and not args.clean:
            # Nothing changed -- all cached
            print(f"\n  {green('CACHED')}: No input changes detected. "
                  f"Skipping pipeline.")
            for d, diff in changed.items():
                tag = "changed" if diff else "ok"
                print(f"    {d:6s} {tag}")
            return 0

        if re_entry is not None:
            changed_dirs = [d for d, diff in changed.items() if diff]
            print(f"\n  Hash check: {', '.join(changed_dirs)} changed")
            print(f"  Re-entry point: Stage {re_entry}")

            # Filter: only run stages >= re_entry
            stages = [s for s in stages if s >= re_entry]
            print(f"  Stages after filter: "
                  f"{', '.join(str(s) for s in stages)}")

    # For explicit --stages runs, still load StateManager if state exists
    # so stage results get recorded in project.json
    if sm is None:
        _test_sm = StateManager(project_dir)
        if _test_sm.load() is not None:
            sm = _test_sm

    # --- Session manifest (legacy, always maintained) ---
    if args.new_session:
        create_session(project_dir, max_iterations=args.max_iter)
    elif load_session(project_dir) is None:
        create_session(project_dir, max_iterations=args.max_iter)

    results = {}
    warnings = set()  # stages that passed with warnings (e.g. audit external)

    def build_stage_args(stage):
        """Build extra_args and reason for a stage. Returns (extra_args, reason, skip)."""
        extra_args = []
        reason = ""
        skip = None  # None = run, 0 = skip-pass, 1 = skip-fail

        # Hardware capability gate: skip hardware-dependent HIL stages
        if stage in (15, 17, 18, 19) and sm:
            hw = sm.get_hardware_capabilities()
            if hw is not None:
                if not hw.get("jtag_detected") and stage in (15, 17, 18, 19):
                    return [], "No JTAG target detected — skipped", 0
                if not hw.get("uart_detected") and stage in (17, 18):
                    return [], "No UART port detected — skipped", 0

        if stage == 0:
            reason = "Discover Vivado/Xsim tools"
            extra_args = ["--project-dir", project_dir]
            if args.settings:
                extra_args.extend(["--settings", args.settings])
                reason += f" (user-specified: {args.settings})"

        elif stage == 1:
            project_scope = get_scope(project_dir) or args.scope
            if project_scope == "system":
                extra_args = ["--project-dir", project_dir]
                if args.top:
                    extra_args.extend(["--top", args.top])
                reason = "Validate DESIGN-INTENT.md for system scope, set dut.entity"
            else:
                files = args.files or find_vhdl_files(project_dir)
                extra_args = list(files)
                if args.top:
                    extra_args = ["--top", args.top] + extra_args
                if files:
                    reason = f"Parse {len(files)} VHDL file(s), estimate DSP/resource usage"
                else:
                    reason = "Greenfield -- no VHDL yet, guidance creates ARCHITECTURE.md"

        elif stage == 3:
            files = args.files or find_vhdl_files(project_dir)
            if not files:
                return [], "No VHDL files found", 0
            extra_args = files
            reason = f"Lint {len(files)} VHDL file(s)"

        elif stage == 4:
            files = args.files or find_vhdl_files(project_dir)
            if not files:
                return [], "No VHDL files found", 0
            extra_args = files
            reason = f"Run 13 static synthesis checks on {len(files)} file(s)"

        elif stage == 5:
            tb_path = find_python_tb(project_dir)
            if tb_path:
                extra_args = [tb_path, "--project-dir", project_dir]
                reason = f"Re-run Python model: {os.path.relpath(tb_path, project_dir)}"
            else:
                return [], "No *_tb.py found in tb/", 0

        elif stage == 7:
            extra_args = ["--project-dir", project_dir]
            reason = "Compile VHDL+SV, elaborate, simulate"
            # Don't pass --top: let xsim.py auto-detect the TB module
            # from the SV filename (e.g. sdlc_axi_tb.sv -> sdlc_axi_tb)
            if args.settings:
                extra_args.extend(["--settings", args.settings])
            # Always enable VCD generation
            extra_args.append("--vcd")
            reason += " + VCD"
            # Pass signal map if found in tb/
            sig_maps = glob.glob(os.path.join(project_dir, "tb", "*signal*map*.json"))
            if sig_maps:
                map_file = sorted(sig_maps)[-1]
                extra_args.extend(["--signal-map", map_file])
                reason += f" (selective: {os.path.basename(map_file)})"

        elif stage == 8:
            vcd_candidates = glob.glob(os.path.join(project_dir, "build", "sim", "*.vcd"))
            signal_maps = glob.glob(os.path.join(project_dir, "build", "sim", "*signal*map*.json")) + \
                          glob.glob(os.path.join(project_dir, "tb", "*signal*map*.json"))
            if not vcd_candidates:
                return [], "No VCD file in build/sim/ -- Stage 7 should have generated one", 1
            if not signal_maps:
                return [], "No signal map JSON found -- create tb/vcd_signal_map.json", 1
            vcd_file = sorted(vcd_candidates)[-1]
            map_file = sorted(signal_maps)[-1]
            extra_args = [vcd_file, "--signal-map", map_file]
            reason = f"Verify waveform: {os.path.basename(vcd_file)} with {os.path.relpath(map_file, project_dir)}"

        elif stage == 9:
            sim_csvs = glob.glob(os.path.join(project_dir, "build", "sim", "*_sim.csv"))
            model_csvs = glob.glob(os.path.join(project_dir, "tb", "*_model.csv")) + \
                         glob.glob(os.path.join(project_dir, "build", "sim", "*_model.csv"))
            if not sim_csvs or not model_csvs:
                missing = []
                if not sim_csvs:
                    missing.append("build/sim/*_sim.csv")
                if not model_csvs:
                    missing.append("tb/*_model.csv")
                return [], f"Missing: {', '.join(missing)}", 0
            sim_csv = sorted(sim_csvs)[-1]
            model_csv = sorted(model_csvs)[-1]
            extra_args = [sim_csv, model_csv]
            reason = f"Compare {os.path.basename(sim_csv)} vs {os.path.basename(model_csv)}"

        elif stage == 10:
            project_scope = get_scope(project_dir) or args.scope

            # System scope: pass --project-dir only (TCL-driven flow)
            if project_scope == "system":
                synth_top = get_entity(project_dir) or "system_wrapper"
                extra_args = ["--project-dir", project_dir]
                if args.settings:
                    extra_args.extend(["--settings", args.settings])
                reason = f"Synthesise {synth_top} (system scope)"
            else:
                synth_top = args.top
                src_dir = os.path.join(project_dir, "src")
                if not os.path.isdir(src_dir):
                    src_dir = project_dir

                # Auto-detect RTL entity: strip _tb suffix, or scan src/ for entities
                if synth_top and synth_top.endswith("_tb"):
                    synth_top = synth_top[:-3]
                if not synth_top:
                    # Scan VHDL files for entity declarations
                    import re as _re
                    for vf in sorted(glob.glob(os.path.join(src_dir, "*.vhd"))):
                        with open(vf) as _f:
                            for _line in _f:
                                _m = _re.match(r'\s*entity\s+(\w+)\s+is\b',
                                               _line, _re.IGNORECASE)
                                if _m:
                                    synth_top = _m.group(1)
                                    break
                        if synth_top:
                            break
                if not synth_top:
                    return [], "--top not provided and no entity found in src/", 0

                # Resolve part: CLI > socks.json > error
                part = resolved_part
                if not part:
                    return [], "No --part provided and no board.part in socks.json", 0

                out_dir = os.path.join(project_dir, "build", "synth")
                os.makedirs(out_dir, exist_ok=True)
                extra_args = [
                    "--top", synth_top,
                    "--part", part,
                    "--src-dir", src_dir,
                    "--out-dir", out_dir,
                ]
                if args.settings:
                    extra_args.extend(["--settings", args.settings])
                reason = f"Synthesise {synth_top} for {part}"

        elif stage == 11:
            extra_args = ["--project-dir", project_dir]
            reason = "Scan project for raw EDA tool calls"

        elif stage == 13:
            extra_args = ["--project-dir", project_dir]
            reason = "Validate SOCKS skill + scan project for PII"

        elif stage == 14:
            project_scope = get_scope(project_dir) or args.scope
            hil_top = args.top
            if not hil_top:
                # System scope: read from socks.json
                hil_top = get_entity(project_dir)
            if not hil_top:
                return [], "ERROR: --top is required for --hil (no dut.entity in socks.json).", 1
            part = resolved_part
            if not part:
                return [], "No --part provided and no board.part in socks.json", 1
            extra_args = ["--project-dir", project_dir, "--top", hil_top, "--part", part]
            if args.settings:
                extra_args.extend(["--settings", args.settings])
            reason = "Create HIL Vivado project from hil.json"

        elif stage == 15:
            xpr_files = glob.glob(os.path.join(project_dir, "build", "hil",
                                               "vivado_project", "*.xpr"))
            if not xpr_files:
                return [], "No Vivado project in build/hil/ -- run Stage 14 first", 1
            extra_args = ["--project-dir", project_dir]
            if args.settings:
                extra_args.extend(["--settings", args.settings])
            reason = "Synthesize + implement + generate bitstream"

        elif stage == 16:
            xsa = os.path.join(project_dir, "build", "hil", "system_wrapper.xsa")
            if not os.path.isfile(xsa):
                return [], "No XSA in build/hil/ -- run Stage 15 first", 1
            extra_args = ["--project-dir", project_dir]
            if args.settings:
                extra_args.extend(["--settings", args.settings])
            # Forward --debug from environment (set by hil_ila.py rebuild)
            if os.environ.get("SOCKS_DEBUG_BUILD") == "1":
                extra_args.append("--debug")
                reason = "Build debug firmware via XSCT (HIL_DEBUG_MODE)"
            else:
                reason = "Build bare-metal firmware via XSCT"

        elif stage == 17:
            bit_files = glob.glob(os.path.join(project_dir, "build", "hil",
                                               "vivado_project", "*/impl_1/*.bit"))
            elf = os.path.join(project_dir, "build", "hil",
                               "vitis_ws", "hil_app", "Debug", "hil_app.elf")
            if not bit_files or not os.path.isfile(elf):
                return [], "Missing bitstream or ELF -- run Stages 15-16 first", 1
            extra_args = ["--project-dir", project_dir]
            reason = "Program board + run HIL test"

        elif stage == 18:
            project_scope = get_scope(project_dir) or args.scope
            extra_args = ["--project-dir", project_dir]
            if args.settings:
                extra_args.extend(["--settings", args.settings])
            if project_scope == "system":
                reason = "ILA multi-capture (capture-only, no VCD comparison)"
            else:
                reason = "ILA multi-capture (VCD required)"

        elif stage == 19:
            project_scope = get_scope(project_dir) or args.scope
            if project_scope == "system":
                return [], "Skipped for system scope (no VCD baseline)", 0
            extra_args = ["--project-dir", project_dir]
            reason = "ILA-vs-VCD verification (VCD required)"

        else:
            reason = "Scheduled stage"

        return extra_args, reason, skip

    import time as _time

    for stage in stages:
        extra_args, reason, skip = build_stage_args(stage)

        if skip is not None:
            log_transition(stage, reason, [], project_dir)
            print(f"      {yellow('N/A')}: {reason}")
            results[stage] = skip
            # Log to session manifest — non-applicable stages are PASS, not SKIP
            status_str = "pass" if skip == 0 else "fail"
            append_session_entry(
                project_dir, stage, status_str, source="script",
                note=reason)
            # Log to state file (no SKIP status — only PASS, FAIL, VIOLATED, UNKNOWN)
            if sm:
                label = STAGES[stage].label if stage in STAGES else ""
                sm.update_stage(stage, status_str.upper(),
                                source="script", note=reason, name=label)
            if skip != 0:
                print(f"\n  Stage {stage} {yellow('FAILED')} -- stopping pipeline")
                break
            continue

        log_transition(stage, reason, extra_args, project_dir)

        # Determine script override for scope-conditional stages
        script_override = None
        if stage == 1:
            project_scope = get_scope(project_dir) or args.scope
            if project_scope == "system":
                script_override = "architecture-system.py"
        elif stage == 10:
            project_scope = get_scope(project_dir) or args.scope
            if project_scope == "system":
                script_override = "synth-system.py"

        t0 = _time.monotonic()
        rc = run_stage(stage, project_dir, extra_args, script_override=script_override)
        elapsed = _time.monotonic() - t0

        # Stage 4 (audit) exit code 2 = external-only warnings (non-blocking)
        if stage == 4 and rc == 2:
            print(f"\n  Stage 4: external module warnings only -- continuing pipeline")
            results[stage] = 0
            warnings.add(stage)
        # Guidance stage WAITING (exit code 2) = files missing, stop pipeline
        elif rc == WAITING:
            results[stage] = WAITING
            sess_status = "waiting"
            append_session_entry(
                project_dir, stage, sess_status, source="guidance",
                note="Required files missing -- author and re-run")
            if sm:
                label = STAGES[stage].label if stage in STAGES else ""
                sm.update_stage(stage, "WAITING",
                                duration_seconds=elapsed,
                                source="guidance", note="Required files missing",
                                name=label)
            print(f"\n  Stage {stage} {yellow('WAITING')} -- "
                  f"author the required files and re-run the orchestrator")
            break
        else:
            results[stage] = rc

        # Log result to session manifest
        if results[stage] == 0:
            sess_status = "pass"
        elif results[stage] == WAITING:
            continue  # already logged above
        else:
            sess_status = "fail"
        # Determine log file for this run
        logs_dir = os.path.join(project_dir, "build", "logs")
        log_files = sorted(glob.glob(os.path.join(logs_dir, "pipeline_*.log")))
        latest_log = log_files[-1] if log_files else None
        append_session_entry(
            project_dir, stage, sess_status, source="script",
            note=reason, log_file=latest_log)

        # Log result to state file
        if sm:
            label = STAGES[stage].label if stage in STAGES else ""
            sm.update_stage(stage, sess_status.upper(),
                            duration_seconds=elapsed,
                            source="script", note=reason, name=label)

        # After Stage 0 passes, show project status dashboard
        if stage == 0 and results.get(stage) == 0:
            status_script = os.path.join(SCRIPT_DIR, "status.py")
            if os.path.isfile(status_script):
                subprocess.run(
                    [sys.executable, status_script,
                     "--project-dir", project_dir],
                    cwd=project_dir)

        if results[stage] != 0:
            print(f"\n  Stage {stage} {yellow('FAILED')} -- stopping pipeline")
            break

    # Summary
    print()
    print_header("Pipeline Summary")
    for stage in stages:
        if stage in results:
            if results[stage] == 0:
                status = pass_str()
            elif results[stage] == WAITING:
                status = yellow("WAIT")
            else:
                status = fail_str()
            label = STAGES[stage].label if stage in STAGES else f"Stage {stage}"
            print(f"  [{status}] Stage {stage:2d}: {label}")

    all_passed = all(rc == 0 for rc in results.values())
    print()
    print_separator()
    if all_passed:
        print(f"  RESULT: {pass_str()} -- all stages passed")
    else:
        print(f"  RESULT: {fail_str()} -- pipeline failed")
    print_separator()

    # --- Update state file hashes and next-action ---
    if sm:
        # Only update hashes if all stages passed. On failure, stale hashes
        # ensure the next run re-enters at the correct stage instead of
        # incorrectly reporting CACHED.
        if all_passed:
            sm.update_hashes()
            sm.clear_next_action()
        else:
            # Find the failed stage
            failed = [s for s, rc in results.items() if rc != 0]
            if failed:
                fs = failed[0]
                label = STAGES[fs].label if fs in STAGES else f"Stage {fs}"
                blocked = [s for s in stages if s > fs]
                sm.set_next_action(
                    f"Stage {fs} ({label}) FAILED. Fix and re-run.",
                    blocked_stages=blocked,
                    can_retry_from=fs)

    # Post-run: always run SOCKS self-audit (unless it was already a requested stage)
    if 13 not in stages:
        print(f"\n  Running post-pipeline SOCKS self-audit...")
        self_audit_path = os.path.join(SCRIPT_DIR, "self_audit.py")
        if os.path.isfile(self_audit_path):
            sa_rc = subprocess.run(
                [sys.executable, self_audit_path, "--project-dir", project_dir],
                cwd=project_dir,
            ).returncode
            if sa_rc != 0:
                print(f"\n  WARNING: SOCKS self-audit found issues")
                all_passed = False

    # Write logs
    write_pipeline_logs(project_dir, stages, results, warnings)

    return 0 if all_passed else 1


def _classify_stage(stage, results, warnings, skipped_stages):
    """Return (symbol, label) for a stage's outcome."""
    rc = results.get(stage)
    if rc is None:
        return "--", "---"
    if stage in warnings:
        return "!", "WARN"
    if stage in skipped_stages:
        return "o", "SKIP"
    if rc == 0:
        return "*", "PASS"
    return "X", "FAIL"


def write_pipeline_logs(project_dir, stages, results, warnings):
    """Write transition log and run chart to project build/logs/ directory."""
    logs_dir = os.path.join(project_dir, "build", "logs")
    os.makedirs(logs_dir, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_id = timestamp
    logged_stages = {e["stage"]: e for e in _transition_log}

    # Figure out which stages were skipped (logged a transition but
    # the reason indicates a skip -- no artifacts, no --top, etc.)
    skipped_stages = set()
    for stage in stages:
        entry = logged_stages.get(stage)
        if entry and results.get(stage) == 0:
            r = entry["reason"].lower()
            if any(kw in r for kw in [
                "no vcd", "no *_tb", "missing:", "--top not",
                "no vhdl", "no signal",
            ]):
                skipped_stages.add(stage)

    # --- Transition log ---
    log_path = os.path.join(logs_dir, f"pipeline_{run_id}.log")
    with open(log_path, "w") as f:
        f.write(f"SOCKS Pipeline Run — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"Project: {project_dir}\n")
        f.write(f"Stages:  {', '.join(str(s) for s in stages)}\n")
        f.write(f"\n{'='*72}\n\n")

        for entry in _transition_log:
            sym, lbl = _classify_stage(entry["stage"], results, warnings,
                                       skipped_stages)
            f.write(f"[{entry['time']}] Stage {entry['stage']:2d}: "
                    f"{entry['label']}  [{lbl}]\n")
            f.write(f"           Reason: {entry['reason']}\n")
            if entry["args"]:
                f.write(f"           Args:   {' '.join(entry['args'])}\n")
            f.write(f"\n")

    # --- Run chart ---
    chart_path = os.path.join(logs_dir, f"pipeline_{run_id}.chart")
    with open(chart_path, "w") as f:
        f.write(f"SOCKS Pipeline Run — "
                f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"Project: {os.path.basename(project_dir)}\n\n")

        cS, cN, cR, cD = 7, 26, 12, 44  # column widths
        border = f"+{'-'*(cS+2)}+{'-'*(cN+2)}+{'-'*(cR+2)}+{'-'*(cD+2)}+"
        header = (f"| {'Stage':^{cS}} | {'Name':<{cN}} "
                  f"| {'Result':^{cR}} | {'Reason / Args':<{cD}} |")

        f.write(border + "\n")
        f.write(header + "\n")
        f.write(border + "\n")

        for i, stage in enumerate(stages):
            entry = logged_stages.get(stage)
            label = STAGES[stage].label if stage in STAGES else f"Stage {stage}"

            sym, status = _classify_stage(stage, results, warnings,
                                          skipped_stages)
            status_str = f"{sym} {status}"

            reason_str = entry["reason"] if entry else ""
            args_str = " ".join(entry["args"]) if entry and entry["args"] else ""
            detail = reason_str
            if args_str:
                detail += f"  [{args_str}]"
            if len(detail) > cD:
                detail = detail[:cD - 3] + "..."

            f.write(f"| {stage:^{cS}} | {label:<{cN}} "
                    f"| {status_str:^{cR}} | {detail:<{cD}} |\n")

            # Flow arrow between stages
            if i < len(stages) - 1:
                f.write(f"| {'':^{cS}} | {'|':^{cN}} "
                        f"| {'':^{cR}} | {'':^{cD}} |\n")
                f.write(f"| {'':^{cS}} | {'v':^{cN}} "
                        f"| {'':^{cR}} | {'':^{cD}} |\n")

        f.write(border + "\n")

        # Tallies
        n_pass = sum(1 for s in stages
                     if results.get(s) == 0
                     and s not in warnings and s not in skipped_stages)
        n_warn = sum(1 for s in stages if s in warnings)
        n_skip = len(skipped_stages)
        n_fail = sum(1 for s in stages if results.get(s, 0) != 0)

        f.write(f"\nRESULT: {n_pass} passed, {n_warn} warned, "
                f"{n_skip} skipped, {n_fail} failed\n")
        f.write(f"\nLegend:  * PASS    ! WARN (external)    "
                f"o SKIP    X FAIL\n")

    print(f"\n  Logs written to:")
    print(f"    {log_path}")
    print(f"    {chart_path}")


if __name__ == "__main__":
    sys.exit(main())
