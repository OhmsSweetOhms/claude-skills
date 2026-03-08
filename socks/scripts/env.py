#!/usr/bin/env python3
"""
Stage 0: Environment Preflight -- Verify all tools, dependencies, and project
structure required by the SOCKS pipeline.

Checks:
  1. Vivado discovery and tool verification (xvhdl, xvlog, xelab, xsim, vivado)
  2. Vivado version (warns if not 2023.2)
  3. Python version (>= 3.8)
  4. Python standard library modules used by SOCKS scripts
  5. SOCKS skill integrity (all scripts and references exist)
  6. Project structure (src/, tb/, build/, sw/, docs/ if --project-dir given)

Usage:
    python scripts/env.py
    python scripts/env.py --settings /tools/Xilinx/Vivado/2023.2/settings64.sh
    python scripts/env.py --project-dir /path/to/my_project

Exit codes:
    0  All checks passed
    1  Critical failure (missing Vivado, wrong Python, missing SOCKS scripts)
    2  Warnings only (version mismatch, missing optional project dirs)
"""

import argparse
import importlib
import os
import re
import sys

# Allow importing socks_lib from the same directory
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from socks_lib import (
    find_vivado_settings, verify_tools, get_vivado_version,
    print_header, print_result, print_separator, pass_str, fail_str,
    REQUIRED_TOOLS,
)

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SKILL_DIR = os.path.dirname(SCRIPT_DIR)

# Vivado version the pipeline is tested against
EXPECTED_VIVADO_VERSION = "2023.2"

# Minimum Python version
MIN_PYTHON = (3, 8)

# Standard library modules SOCKS scripts import (beyond builtins)
REQUIRED_STDLIB = [
    "argparse", "collections", "csv", "dataclasses", "datetime",
    "glob", "json", "os", "re", "shutil", "subprocess", "sys",
    "typing",
]

# Scripts that must exist in the socks scripts/ directory
# (derived from STAGES dict in socks.py + supporting scripts)
REQUIRED_SCRIPTS = [
    "socks.py",
    "socks_lib.py",
    "env.py",
    "architecture.py",
    "audit.py",
    "python_rerun.py",
    "xsim.py",
    "vcd_verify.py",
    "csv_crosscheck.py",
    "synth.py",
    "bash_audit.py",
    "self_audit.py",
    "clean.py",
    "build.py",
]

# Reference files that must exist
REQUIRED_REFERENCES = [
    "architecture-diagrams.md",
    "baremetal.md",
    "design-loop.md",
    "dpll.md",
    "linter.md",
    "project-structure.md",
    "python-testbench.md",
    "synthesis.md",
    "vcd-verify.md",
    "vhdl.md",
    "xsim.md",
]

# Project directories (required and optional)
PROJECT_DIRS_REQUIRED = ["src"]
PROJECT_DIRS_OPTIONAL = ["tb", "build", "sw", "docs"]
PROJECT_FILES_OPTIONAL = ["CLAUDE.md", ".gitignore"]

# Fingerprint engine (shared with git-fingerprint-guard hook)
FINGERPRINT_ENGINE = os.path.join(
    str(os.path.expanduser("~")), ".claude", "hooks", "fingerprint_engine.py"
)


def check_vivado(settings_path):
    """Check Vivado installation. Returns (passed, warnings, info_lines)."""
    info = []
    warnings = []
    passed = True

    if settings_path is None:
        settings_path = find_vivado_settings()

    if settings_path is None:
        info.append(("Vivado settings64.sh", False, "NOT FOUND"))
        info.append(("", False, "Searched:"))
        info.append(("", False, "  /tools/Xilinx/Vivado/*/settings64.sh"))
        info.append(("", False, "  /opt/Xilinx/Vivado/*/settings64.sh"))
        info.append(("", False, "  ~/Xilinx/Vivado/*/settings64.sh"))
        info.append(("", False, "  Use --settings to specify manually"))
        return False, warnings, info, None

    if not os.path.isfile(settings_path):
        info.append(("Vivado settings64.sh", False, f"File not found: {settings_path}"))
        return False, warnings, info, None

    info.append(("Vivado settings64.sh", True, settings_path))

    # Version check
    version = get_vivado_version(settings_path)
    if version:
        if version == EXPECTED_VIVADO_VERSION:
            info.append(("Vivado version", True, version))
        else:
            info.append(("Vivado version", True,
                         f"{version} (expected {EXPECTED_VIVADO_VERSION})"))
            warnings.append(f"Vivado {version} found; pipeline tested with {EXPECTED_VIVADO_VERSION}")
    else:
        info.append(("Vivado version", False, "Could not determine version"))
        warnings.append("Could not determine Vivado version from path")

    # Tool verification
    tools = verify_tools(settings_path)
    for tool in REQUIRED_TOOLS:
        path = tools.get(tool)
        if path:
            info.append((tool, True, path))
        else:
            info.append((tool, False, "NOT FOUND"))
            passed = False

    return passed, warnings, info, settings_path


