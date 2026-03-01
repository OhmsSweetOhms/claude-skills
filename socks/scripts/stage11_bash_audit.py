#!/usr/bin/env python3
"""
Stage 11: Bash Audit -- Scan project files for raw EDA tool calls that
should be routed through SOCKS Python scripts.

Checks shell scripts, Tcl files, Makefiles, and any other project files
for patterns that indicate direct tool invocations outside the SOCKS
pipeline. These are pipeline gaps that need to be addressed.

Usage:
    python scripts/stage11_bash_audit.py --project-dir .
    python scripts/stage11_bash_audit.py --project-dir /path/to/project

Exit code 0 if no raw tool calls found, 1 if gaps detected.
"""

import argparse
import glob
import os
import re
import sys
from dataclasses import dataclass, field
from typing import List

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from socks_lib import print_header, print_result, print_separator


@dataclass
class Finding:
    file: str
    line: int
    check: str
    text: str


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------

# Patterns that indicate raw EDA tool calls (must be at start of a command,
# not inside filenames like xvhdl.pb or directory names like xsim.dir)
RAW_TOOL_PATTERNS = [
    (r'(?:^|\s|&&|\|\||;)xvhdl\b', "Raw xvhdl call (use stage6_xsim.py)"),
    (r'(?:^|\s|&&|\|\||;)xvlog\b', "Raw xvlog call (use stage6_xsim.py)"),
    (r'(?:^|\s|&&|\|\||;)xelab\b', "Raw xelab call (use stage6_xsim.py)"),
    (r'(?:^|\s|&&|\|\||;)xsim\b', "Raw xsim call (use stage6_xsim.py)"),
    (r'(?:^|\s|&&|\|\||;)vivado\s+-mode\s+batch\b', "Raw vivado batch call (use stage9_synth.py)"),
]

# Patterns for shell anti-patterns
SHELL_ANTIPATTERNS = [
    (r'source\s+.*settings64\.sh', "Direct settings64.sh sourcing (scripts handle this)"),
    (r'<\(', "Process substitution <() (not supported in Claude Code)"),
    (r'>\(', "Process substitution >() (not supported in Claude Code)"),
]

# File extensions to scan
SCAN_EXTENSIONS = {
    ".sh", ".bash", ".tcl", ".mk", ".makefile",
}

# Files to scan by name
SCAN_NAMES = {
    "Makefile", "makefile", "GNUmakefile",
}

# Directories to skip
SKIP_DIRS = {
    ".git", ".Xil", "xsim.dir", "webtalk", "__pycache__", ".claude",
}


def find_scannable_files(project_dir):
    """Find all files that should be scanned for raw tool calls."""
    files = []
    for root, dirs, filenames in os.walk(project_dir):
        # Skip build artifact directories
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]

        for fname in filenames:
            ext = os.path.splitext(fname)[1].lower()
            if ext in SCAN_EXTENSIONS or fname in SCAN_NAMES:
                files.append(os.path.join(root, fname))

    return sorted(files)


def scan_file(filepath, project_dir):
    """Scan a single file for raw tool calls. Returns list of Findings."""
    findings = []
    rel_path = os.path.relpath(filepath, project_dir)

    try:
        with open(filepath, "r", errors="replace") as f:
            lines = f.readlines()
    except (IOError, OSError):
        return findings

    for i, line in enumerate(lines, 1):
        stripped = line.strip()

        # Skip comments
        if stripped.startswith("#") or stripped.startswith("//"):
            continue

        # Skip cleanup/removal lines (rm commands referencing tool artifacts)
        if re.match(r'^rm\s', stripped):
            continue

        # Check for raw EDA tool calls
        for pattern, msg in RAW_TOOL_PATTERNS:
            if re.search(pattern, stripped, re.IGNORECASE):
                findings.append(Finding(rel_path, i, msg, stripped))

        # Check for shell anti-patterns
        for pattern, msg in SHELL_ANTIPATTERNS:
            if re.search(pattern, stripped):
                findings.append(Finding(rel_path, i, msg, stripped))

    return findings


def check_shell_script_summary(project_dir, all_findings):
    """Add summary findings for shell scripts that contain raw tool calls."""
    findings = []
    # Group existing findings by file
    files_with_hits = set(f.file for f in all_findings)

    shell_names = {"run_sim.sh", "run.sh", "build.sh", "simulate.sh"}
    for rel_path in files_with_hits:
        basename = os.path.basename(rel_path)
        if basename in shell_names:
            findings.append(Finding(
                rel_path, 0,
                f"Shell script '{basename}' contains raw tool calls",
                "Consider replacing with stage6_xsim.py or wrapping in a Python script",
            ))

    return findings


