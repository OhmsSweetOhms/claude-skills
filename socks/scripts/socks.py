#!/usr/bin/env python3
"""
socks.py -- SOCKS Pipeline Orchestrator

Runs pipeline stages in sequence or individually.

Usage:
    python scripts/socks.py --project-dir . --stages all
    python scripts/socks.py --project-dir . --stages 0,4,7
    python scripts/socks.py --project-dir . --stages 4 --files src/*.vhd
    python scripts/socks.py --project-dir . --stages 0  # env check only

Available stages:
    0   Environment setup (Vivado/Xsim discovery)
    1   Architecture analysis (VHDL entity parsing, DSP estimates)
    4   Synthesis audit (12 static VHDL checks)
    5   Python testbench re-run
    7   Xsim build & simulate (compile + elaborate + run)
    8   VCD post-simulation verification
    9   CSV cross-check (sim vs model)
    10  Vivado synthesis (TCL generation + batch run)
    11  Bash audit (scan for raw tool calls in project files)
    13  SOCKS self-audit (skill consistency check)

Stages 2, 3, 6, 12 are guidance-only (Claude writes code/docs manually).

The self-audit (stage 13) always runs as the final stage when --stages all.
It also runs as a post-check after every orchestrator invocation.
"""

import argparse
import glob
import os
import subprocess
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from socks_lib import print_header, print_separator, pass_str, fail_str, yellow, bold

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# Collected during the run, written to logs/ at the end
_transition_log = []


def log_transition(stage_num, reason, extra_args, project_dir):
    """Log what stage is about to run, why, and what it receives."""
    label = (AUTOMATED_STAGES.get(stage_num, (None, None))[1] or
             GUIDANCE_STAGES.get(stage_num, f"Stage {stage_num}"))
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

AUTOMATED_STAGES = {
    0: ("env.py", "Environment Setup"),
    1: ("architecture.py", "Architecture Analysis"),
    4: ("audit.py", "Synthesis Audit"),
    5: ("python_rerun.py", "Python Testbench Re-run"),
    7: ("xsim.py", "Xsim Build & Simulate"),
    8: ("vcd_verify.py", "VCD Verification"),
    9: ("csv_crosscheck.py", "CSV Cross-Check"),
    10: ("synth.py", "Vivado Synthesis"),
    11: ("bash_audit.py", "Bash Audit"),
    13: ("self_audit.py", "SOCKS Self-Audit"),
}

GUIDANCE_STAGES = {
    2: "VHDL Authoring (read references/vhdl.md)",
    3: "VHDL Linter (read references/linter.md)",
    6: "Bare-Metal C Driver (read references/baremetal.md)",
    12: "CLAUDE.md Documentation (read references/project-structure.md)",
}

# Verification loop: stages 5-9 form a consistency group.
# Python model (5) is the spec. C driver (6) provides DPI-C helpers.
# Xsim (7) must match the model. VCD (8) confirms the waveform.
# CSV cross-check (9) compares sim vs model numerically.
# If any stage fails, the loop restarts from stage 5.
VERIFY_LOOP = [5, 6, 7, 8, 9]
VERIFY_MAX_RETRIES = 2  # max times the loop can restart before giving up


def parse_stages(stages_str):
    """Parse stage specification: 'all', or comma-separated numbers.

    If any stage in the verification loop (5-8) is requested, auto-expand
    to include all prerequisite loop stages. E.g. requesting stage 7
    expands to 5,6,7. Requesting stage 8 expands to 5,6,7,8.
    """
    if stages_str.strip().lower() == "all":
        return sorted(AUTOMATED_STAGES.keys())

    stages = []
    for part in stages_str.split(","):
        part = part.strip()
        if part:
            stages.append(int(part))

    # Auto-expand: if any verify loop stage is present, ensure all
    # prerequisite loop stages are included before it
    requested_loop = [s for s in stages if s in VERIFY_LOOP]
    if requested_loop:
        max_loop = max(requested_loop)
        needed = [s for s in VERIFY_LOOP if s <= max_loop]
        expanded = []
        insert_done = False
        for s in stages:
            if s in VERIFY_LOOP and not insert_done:
                # Insert the full prerequisite chain here
                for n in needed:
                    if n not in expanded:
                        expanded.append(n)
                insert_done = True
            elif s not in VERIFY_LOOP:
                expanded.append(s)
        # Deduplicate while preserving order
        seen = set()
        stages = []
        for s in expanded:
            if s not in seen:
                seen.add(s)
                stages.append(s)

    return stages


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
    """Run an automated stage script. Returns exit code."""
    if stage_num not in AUTOMATED_STAGES:
        if stage_num in GUIDANCE_STAGES:
            print(f"\n  Stage {stage_num}: {GUIDANCE_STAGES[stage_num]}")
            print(f"  (Guidance-only -- no automated script)")
            return 0
        print(f"\n  ERROR: Unknown stage {stage_num}")
        return 1

    script_name, description = AUTOMATED_STAGES[stage_num]
    script_path = os.path.join(SCRIPT_DIR, script_name)

    if not os.path.isfile(script_path):
        print(f"\n  ERROR: Script not found: {script_path}")
        return 1

    cmd = [sys.executable, script_path]
    if extra_args:
        cmd.extend(extra_args)

    result = subprocess.run(cmd, cwd=project_dir)
    return result.returncode


