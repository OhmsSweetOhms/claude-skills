#!/usr/bin/env python3
"""
socks.py -- SOCKS Pipeline Orchestrator

Runs pipeline stages in sequence or individually.

Usage:
    python scripts/socks.py --project-dir . --stages all
    python scripts/socks.py --project-dir . --stages 0,4,9
    python scripts/socks.py --project-dir . --stages 4 --files src/*.vhd
    python scripts/socks.py --project-dir . --stages 0  # env check only

Available stages:
    0   Environment setup (Vivado/Xsim discovery)
    1   Architecture analysis (VHDL entity parsing, DSP estimates)
    4   Synthesis audit (12 static VHDL checks)
    5   Python testbench re-run
    6   Xsim build & simulate (compile + elaborate + run)
    7   VCD post-simulation verification
    8   CSV cross-check (sim vs model)
    9   Vivado synthesis (TCL generation + batch run)
    11  Bash audit (scan for raw tool calls in project files)
    13  SOCKS self-audit (skill consistency check)

Stages 2, 3, 10, 12 are guidance-only (Claude writes code/docs manually).

The self-audit (stage 13) always runs as the final stage when --stages all.
It also runs as a post-check after every orchestrator invocation.
"""

import argparse
import glob
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from socks_lib import print_header, print_separator, pass_str, fail_str, yellow, bold

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


def log_transition(stage_num, reason, extra_args, project_dir):
    """Log what stage is about to run, why, and what it receives."""
    label = (AUTOMATED_STAGES.get(stage_num, (None, None))[1] or
             GUIDANCE_STAGES.get(stage_num, f"Stage {stage_num}"))
    print(f"\n  {bold('>>>')} Stage {stage_num}: {label}")
    print(f"      Reason: {reason}")
    if extra_args:
        # Show args relative to project dir for readability
        display = []
        for a in extra_args:
            if isinstance(a, str) and a.startswith(project_dir):
                display.append(os.path.relpath(a, project_dir))
            else:
                display.append(str(a))
        print(f"      Args:   {' '.join(display)}")
    else:
        print(f"      Args:   (none)")

AUTOMATED_STAGES = {
    0: ("env.py", "Environment Setup"),
    1: ("architecture.py", "Architecture Analysis"),
    4: ("audit.py", "Synthesis Audit"),
    5: ("python_rerun.py", "Python Testbench Re-run"),
    6: ("xsim.py", "Xsim Build & Simulate"),
    7: ("vcd_verify.py", "VCD Verification"),
    8: ("csv_crosscheck.py", "CSV Cross-Check"),
    9: ("synth.py", "Vivado Synthesis"),
    11: ("bash_audit.py", "Bash Audit"),
    13: ("self_audit.py", "SOCKS Self-Audit"),
}

GUIDANCE_STAGES = {
    2: "VHDL Authoring (read references/vhdl.md)",
    3: "Python Testbench (read references/python-testbench.md)",
    10: "Bare-Metal C Driver (read references/baremetal.md)",
    12: "CLAUDE.md Documentation (read references/project-structure.md)",
}


