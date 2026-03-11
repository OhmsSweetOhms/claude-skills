#!/usr/bin/env python3
"""
socks.py -- SOCKS Pipeline Orchestrator

Runs pipeline stages in sequence or individually.

Usage:
    python scripts/socks.py --project-dir . --stages automated
    python scripts/socks.py --project-dir . --stages 0,4,7
    python scripts/socks.py --project-dir . --stages 4 --files src/*.vhd

Stage keywords:
    automated   All stages with scripts (default)
    0,4,7       Specific stages (comma-separated, no auto-expansion)

Available stages:
    0   Environment setup (script)
    1   Architecture analysis (script + guidance)
    2   Write/Modify RTL (guidance only)
    3   VHDL Linter (script + guidance)
    4   Synthesis audit (script)
    5   Python testbench (script + guidance)
    6   Bare-Metal C driver (guidance only)
    7   SV/Xsim testbench (script + guidance)
    8   VCD verification (script)
    9   CSV cross-check (script)
    10  Vivado synthesis (script)
    11  Bash audit (script)
    12  CLAUDE.md documentation (guidance only)
    13  SOCKS self-audit (script)

Stages 2-9 form the design loop. Claude decides re-entry on failure.
Guidance-only stages (2, 6, 12) are driven by Claude reading SKILL.md,
not by this orchestrator.
"""

import argparse
import glob
import os
import subprocess
import sys
from collections import namedtuple
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from socks_lib import print_header, print_separator, pass_str, fail_str, yellow, bold
from session import load_session, create_session, append_session_entry

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# Collected during the run, written to logs/ at the end
_transition_log = []

# ---------------------------------------------------------------------------
# Unified stage definitions
# ---------------------------------------------------------------------------

StageDef = namedtuple("StageDef", ["label", "script", "guidance"], defaults=[None, None])

STAGES = {
    0:  StageDef("Environment Setup",       script="env.py"),
    1:  StageDef("Architecture Analysis",    script="architecture.py",
                 guidance="RTL + TB architecture, Mermaid diagrams, rate analysis (read references/architecture-diagrams.md). Enter plan mode for user approval before proceeding."),
    2:  StageDef("Write/Modify RTL",         guidance="read references/vhdl.md"),
    3:  StageDef("VHDL Linter",              script="linter.py",
                 guidance="read references/linter.md"),
    4:  StageDef("Synthesis Audit",          script="audit.py"),
    5:  StageDef("Python Testbench",         script="python_rerun.py",
                 guidance="Write/update cycle-accurate Python model (read references/python-testbench.md)"),
    6:  StageDef("Bare-Metal C Driver",      guidance="read references/baremetal.md"),
    7:  StageDef("SV/Xsim Testbench",        script="xsim.py",
                 guidance="Write/update SV testbench (read references/xsim.md)"),
    8:  StageDef("VCD Verification",         script="vcd_verify.py"),
    9:  StageDef("CSV Cross-Check",          script="csv_crosscheck.py"),
    10: StageDef("Vivado Synthesis",         script="synth.py"),
    11: StageDef("Bash Audit",               script="bash_audit.py"),
    12: StageDef("CLAUDE.md Documentation",  guidance="read references/project-structure.md"),
    13: StageDef("SOCKS Self-Audit",         script="self_audit.py"),
}

DESIGN_LOOP = [2, 3, 4, 5, 6, 7, 8, 9]  # informational only, not mechanical


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