def check_python():
    """Check Python version and stdlib modules. Returns (passed, warnings, info)."""
    info = []
    warnings = []
    passed = True

    # Version
    ver = sys.version_info
    ver_str = f"{ver.major}.{ver.minor}.{ver.micro}"
    if (ver.major, ver.minor) >= MIN_PYTHON:
        info.append(("Python version", True,
                     f"{ver_str} ({sys.executable})"))
    else:
        info.append(("Python version", False,
                     f"{ver_str} (need >= {MIN_PYTHON[0]}.{MIN_PYTHON[1]})"))
        passed = False

    # Stdlib modules
    missing_modules = []
    for mod_name in REQUIRED_STDLIB:
        try:
            importlib.import_module(mod_name)
        except ImportError:
            missing_modules.append(mod_name)

    if missing_modules:
        info.append(("Python stdlib", False,
                     f"Missing: {', '.join(missing_modules)}"))
        passed = False
    else:
        info.append(("Python stdlib", True,
                     f"All {len(REQUIRED_STDLIB)} modules available"))

    return passed, warnings, info


def check_socks_scripts():
    """Check SOCKS skill scripts exist. Returns (passed, warnings, info)."""
    info = []
    warnings = []
    passed = True

    missing = []
    for script in REQUIRED_SCRIPTS:
        path = os.path.join(SCRIPT_DIR, script)
        if not os.path.isfile(path):
            missing.append(script)
            passed = False

    if missing:
        info.append(("SOCKS scripts", False,
                     f"Missing {len(missing)}: {', '.join(missing)}"))
    else:
        info.append(("SOCKS scripts", True,
                     f"All {len(REQUIRED_SCRIPTS)} scripts present"))

    return passed, warnings, info


def check_socks_references():
    """Check SOCKS reference files exist. Returns (passed, warnings, info)."""
    info = []
    warnings = []
    passed = True

    ref_dir = os.path.join(SKILL_DIR, "references")
    if not os.path.isdir(ref_dir):
        info.append(("SOCKS references", False, "references/ directory not found"))
        return False, warnings, info

    missing = []
    for ref in REQUIRED_REFERENCES:
        path = os.path.join(ref_dir, ref)
        if not os.path.isfile(path):
            missing.append(ref)
            passed = False

    if missing:
        info.append(("SOCKS references", False,
                     f"Missing {len(missing)}: {', '.join(missing)}"))
    else:
        info.append(("SOCKS references", True,
                     f"All {len(REQUIRED_REFERENCES)} reference files present"))

    return passed, warnings, info


def check_skill_md():
    """Check SKILL.md exists and is parseable. Returns (passed, warnings, info)."""
    info = []
    warnings = []

    skill_md = os.path.join(SKILL_DIR, "SKILL.md")
    if not os.path.isfile(skill_md):
        info.append(("SKILL.md", False, "Not found"))
        return False, warnings, info

    with open(skill_md, "r") as f:
        content = f.read()

    # Check frontmatter
    if not content.startswith("---"):
        info.append(("SKILL.md frontmatter", False, "Missing YAML frontmatter"))
        return False, warnings, info

    # Check name field
    name_match = re.search(r'^name:\s*(\S+)', content, re.MULTILINE)
    if name_match:
        info.append(("SKILL.md", True, f"name: {name_match.group(1)}"))
    else:
        info.append(("SKILL.md", False, "Missing 'name:' in frontmatter"))
        return False, warnings, info

    return True, warnings, info


def check_project_structure(project_dir):
    """Check project directory structure. Returns (passed, warnings, info)."""
    info = []
    warnings = []
    passed = True

    if not os.path.isdir(project_dir):
        info.append(("Project directory", False, f"Not found: {project_dir}"))
        return False, warnings, info

    info.append(("Project directory", True, project_dir))

    # Required directories
    for d in PROJECT_DIRS_REQUIRED:
        path = os.path.join(project_dir, d)
        if os.path.isdir(path):
            # Count files
            vhd_count = len([f for f in os.listdir(path)
                           if f.endswith((".vhd", ".vhdl"))])
            info.append((f"{d}/", True, f"{vhd_count} VHDL file(s)"))
        else:
            info.append((f"{d}/", False, "MISSING (required)"))
            passed = False

    # Optional directories
    for d in PROJECT_DIRS_OPTIONAL:
        path = os.path.join(project_dir, d)
        if os.path.isdir(path):
            count = len([f for f in os.listdir(path) if not f.startswith(".")])
            info.append((f"{d}/", True, f"{count} file(s)"))
        else:
            info.append((f"{d}/", None, "not present (optional)"))

    # Optional files
    for f in PROJECT_FILES_OPTIONAL:
        path = os.path.join(project_dir, f)
        if os.path.isfile(path):
            info.append((f, True, "present"))
        else:
            info.append((f, None, "not present (optional)"))
            if f == "CLAUDE.md":
                warnings.append("No CLAUDE.md -- run Stage 12 to generate")

    return passed, warnings, info