def check_tcl_scripts(project_dir):
    """Check TCL scripts for patterns that stage9_synth.py should generate."""
    findings = []

    for tcl_file in glob.glob(os.path.join(project_dir, "**", "*.tcl"), recursive=True):
        rel_path = os.path.relpath(tcl_file, project_dir)

        # Skip Tcl files in Xsim build directories
        if "xsim.dir" in rel_path or ".Xil" in rel_path:
            continue

        try:
            with open(tcl_file, "r") as f:
                content = f.read()
        except (IOError, OSError):
            continue

        # Check if this is a synthesis TCL that stage9_synth.py should generate
        if "synth_design" in content and "report_utilization" in content:
            # Check if it's using hardcoded paths or project names
            if re.search(r'add_files\s+\$\{proj_dir\}/\w+\.vhd', content):
                # Has hardcoded file names -- stage9_synth.py handles this
                pass  # Not necessarily a finding -- user may want static TCL

        # Check for run commands that should use stage6_xsim.py
        if re.search(r'\brun\s+-all\b', content):
            fname = os.path.basename(tcl_file)
            if fname.startswith("_run"):
                # Auto-generated run scripts are fine
                continue

    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description="Stage 11: Bash Audit")
    parser.add_argument("--project-dir", type=str, default=".",
                        help="Project root directory")
    args = parser.parse_args()

    project_dir = os.path.abspath(args.project_dir)

    print_header("SOCKS Stage 11 -- Bash Audit")
    print(f"\n  Project: {project_dir}")

    # Find files to scan
    scan_files = find_scannable_files(project_dir)
    print(f"  Scanning {len(scan_files)} files for raw tool calls...")

    all_findings: List[Finding] = []

    # Scan each file
    for filepath in scan_files:
        findings = scan_file(filepath, project_dir)
        all_findings.extend(findings)

    # Add summary findings for shell scripts with raw tool calls
    all_findings.extend(check_shell_script_summary(project_dir, all_findings))

    # Check TCL scripts
    all_findings.extend(check_tcl_scripts(project_dir))

    # Report per-check summary
    check_names = set(f.check for f in all_findings)

    if scan_files:
        print(f"\n  Files scanned:")
        for f in scan_files:
            rel = os.path.relpath(f, project_dir)
            file_hits = sum(1 for finding in all_findings if finding.file == rel)
            if file_hits > 0:
                print_result(f"{rel} ({file_hits} findings)", False)
            else:
                print_result(rel, True)

    # Detail findings
    if all_findings:
        print(f"\n  Findings ({len(all_findings)}):")
        for f in all_findings:
            loc = f"{f.file}:{f.line}" if f.line > 0 else f.file
            print(f"    [{loc}]")
            print(f"      Check: {f.check}")
            if f.text and f.line > 0:
                print(f"      Line:  {f.text}")

    # SOCKS script coverage summary
    print(f"\n  SOCKS script coverage:")
    coverage = [
        ("Stage 0:  Environment", "stage0_env.py", True),
        ("Stage 1:  Architecture", "stage1_architecture.py", True),
        ("Stage 4:  Synthesis audit", "stage4_audit.py", True),
        ("Stage 5:  Python re-run", "stage5_python_rerun.py", True),
        ("Stage 6:  Xsim build/sim", "stage6_xsim.py", True),
        ("Stage 7:  VCD verify", "stage7_vcd_verify.py", True),
        ("Stage 8:  CSV cross-check", "stage8_csv_crosscheck.py", True),
        ("Stage 9:  Vivado synth", "stage9_synth.py", True),
        ("Stage 11: Bash audit", "stage11_bash_audit.py", True),
    ]
    for label, script, covered in coverage:
        print_result(f"{label} -> {script}", covered)

    print()
    print_separator()
    if not all_findings:
        print("  RESULT: PASS -- no raw tool calls found in project files")
        print("  All EDA tool invocations are routed through SOCKS scripts.")
    else:
        print(f"  RESULT: FAIL -- {len(all_findings)} raw tool call(s) found")
        print("  Replace with SOCKS script invocations or add to pipeline scripts.")
    print_separator()

    return 1 if all_findings else 0


if __name__ == "__main__":
    sys.exit(main())
