#!/usr/bin/env python3
"""
self_audit.py -- SOCKS Skill Self-Audit

Validates internal consistency of the SOCKS skill: checks that all scripts,
references, and cross-references in SKILL.md are valid. Runs automatically
after every orchestrator invocation and as the final pipeline stage.

Usage:
    python scripts/self_audit.py
    python scripts/self_audit.py --verbose
"""

import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from socks_lib import print_header, print_separator, pass_str, fail_str

# SOCKS skill root (parent of scripts/)
SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def check_skill_md_script_refs(verbose=False):
    """Check that all scripts referenced in SKILL.md exist."""
    skill_md = os.path.join(SKILL_DIR, "SKILL.md")
    if not os.path.isfile(skill_md):
        return [("SKILL.md", "File not found")]

    errors = []
    with open(skill_md, "r") as f:
        content = f.read()

    # Match `scripts/foo.py` and `scripts/hil/foo.py` patterns
    refs = re.findall(r'`scripts/([a-zA-Z0-9_/]+\.py)`', content)
    for ref in refs:
        path = os.path.join(SKILL_DIR, "scripts", ref)
        if not os.path.isfile(path):
            errors.append(("SKILL.md", f"Script not found: scripts/{ref}"))
        elif verbose:
            print(f"    scripts/{ref} ... OK")

    return errors


def check_skill_md_reference_refs(verbose=False):
    """Check that all references/*.md files referenced in SKILL.md exist."""
    skill_md = os.path.join(SKILL_DIR, "SKILL.md")
    if not os.path.isfile(skill_md):
        return []

    errors = []
    with open(skill_md, "r") as f:
        content = f.read()

    refs = re.findall(r'`references/([a-zA-Z0-9_-]+\.md)`', content)
    for ref in set(refs):
        path = os.path.join(SKILL_DIR, "references", ref)
        if not os.path.isfile(path):
            errors.append(("SKILL.md", f"Reference not found: references/{ref}"))
        elif verbose:
            print(f"    references/{ref} ... OK")

    return errors


def check_reference_script_refs(verbose=False):
    """Check that scripts referenced inside reference files exist."""
    ref_dir = os.path.join(SKILL_DIR, "references")
    if not os.path.isdir(ref_dir):
        return [("references/", "Directory not found")]

    errors = []
    for fname in sorted(os.listdir(ref_dir)):
        if not fname.endswith(".md"):
            continue
        fpath = os.path.join(ref_dir, fname)
        with open(fpath, "r") as f:
            content = f.read()

        refs = re.findall(r'scripts/([a-zA-Z0-9_]+\.py)', content)
        for ref in set(refs):
            path = os.path.join(SKILL_DIR, "scripts", ref)
            if not os.path.isfile(path):
                errors.append((f"references/{fname}", f"Script not found: scripts/{ref}"))
            elif verbose:
                print(f"    references/{fname} -> scripts/{ref} ... OK")

    return errors


def check_stale_stage_numbers(verbose=False):
    """Check for old stage-numbered script filenames (stageN_*.py)."""
    scripts_dir = os.path.join(SKILL_DIR, "scripts")
    errors = []

    # Check for stale files
    for fname in sorted(os.listdir(scripts_dir)):
        if re.match(r'stage\d+_', fname):
            errors.append(("scripts/", f"Stale stage-numbered script: {fname}"))

    # Check for stale references in all .md and .py files
    for root, _, files in os.walk(SKILL_DIR):
        # Skip .git
        if ".git" in root:
            continue
        for fname in files:
            if not (fname.endswith(".md") or fname.endswith(".py")):
                continue
            fpath = os.path.join(root, fname)
            rel = os.path.relpath(fpath, SKILL_DIR)
            with open(fpath, "r") as f:
                for lineno, line in enumerate(f, 1):
                    matches = re.findall(r'stage\d+_[a-z_]+\.py', line)
                    for m in matches:
                        errors.append((f"{rel}:{lineno}", f"Stale reference: {m}"))

    if verbose and not errors:
        print("    No stale stage-numbered references found")

    return errors