def parse_stages(stages_str):
    """Parse stage specification: 'all', or comma-separated numbers."""
    if stages_str.strip().lower() == "all":
        return sorted(AUTOMATED_STAGES.keys())

    stages = []
    for part in stages_str.split(","):
        part = part.strip()
        if part:
            stages.append(int(part))
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
                        help="Top-level entity name (for stages 1, 9)")
    parser.add_argument("--part", type=str, default="xc7z020clg484-1",
                        help="FPGA part (for stage 9)")
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

    for stage in stages:
        extra_args = []

        if stage == 0:
            reason = "Discover Vivado/Xsim tools"
            if args.settings:
                extra_args = ["--settings", args.settings]
                reason += f" (user-specified: {args.settings})"

        elif stage == 1:
            files = args.files or find_vhdl_files(project_dir)
            if not files:
                log_transition(stage, "No VHDL files found", [], project_dir)
                print(f"      {yellow('SKIP')}: nothing to analyse")
                results[stage] = 0
                continue
            extra_args = files
            if args.top:
                extra_args = ["--top", args.top] + extra_args
            reason = f"Parse {len(files)} VHDL file(s), estimate DSP/resource usage"

        elif stage == 4:
            files = args.files or find_vhdl_files(project_dir)
            if not files:
                log_transition(stage, "No VHDL files found", [], project_dir)
                print(f"      {yellow('SKIP')}: nothing to audit")
                results[stage] = 0
                continue
            extra_args = files
            reason = f"Run 12 static synthesis checks on {len(files)} file(s)"

        elif stage == 5:
            tb_path = find_python_tb(project_dir)
            if tb_path:
                extra_args = [tb_path, "--project-dir", project_dir]
                reason = f"Re-run Python model: {os.path.relpath(tb_path, project_dir)}"
            else:
                log_transition(stage, "No *_tb.py found in tb/", [], project_dir)
                print(f"      {yellow('SKIP')}: no Python testbench")
                results[stage] = 0
                continue

        elif stage == 6:
            extra_args = ["--project-dir", project_dir]
            reason = "Compile VHDL+SV, elaborate, simulate"
            if args.top:
                extra_args.extend(["--top", args.top])
                reason += f" (top={args.top})"
            if args.settings:
                extra_args.extend(["--settings", args.settings])
            # Auto-enable VCD if stage 7 (VCD verify) is downstream
            if 7 in stages:
                extra_args.append("--vcd")
                reason += " + VCD (stage 7 downstream)"

        elif stage == 7:
            vcd_candidates = glob.glob(os.path.join(project_dir, "sim", "*.vcd"))
            signal_maps = glob.glob(os.path.join(project_dir, "sim", "*signal*map*.json")) + \
                          glob.glob(os.path.join(project_dir, "tb", "*signal*map*.json"))
            if not vcd_candidates:
                log_transition(stage, "No VCD file in sim/", [], project_dir)
                print(f"      {yellow('SKIP')}: run stage 6 first to produce VCD")
                results[stage] = 0
                continue
            if not signal_maps:
                log_transition(stage, "No signal map JSON found", [], project_dir)
                print(f"      {yellow('SKIP')}: create sim/signal_map.json to enable")
                results[stage] = 0
                continue
            vcd_file = sorted(vcd_candidates)[-1]
            map_file = sorted(signal_maps)[-1]
            extra_args = [vcd_file, "--signal-map", map_file]
            reason = f"Verify waveform: {os.path.basename(vcd_file)} with {os.path.relpath(map_file, project_dir)}"

        elif stage == 8:
            sim_csvs = glob.glob(os.path.join(project_dir, "sim", "*_sim.csv"))
            model_csvs = glob.glob(os.path.join(project_dir, "tb", "*_model.csv")) + \
                         glob.glob(os.path.join(project_dir, "sim", "*_model.csv"))
            if not sim_csvs or not model_csvs:
                missing = []
                if not sim_csvs:
                    missing.append("sim/*_sim.csv")
                if not model_csvs:
                    missing.append("tb/*_model.csv")
                log_transition(stage, f"Missing: {', '.join(missing)}", [], project_dir)
                print(f"      {yellow('SKIP')}: need both sim and model CSVs")
                results[stage] = 0
                continue
            sim_csv = sorted(sim_csvs)[-1]
            model_csv = sorted(model_csvs)[-1]
            extra_args = [sim_csv, model_csv]
            reason = f"Compare {os.path.basename(sim_csv)} vs {os.path.basename(model_csv)}"

        elif stage == 9:
            if not args.top:
                log_transition(stage, "--top not provided", [], project_dir)
                print(f"      {yellow('SKIP')}: pass --top to enable Vivado synthesis")
                results[stage] = 0
                continue
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

        log_transition(stage, reason, extra_args, project_dir)

        rc = run_stage(stage, project_dir, extra_args)

        # Stage 4 (audit) exit code 2 = external-only warnings (non-blocking)
        if stage == 4 and rc == 2:
            print(f"\n  Stage 4: external module warnings only -- continuing pipeline")
            results[stage] = 0  # treat as pass for pipeline flow
        else:
            results[stage] = rc

        if results[stage] != 0:
            print(f"\n  Stage {stage} {yellow('FAILED')} -- stopping pipeline")
            break

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

    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