def run_stage(stage_num, project_dir, extra_args=None):
    """Run a pipeline stage. Returns exit code.

    Guidance-only stages (no script) print their label and return 0.
    """
    if stage_num not in STAGES:
        print(f"\n  ERROR: Unknown stage {stage_num}")
        return 1

    stage = STAGES[stage_num]

    # Guidance-only stage with no script
    if not stage.script:
        print(f"\n  Stage {stage_num}: {stage.label}")
        print(f"  (Guidance-only -- no automated script)")
        return 0

    script_path = os.path.join(SCRIPT_DIR, stage.script)

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
    parser.add_argument("--stages", type=str, default="automated",
                        help="Stages to run: 'automated' or comma-separated (e.g. '0,4,9')")
    parser.add_argument("--files", type=str, nargs="*", default=None,
                        help="Specific files to pass to stage scripts")
    parser.add_argument("--top", type=str, default=None,
                        help="Top-level entity name (for stages 1, 10)")
    parser.add_argument("--part", type=str, default="xc7z020clg484-1",
                        help="FPGA part (for stage 10)")
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

    # --summary: print and exit
    if args.summary:
        print_session_summary(project_dir)
        return 0

    stages = parse_stages(args.stages)

    print_header("SOCKS Pipeline Orchestrator")
    print(f"\n  Project: {project_dir}")
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

    # --- Session manifest ---
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

        if stage == 0:
            reason = "Discover Vivado/Xsim tools"
            if args.settings:
                extra_args = ["--settings", args.settings]
                reason += f" (user-specified: {args.settings})"

        elif stage == 1:
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

            out_dir = os.path.join(project_dir, "build", "synth")
            os.makedirs(out_dir, exist_ok=True)
            extra_args = [
                "--top", synth_top,
                "--part", args.part,
                "--src-dir", src_dir,
                "--out-dir", out_dir,
            ]
            if args.settings:
                extra_args.extend(["--settings", args.settings])
            reason = f"Synthesise {synth_top} for {args.part}"

        elif stage == 11:
            extra_args = ["--project-dir", project_dir]
            reason = "Scan project for raw EDA tool calls"

        elif stage == 13:
            reason = "Validate SOCKS skill internal consistency"

        else:
            reason = "Scheduled stage"

        return extra_args, reason, skip

    for stage in stages:
        extra_args, reason, skip = build_stage_args(stage)

        if skip is not None:
            log_transition(stage, reason, [], project_dir)
            print(f"      {yellow('SKIP')}: {reason}")
            results[stage] = skip
            # Log skip to session manifest
            status_str = "skip" if skip == 0 else "fail"
            append_session_entry(
                project_dir, stage, status_str, source="script",
                note=reason)
            if skip != 0:
                print(f"\n  Stage {stage} {yellow('FAILED')} -- stopping pipeline")
                break
            continue

        log_transition(stage, reason, extra_args, project_dir)

        rc = run_stage(stage, project_dir, extra_args)

        # Stage 4 (audit) exit code 2 = external-only warnings (non-blocking)
        if stage == 4 and rc == 2:
            print(f"\n  Stage 4: external module warnings only -- continuing pipeline")
            results[stage] = 0
            warnings.add(stage)
        else:
            results[stage] = rc

        # Log result to session manifest
        if results[stage] == 0:
            sess_status = "pass"
        else:
            sess_status = "fail"
        # Determine log file for this run
        logs_dir = os.path.join(project_dir, "build", "logs")
        log_files = sorted(glob.glob(os.path.join(logs_dir, "pipeline_*.log")))
        latest_log = log_files[-1] if log_files else None
        append_session_entry(
            project_dir, stage, sess_status, source="script",
            note=reason, log_file=latest_log)

        if results[stage] != 0:
            print(f"\n  Stage {stage} {yellow('FAILED')} -- stopping pipeline")
            break

    # Summary
    print()
    print_header("Pipeline Summary")
    for stage in stages:
        if stage in results:
            status = pass_str() if results[stage] == 0 else fail_str()
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

    # Post-run: always run SOCKS self-audit (unless it was already a requested stage)
    if 13 not in stages:
        print(f"\n  Running post-pipeline SOCKS self-audit...")
        self_audit_path = os.path.join(SCRIPT_DIR, "self_audit.py")
        if os.path.isfile(self_audit_path):
            sa_rc = subprocess.run(
                [sys.executable, self_audit_path],
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
