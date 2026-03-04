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

    # Match `scripts/foo.py` patterns
    refs = re.findall(r'`scripts/([a-zA-Z0-9_]+\.py)`', content)
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


def check_absolute_paths(verbose=False):
    """Check for absolute paths or user-specific paths in skill files."""
    errors = []
    patterns = [
        (r'/home/\w+/', "Absolute home path"),
        (r'/media/\w+/', "Absolute media path"),
        (r'/Users/\w+/', "Absolute macOS user path"),
    ]

    for root, _, files in os.walk(SKILL_DIR):
        if ".git" in root:
            continue
        for fname in files:
            if not (fname.endswith(".md") or fname.endswith(".py") or fname.endswith(".yml")):
                continue
            fpath = os.path.join(root, fname)
            rel = os.path.relpath(fpath, SKILL_DIR)
            with open(fpath, "r") as f:
                for lineno, line in enumerate(f, 1):
                    for pat, desc in patterns:
                        if re.search(pat, line):
                            errors.append((f"{rel}:{lineno}", f"{desc}: {line.strip()[:80]}"))

    if verbose and not errors:
        print("    No absolute/user paths found")

    return errors


def check_orchestrator_consistency(verbose=False):
    """Check that socks.py STAGES dict matches actual script files."""
    socks_py = os.path.join(SKILL_DIR, "scripts", "socks.py")
    if not os.path.isfile(socks_py):
        return [("scripts/socks.py", "File not found")]

    errors = []
    with open(socks_py, "r") as f:
        content = f.read()

    # Extract script filenames from STAGES dict
    refs = re.findall(r'\"([a-zA-Z0-9_]+\.py)\"', content)
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
    args = parser.parse_args()

    print_header("SOCKS Self-Audit")
    print(f"\n  Skill directory: {SKILL_DIR}")

    all_errors = []
    checks = [
        ("SKILL.md script references", check_skill_md_script_refs),
        ("SKILL.md reference file references", check_skill_md_reference_refs),
        ("Reference → script cross-references", check_reference_script_refs),
        ("Stale stage-numbered names", check_stale_stage_numbers),
        ("Absolute / user-specific paths", check_absolute_paths),
        ("Orchestrator dispatch consistency", check_orchestrator_consistency),
    ]

    for name, check_fn in checks:
        print(f"\n  {name}:")
        errors = check_fn(verbose=args.verbose)
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