def main() -> int:
    parser = argparse.ArgumentParser(
        description="SOCKS Pipeline Orchestrator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__)
    parser.add_argument("--project-dir", type=str, default=".",
                        help="Project root directory (default: current dir)")
    parser.add_argument("--stages", type=str, default="all",
                        help="Stages to run: 'all' or comma-separated (e.g. '0,4,9')")
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
    args = parser.parse_args()

    project_dir = os.path.abspath(args.project_dir)
    stages = parse_stages(args.stages)

    print_header("SOCKS Pipeline Orchestrator")
    print(f"\n  Project: {project_dir}")
    print(f"  Stages: {', '.join(str(s) for s in stages)}")

    # Show verify loop expansion if it happened
    requested_loop = [s for s in stages if s in VERIFY_LOOP]
    if requested_loop:
        print(f"  Verify loop: stages {', '.join(str(s) for s in VERIFY_LOOP[:VERIFY_LOOP.index(max(requested_loop))+1])}"
              f" (all must pass together)")

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

    results = {}
    warnings = set()  # stages that passed with warnings (e.g. audit external)
    verify_retries = 0

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
            if not files:
                return [], "No VHDL files found", 0
            extra_args = files
            if args.top:
                extra_args = ["--top", args.top] + extra_args
            reason = f"Parse {len(files)} VHDL file(s), estimate DSP/resource usage"

        elif stage == 4:
            files = args.files or find_vhdl_files(project_dir)
            if not files:
                return [], "No VHDL files found", 0
            extra_args = files
            reason = f"Run 12 static synthesis checks on {len(files)} file(s)"

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
            if args.top:
                extra_args.extend(["--top", args.top])
                reason += f" (top={args.top})"
            if args.settings:
                extra_args.extend(["--settings", args.settings])
            if 8 in stages:
                extra_args.append("--vcd")
                reason += " + VCD (stage 8 downstream)"

        elif stage == 8:
            vcd_candidates = glob.glob(os.path.join(project_dir, "sim", "*.vcd"))
            signal_maps = glob.glob(os.path.join(project_dir, "sim", "*signal*map*.json")) + \
                          glob.glob(os.path.join(project_dir, "tb", "*signal*map*.json"))
            if not vcd_candidates:
                return [], "No VCD file in sim/", 0
            if not signal_maps:
                return [], "No signal map JSON found", 0
            vcd_file = sorted(vcd_candidates)[-1]
            map_file = sorted(signal_maps)[-1]
            extra_args = [vcd_file, "--signal-map", map_file]
            reason = f"Verify waveform: {os.path.basename(vcd_file)} with {os.path.relpath(map_file, project_dir)}"

        elif stage == 9:
            sim_csvs = glob.glob(os.path.join(project_dir, "sim", "*_sim.csv"))
            model_csvs = glob.glob(os.path.join(project_dir, "tb", "*_model.csv")) + \
                         glob.glob(os.path.join(project_dir, "sim", "*_model.csv"))
            if not sim_csvs or not model_csvs:
                missing = []
                if not sim_csvs:
                    missing.append("sim/*_sim.csv")
                if not model_csvs:
                    missing.append("tb/*_model.csv")
                return [], f"Missing: {', '.join(missing)}", 0
            sim_csv = sorted(sim_csvs)[-1]
            model_csv = sorted(model_csvs)[-1]
            extra_args = [sim_csv, model_csv]
            reason = f"Compare {os.path.basename(sim_csv)} vs {os.path.basename(model_csv)}"

        elif stage == 10:
            if not args.top:
                return [], "--top not provided", 0
            src_dir = os.path.join(project_dir, "src")
            if not os.path.isdir(src_dir):
                src_dir = project_dir
            out_dir = os.path.join(project_dir, "sim")
            os.makedirs(out_dir, exist_ok=True)
            extra_args = [
                "--top", args.top,
                "--part", args.part,
                "--src-dir", src_dir,
                "--out-dir", out_dir,
            ]
            if args.settings:
                extra_args.extend(["--settings", args.settings])
            reason = f"Synthesise {args.top} for {args.part}"

        elif stage == 11:
            extra_args = ["--project-dir", project_dir]
            reason = "Scan project for raw EDA tool calls"

        elif stage == 13:
            reason = "Validate SOCKS skill internal consistency"

        else:
            reason = "Scheduled stage"

        return extra_args, reason, skip

    idx = 0
    while idx < len(stages):
        stage = stages[idx]
        extra_args, reason, skip = build_stage_args(stage)

        if skip is not None:
            log_transition(stage, reason, [], project_dir)
            print(f"      {yellow('SKIP')}: {reason}")
            results[stage] = skip
            if skip != 0:
                print(f"\n  Stage {stage} {yellow('FAILED')} -- stopping pipeline")
                break
            idx += 1
            continue

        # On verify loop retry, annotate the reason
        if stage in VERIFY_LOOP and verify_retries > 0:
            reason = f"[RETRY {verify_retries}] {reason}"

        log_transition(stage, reason, extra_args, project_dir)

        rc = run_stage(stage, project_dir, extra_args)

        # Stage 4 (audit) exit code 2 = external-only warnings (non-blocking)
        if stage == 4 and rc == 2:
            print(f"\n  Stage 4: external module warnings only -- continuing pipeline")
            results[stage] = 0
            warnings.add(stage)
        else:
            results[stage] = rc

        if results[stage] != 0:
            # Verification loop failure: restart from stage 5
            if stage in VERIFY_LOOP and verify_retries < VERIFY_MAX_RETRIES:
                verify_retries += 1
                failed_label = AUTOMATED_STAGES.get(stage, (None, f"Stage {stage}"))[1]
                print(f"\n  {bold('<<< VERIFY LOOP RESTART')} "
                      f"(attempt {verify_retries}/{VERIFY_MAX_RETRIES}) >>>")
                print(f"      Stage {stage} ({failed_label}) failed")
                print(f"      Restarting from stage 5 (Python model is the spec)")
                print(f"      Fix the issue, then all verify stages re-run")
                # Rewind to stage 5 in the stage list
                try:
                    idx = stages.index(5)
                except ValueError:
                    print(f"      ERROR: stage 5 not in pipeline, cannot restart")
                    break
                # Clear results for all verify loop stages so they re-run clean
                for vs in VERIFY_LOOP:
                    results.pop(vs, None)
                continue
            else:
                if stage in VERIFY_LOOP and verify_retries >= VERIFY_MAX_RETRIES:
                    print(f"\n  Verify loop exhausted {VERIFY_MAX_RETRIES} retries"
                          f" -- stopping pipeline")
                else:
                    print(f"\n  Stage {stage} {yellow('FAILED')} -- stopping pipeline")
                break

        idx += 1

    # Summary
    print()
    print_header("Pipeline Summary")
    for stage in stages:
        if stage in results:
            status = pass_str() if results[stage] == 0 else fail_str()
            label = (AUTOMATED_STAGES.get(stage, (None, None))[1] or
                     GUIDANCE_STAGES.get(stage, f"Stage {stage}"))
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
    write_pipeline_logs(project_dir, stages, results, warnings, verify_retries)

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


def write_pipeline_logs(project_dir, stages, results, warnings, verify_retries):
    """Write transition log and run chart to project logs/ directory."""
    logs_dir = os.path.join(project_dir, "logs")
    os.makedirs(logs_dir, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_id = timestamp
    logged_stages = {e["stage"]: e for e in _transition_log}

    # Figure out which stages were skipped (logged a transition but
    # the reason indicates a skip — no artifacts, no --top, etc.)
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
        if verify_retries > 0:
            f.write(f"Verify loop retries: {verify_retries}\n")
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

        cS, cN, cR, cD = 7, 22, 12, 44  # column widths
        border = f"+{'-'*(cS+2)}+{'-'*(cN+2)}+{'-'*(cR+2)}+{'-'*(cD+2)}+"
        header = (f"| {'Stage':^{cS}} | {'Name':<{cN}} "
                  f"| {'Result':^{cR}} | {'Reason / Args':<{cD}} |")

        f.write(border + "\n")
        f.write(header + "\n")
        f.write(border + "\n")

        for i, stage in enumerate(stages):
            entry = logged_stages.get(stage)
            label = AUTOMATED_STAGES.get(stage, (None, None))[1]
            if not label:
                # Guidance stages: strip the "(read ...)" suffix for the chart
                g = GUIDANCE_STAGES.get(stage, f"Stage {stage}")
                label = g.split(" (read ")[0] if " (read " in g else g

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
        if verify_retries > 0:
            f.write(f"Verify loop restarted {verify_retries} time(s)\n")
        f.write(f"\nLegend:  * PASS    ! WARN (external)    "
                f"o SKIP    X FAIL\n")

    print(f"\n  Logs written to:")
    print(f"    {log_path}")
    print(f"    {chart_path}")


if __name__ == "__main__":
    sys.exit(main())