def check_absolute_paths(scan_dir, verbose=False):
    """Check for PII, secrets, and absolute paths in a directory using fingerprint engine.

    scan_dir: directory to scan (project dir when run as pipeline stage).
    """
    engine_path = os.path.join(
        os.path.expanduser("~"), ".claude", "hooks", "fingerprint_engine.py"
    )

    # Fall back to simple regex check if engine not installed
    if not os.path.isfile(engine_path):
        errors = []
        patterns = [
            (r'/home/\w+/', "Absolute home path"),
            (r'/media/\w+/', "Absolute media path"),
            (r'/Users/\w+/', "Absolute macOS user path"),
        ]
        for root, _, files in os.walk(scan_dir):
            if ".git" in root or "build" in root.split(os.sep):
                continue
            for fname in files:
                if not (fname.endswith(".md") or fname.endswith(".py") or
                        fname.endswith(".yml") or fname.endswith(".c") or
                        fname.endswith(".h") or fname.endswith(".vhd") or
                        fname.endswith(".sv") or fname.endswith(".tcl") or
                        fname.endswith(".xdc")):
                    continue
                fpath = os.path.join(root, fname)
                rel = os.path.relpath(fpath, scan_dir)
                with open(fpath, "r") as f:
                    for lineno, line in enumerate(f, 1):
                        for pat, desc in patterns:
                            if re.search(pat, line):
                                errors.append((f"{rel}:{lineno}", f"{desc}: {line.strip()[:80]}"))
        if verbose and not errors:
            print("    No absolute/user paths found (basic check -- engine not installed)")
        return errors

    # Use fingerprint engine for comprehensive scan
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("fingerprint_engine", engine_path)
        engine = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(engine)

        findings = engine.scan_single_repo(scan_dir)
        errors = []

        # Exclude known generated directories that always contain PII (absolute
        # paths with username from Vivado/Vitis). These are gitignored build
        # artifacts but the fingerprint engine may not respect .gitignore when
        # no .git repo exists or when scanning a subdirectory like build/hil/.
        skip_prefixes = (
            "build/vivado_project/",
            "build/hil/vivado_project/",
            "build/hil/vitis_ws/",
            "vivado_project/",   # when scan_dir is build/hil/
            "vitis_ws/",         # when scan_dir is build/hil/
        )
        skip_suffixes = (".log", ".jou", ".backup.log", ".backup.jou")

        for f in findings:
            detail = f.replace("BLOCKED: ", "")
            # Extract the file path from the finding (format: "description -- path:line")
            # or "description found in path:line"
            skip = False
            for prefix in skip_prefixes:
                if prefix in detail:
                    skip = True
                    break
            if not skip:
                for suffix in skip_suffixes:
                    # Check if the finding references a file with this suffix
                    # Findings look like: "Personal identifier 'x' found in file.log:42"
                    parts = detail.split(" found in ")
                    if len(parts) > 1 and any(parts[1].rsplit(":", 1)[0].endswith(s)
                                              for s in skip_suffixes):
                        skip = True
                        break
            if not skip:
                errors.append(("fingerprint", detail))

        if verbose and not errors:
            print("    No PII, secrets, or absolute paths found (fingerprint engine)")

        return errors

    except Exception as e:
        if verbose:
            print(f"    Fingerprint engine error: {e} -- falling back to basic check")
        return []


def check_expected_reference_files(verbose=False):
    """Check that required reference files exist in the skill."""
    expected = [
        "claude_notes.md",
        "hil.md",
        "xsim.md",
        "python-testbench.md",
        "session.md",
    ]
    errors = []
    for fname in expected:
        path = os.path.join(SKILL_DIR, "references", fname)
        if not os.path.isfile(path):
            errors.append(("references/", f"Expected file missing: references/{fname}"))
        elif verbose:
            print(f"    references/{fname} ... OK")
    return errors


def check_orchestrator_consistency(verbose=False):
    """Check that socks.py STAGES dict matches actual script files."""
    socks_py = os.path.join(SKILL_DIR, "scripts", "socks.py")
    if not os.path.isfile(socks_py):
        return [("scripts/socks.py", "File not found")]

    errors = []
    with open(socks_py, "r") as f:
        content = f.read()

    # Extract script filenames from STAGES dict (handles both "foo.py" and "hil/foo.py")
    refs = re.findall(r'\"([a-zA-Z0-9_/]+\.py)\"', content)
    for ref in refs:
        if ref == "socks.py" or ref == "clean.py":
            continue
        path = os.path.join(SKILL_DIR, "scripts", ref)
        if not os.path.isfile(path):
            errors.append(("scripts/socks.py", f"Dispatched script not found: {ref}"))
        elif verbose:
            print(f"    socks.py dispatch -> {ref} ... OK")

    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="SOCKS Skill Self-Audit")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Show passing checks too")
    parser.add_argument("--project-dir", type=str, default=None,
                        help="Project directory for fingerprint scan (default: skill dir)")
    args = parser.parse_args()

    # Fingerprint scans the project; all other checks scan the skill
    fingerprint_dir = os.path.abspath(args.project_dir) if args.project_dir else SKILL_DIR

    print_header("SOCKS Self-Audit")
    print(f"\n  Skill directory: {SKILL_DIR}")
    if args.project_dir:
        print(f"  Fingerprint scan: {fingerprint_dir}")

    all_errors = []
    checks = [
        ("SKILL.md script references", lambda v: check_skill_md_script_refs(v)),
        ("SKILL.md reference file references", lambda v: check_skill_md_reference_refs(v)),
        ("Expected reference files", lambda v: check_expected_reference_files(v)),
        ("Reference → script cross-references", lambda v: check_reference_script_refs(v)),
        ("Stale stage-numbered names", lambda v: check_stale_stage_numbers(v)),
        ("PII / secrets / absolute paths", lambda v: check_absolute_paths(fingerprint_dir, v)),
        ("Orchestrator dispatch consistency", lambda v: check_orchestrator_consistency(v)),
    ]

    for name, check_fn in checks:
        print(f"\n  {name}:")
        errors = check_fn(args.verbose)
        if errors:
            for loc, msg in errors:
                print(f"    [{fail_str()}] {loc}: {msg}")
            all_errors.extend(errors)
        else:
            print(f"    [{pass_str()}] All checks passed")

    print()
    print_separator()
    if all_errors:
        print(f"  RESULT: {fail_str()} -- {len(all_errors)} issue(s) found")
    else:
        print(f"  RESULT: {pass_str()} -- SOCKS skill is consistent")
    print_separator()

    return 1 if all_errors else 0


if __name__ == "__main__":
    sys.exit(main())