def check_fingerprint(project_dir):
    """Run fingerprint scan on project tracked files. Returns (passed, warnings, info)."""
    info = []
    warnings = []

    if not os.path.isfile(FINGERPRINT_ENGINE):
        info.append(("Fingerprint engine", None,
                     "not installed (~/.claude/hooks/fingerprint_engine.py)"))
        warnings.append("Fingerprint scanner not available -- skip PII check")
        return True, warnings, info

    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("fingerprint_engine",
                                                       FINGERPRINT_ENGINE)
        engine = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(engine)

        findings = engine.scan_single_repo(project_dir)

        if findings:
            info.append(("Fingerprint scan", False,
                         f"{len(findings)} finding(s) in tracked files"))
            for f in findings[:10]:
                # Strip "BLOCKED: " prefix for cleaner display
                detail = f.replace("BLOCKED: ", "")
                info.append(("", False, f"  {detail}"))
            if len(findings) > 10:
                info.append(("", False,
                             f"  ... and {len(findings) - 10} more"))
            return False, warnings, info
        else:
            info.append(("Fingerprint scan", True, "CLEAN -- no PII or secrets"))
            return True, warnings, info

    except Exception as e:
        info.append(("Fingerprint scan", None, f"error: {e}"))
        warnings.append(f"Fingerprint scan failed: {e}")
        return True, warnings, info


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Stage 0: Environment Preflight",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__)
    parser.add_argument("--settings", type=str, default=None,
                        help="Explicit path to Vivado settings64.sh")
    parser.add_argument("--project-dir", type=str, default=None,
                        help="Project directory to check structure (optional)")
    args = parser.parse_args()

    print_header("SOCKS Stage 0 -- Environment Preflight")

    all_passed = True
    all_warnings = []
    settings_path = None

    # --- Section 1: Vivado ---
    print(f"\n  Vivado:")
    vivado_ok, vivado_warn, vivado_info, settings_path = check_vivado(args.settings)
    for name, ok, detail in vivado_info:
        if name:
            print_result(f"{name:24s} {detail}", ok)
        else:
            print(f"         {detail}")
    if not vivado_ok:
        all_passed = False
    all_warnings.extend(vivado_warn)

    # --- Section 2: Python ---
    print(f"\n  Python:")
    python_ok, python_warn, python_info = check_python()
    for name, ok, detail in python_info:
        print_result(f"{name:24s} {detail}", ok)
    if not python_ok:
        all_passed = False
    all_warnings.extend(python_warn)

    # --- Section 3: SOCKS Skill ---
    print(f"\n  SOCKS Skill:")
    skill_ok, skill_warn, skill_info = check_skill_md()
    for name, ok, detail in skill_info:
        print_result(f"{name:24s} {detail}", ok)
    if not skill_ok:
        all_passed = False
    all_warnings.extend(skill_warn)

    scripts_ok, scripts_warn, scripts_info = check_socks_scripts()
    for name, ok, detail in scripts_info:
        print_result(f"{name:24s} {detail}", ok)
    if not scripts_ok:
        all_passed = False
    all_warnings.extend(scripts_warn)

    refs_ok, refs_warn, refs_info = check_socks_references()
    for name, ok, detail in refs_info:
        print_result(f"{name:24s} {detail}", ok)
    if not refs_ok:
        all_passed = False
    all_warnings.extend(refs_warn)

    # --- Section 4: Project Structure (optional) ---
    has_warnings = False
    if args.project_dir:
        print(f"\n  Project Structure:")
        proj_ok, proj_warn, proj_info = check_project_structure(
            os.path.abspath(args.project_dir))
        for name, ok, detail in proj_info:
            if ok is None:
                print(f"    [{'----':4s}] {name:24s} {detail}")
            else:
                print_result(f"{name:24s} {detail}", ok)
        if not proj_ok:
            all_passed = False
        all_warnings.extend(proj_warn)

        # --- Section 5: Fingerprint Scan (if project-dir given) ---
        print(f"\n  Fingerprint Scan:")
        fp_ok, fp_warn, fp_info = check_fingerprint(
            os.path.abspath(args.project_dir))
        for name, ok, detail in fp_info:
            if ok is None:
                print(f"    [{'----':4s}] {name:24s} {detail}")
            else:
                print_result(f"{name:24s} {detail}", ok)
        if not fp_ok:
            all_passed = False
        all_warnings.extend(fp_warn)

    # --- Warnings ---
    if all_warnings:
        has_warnings = True
        print(f"\n  Warnings:")
        for w in all_warnings:
            print(f"    - {w}")

    # --- Summary ---
    print()
    print_separator()
    if all_passed and not has_warnings:
        print(f"  RESULT: {pass_str()} -- all checks passed")
    elif all_passed and has_warnings:
        print(f"  RESULT: {pass_str()} -- all checks passed (with warnings)")
    else:
        print(f"  RESULT: {fail_str()} -- critical issues found")

    if settings_path:
        print(f"\n  Shell prefix for all Bash commands:")
        print(f"    bash -c 'source {settings_path} && <command>'")

    print_separator()

    if not all_passed:
        return 1
    elif has_warnings:
        return 2
    else:
        return 0


if __name__ == "__main__":
    sys.exit(main())
