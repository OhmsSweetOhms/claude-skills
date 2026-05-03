#!/usr/bin/env python3
"""Render the codex worktree handoff scaffold for a plan hop.

Reads the threads-skill template at
``~/.claude/skills/threads/assets/templates/codex-handoff-prompt.md``,
substitutes ``{{...}}`` placeholders from thread state, worktree git
state, and the plan file path, and emits a scaffold for the main
agent to hand-curate.

This script deliberately does NOT extract task, deliverables, tests,
or constraints from the plan. A launch-ready codex prompt is a
generative artifact: the main agent must compose the source-work
assignment from the plan, handoff, and current repo state. The
renderer only fills mechanical boilerplate and places explicit
``HAND-CURATE`` markers where judgment is required.

Adjacent-threads briefing is also curated judgment. Pass
``--adjacent-threads <path>`` to inject one, or accept the default
``HAND-CURATE`` marker.

Usage:
    python3 render_codex_handoff.py \\
        --thread-id receiver/20260427-chi-square-raim-design \\
        --plan-id plan-03 \\
        [--main-repo .] \\
        [--worktree-path ../gps_design-chi-square-raim-design] \\
        [--adjacent-threads path/to/briefing.md] \\
        [--out path/to/rendered.md]

If ``--worktree-path`` is omitted, the convention
``<main-repo>-<branch-slug>`` is used (sibling directory of the
main checkout, named after the thread slug).

Anchored substitutions and their sources:

    {{REPO_NAME}}                ← basename of --main-repo
    {{WORKTREE_PATH}}            ← --worktree-path; otherwise discovered
                                   via thread.json codex_worktrees[];
                                   falls back to convention
                                   <main-repo>-<thread-slug>, with the
                                   YYYYMMDD- date prefix stripped as a
                                   secondary fallback (since branches
                                   often drop the date prefix even when
                                   thread slugs include it)
    {{BRANCH}}                   ← `git -C <worktree> branch --show-current`
                                   on the discovered worktree; falls
                                   back to thread.json codex_worktrees[]
                                   .branch, then the slug as last resort
    {{BASE_COMMIT_SHA}}          ← `git -C <worktree> rev-parse HEAD`
                                   (NOT the original branch base — the
                                   worktree's CURRENT head, so each
                                   hop sees the prior hop's terminal
                                   commit)
    {{MAIN_REPO_PATH}}           ← absolute path of --main-repo
    {{HANDOFF_DIR}}              ← <worktree>/codex-handoff/<plan-id>
    {{HANDBACK_JSON_PATH}}       ← {{HANDOFF_DIR}}/handback.json
    {{HANDBACK_MD_PATH}}         ← {{HANDOFF_DIR}}/handback.md
    {{HANDOFF_SCRIPTS_DIR}}      ← {{HANDOFF_DIR}}/scripts
    {{HANDOFF_TEMP_DIR}}         ← {{HANDOFF_DIR}}/temp
    {{HANDOFF_ARTIFACTS_DIR}}    ← {{HANDOFF_DIR}}/artifacts
    {{TASK_SCAFFOLD}}            ← HAND-CURATE marker with source refs
    {{READ_THESE_FIRST_SCAFFOLD}} ← HAND-CURATE marker with source refs
    {{STEP_BY_STEP_SCAFFOLD}}    ← HAND-CURATE marker with source refs
    {{DELIVERABLES_SCAFFOLD}}    ← HAND-CURATE marker with source refs
    {{HARD_CONSTRAINTS_SCAFFOLD}} ← HAND-CURATE marker with source refs
    {{FOCUSED_TESTS_SCAFFOLD}}   ← HAND-CURATE marker with source refs
    {{REGRESSION_BASELINE_SCAFFOLD}} ← HAND-CURATE marker with source refs
    {{RUNTIME_INVARIANT_SCAFFOLD}} ← HAND-CURATE marker with source refs
    {{ADJACENT_THREADS_BRIEFING}} ← --adjacent-threads file or marker
    {{RECORDING_DISCIPLINE_BLOCK}} ← read verbatim from
                                     templates/recording-discipline.md
    {{THREAD_ID}}                ← --thread-id
    {{PLAN_ID}}                  ← --plan-id
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

SKILL_ROOT = Path.home() / ".claude" / "skills" / "threads"
TEMPLATE_PATH = SKILL_ROOT / "assets" / "templates" / "codex-handoff-prompt.md"
RECORDING_DISCIPLINE_PATH = (
    SKILL_ROOT / "assets" / "templates" / "recording-discipline.md"
)

ADJACENT_THREADS_DEFAULT_MARKER = (
    "<!-- HAND-CURATE[adjacent-threads]: Main agent, summarize "
    "adjacent threads only if they matter to this codex run. Write "
    "'No adjacent threads.' if none are relevant. Remove this marker "
    "before handing the prompt to the user. -->"
)


def warn(msg: str) -> None:
    print(f"warning: {msg}", file=sys.stderr)


def die(msg: str, code: int = 1) -> None:
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(code)


def git_rev_parse_short(repo: Path, ref: str = "HEAD") -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "--short", ref],
            capture_output=True,
            text=True,
            check=True,
        )
    except FileNotFoundError:
        die("git not found on PATH")
    except subprocess.CalledProcessError as exc:
        die(
            f"git rev-parse {ref} failed in {repo}: "
            f"{exc.stderr.strip() or exc.stdout.strip()}"
        )
    return result.stdout.strip()


def git_current_branch(repo: Path) -> str | None:
    """Read the current branch name from a worktree/repo via git.

    Returns ``None`` if not on a branch (detached HEAD) or if the
    invocation fails for any reason. Callers should fall back to other
    sources (thread.json, slug) when this returns ``None``.
    """
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), "branch", "--show-current"],
            capture_output=True,
            text=True,
            check=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return None
    branch = result.stdout.strip()
    return branch if branch else None


def discover_worktree_path(main_repo: Path, thread_id: str) -> Path | None:
    """Locate the worktree directory for a thread.

    Resolution order:

    1. Read ``main_repo/.threads/<thread-id>/thread.json`` and walk
       ``codex_worktrees[]`` looking for an entry whose ``path``
       resolves to an existing directory. Non-merged entries are
       preferred over merged ones (the latter still useful for
       retrospective re-renders before worktree cleanup).
    2. Convention: ``<main-repo>-<thread-slug>`` as a sibling dir.
    3. Convention with the ``YYYYMMDD-`` date prefix stripped from the
       slug, since thread branches often drop the date even when the
       slug carries it.

    Returns the first existing directory, or ``None``. Returning
    ``None`` lets the caller fail with a clear error message rather
    than guessing.
    """
    thread_json = main_repo / ".threads" / thread_id / "thread.json"
    if thread_json.exists():
        try:
            data = json.loads(thread_json.read_text())
        except (json.JSONDecodeError, OSError):
            data = None
        if data:
            worktrees = data.get("codex_worktrees", [])
            ranked = sorted(
                worktrees,
                key=lambda w: 0 if w.get("status") not in ("merged",) else 1,
            )
            for entry in ranked:
                raw_path = entry.get("path")
                if not raw_path:
                    continue
                path = Path(raw_path)
                if not path.is_absolute():
                    path = (main_repo / raw_path).resolve()
                if path.exists():
                    return path

    slug = thread_id.split("/")[-1]
    candidates = [main_repo.parent / f"{main_repo.name}-{slug}"]
    match = re.match(r"^\d{8}-(.+)$", slug)
    if match:
        candidates.append(
            main_repo.parent / f"{main_repo.name}-{match.group(1)}"
        )
    for candidate in candidates:
        if candidate.exists():
            return candidate

    return None


def find_plan_file(thread_dir: Path, plan_id: str) -> Path:
    """Find ``plan-NN-*.md`` (or ``plan-NN.md``) in the thread dir."""
    candidates = sorted(thread_dir.glob(f"{plan_id}-*.md"))
    if not candidates:
        candidates = sorted(thread_dir.glob(f"{plan_id}.md"))
    if not candidates:
        existing = sorted(p.name for p in thread_dir.glob("plan-*.md"))
        die(
            f"no plan file matching '{plan_id}-*.md' in {thread_dir}.\n"
            f"  existing plan files: {existing or 'none'}"
        )
    if len(candidates) > 1:
        warn(
            f"multiple plan files matched {plan_id}; using "
            f"{candidates[0].name}"
        )
    return candidates[0]


def count_lines(path: Path) -> int:
    """Count text lines for source-reference markers."""
    try:
        text = path.read_text()
    except OSError as exc:
        die(f"failed to read {path}: {exc}")
    return max(1, len(text.splitlines()))


def repo_relative(path: Path, repo: Path) -> str:
    """Return a stable POSIX-ish source path for prompt markers."""
    try:
        return path.relative_to(repo).as_posix()
    except ValueError:
        return str(path)


def hand_curate_marker(
    *,
    field: str,
    instruction: str,
    plan_ref: str,
    plan_lines: int,
    handoff_ref: str | None,
    handoff_lines: int | None,
) -> str:
    refs = [f"Raw plan material: `{plan_ref}` lines 1-{plan_lines}."]
    if handoff_ref and handoff_lines:
        refs.append(
            f"Current-state handoff material: `{handoff_ref}` lines "
            f"1-{handoff_lines}."
        )
    refs_text = " ".join(refs)
    return (
        f"<!-- HAND-CURATE[{field}]: Main agent, {instruction} "
        f"{refs_text} Remove this marker before handing the prompt "
        f"to the user. -->"
    )


def build_scaffold_substitutions(
    *,
    main_repo: Path,
    plan_file: Path,
    thread_dir: Path,
) -> dict[str, str]:
    plan_ref = repo_relative(plan_file, main_repo)
    plan_lines = count_lines(plan_file)

    handoff_file = thread_dir / "handoff.md"
    handoff_ref: str | None = None
    handoff_lines: int | None = None
    if handoff_file.exists():
        handoff_ref = repo_relative(handoff_file, main_repo)
        handoff_lines = count_lines(handoff_file)

    marker_args = {
        "plan_ref": plan_ref,
        "plan_lines": plan_lines,
        "handoff_ref": handoff_ref,
        "handoff_lines": handoff_lines,
    }
    return {
        "TASK_SCAFFOLD": hand_curate_marker(
            field="task",
            instruction=(
                "compose the concise source-work assignment for this "
                "codex run. Include only the current plan hop's scope."
            ),
            **marker_args,
        ),
        "READ_THESE_FIRST_SCAFFOLD": hand_curate_marker(
            field="read-these-first",
            instruction=(
                "list the files, references, and command outputs the "
                "codex agent must read before editing."
            ),
            **marker_args,
        ),
        "STEP_BY_STEP_SCAFFOLD": hand_curate_marker(
            field="step-by-step",
            instruction=(
                "turn the plan into an execution sequence with clear "
                "stop points and no unrelated exploration."
            ),
            **marker_args,
        ),
        "DELIVERABLES_SCAFFOLD": hand_curate_marker(
            field="deliverables",
            instruction=(
                "write concrete expected file changes and artifacts. "
                "Preserve multi-line bullets when needed."
            ),
            **marker_args,
        ),
        "HARD_CONSTRAINTS_SCAFFOLD": hand_curate_marker(
            field="hard-constraints",
            instruction=(
                "surface non-negotiable constraints, source-only "
                "boundaries, branch/worktree rules, and known traps."
            ),
            **marker_args,
        ),
        "FOCUSED_TESTS_SCAFFOLD": hand_curate_marker(
            field="focused-tests",
            instruction=(
                "write the exact focused verification commands as "
                "fenced bash, using the worktree venv."
            ),
            **marker_args,
        ),
        "REGRESSION_BASELINE_SCAFFOLD": hand_curate_marker(
            field="regression-baseline",
            instruction=(
                "write the exact before-final-commit regression "
                "baseline commands as fenced bash."
            ),
            **marker_args,
        ),
        "RUNTIME_INVARIANT_SCAFFOLD": hand_curate_marker(
            field="runtime-invariant",
            instruction=(
                "state any plan-specific runtime invariant that codex "
                "must preserve, or write that there is no additional "
                "runtime invariant beyond the constraints above."
            ),
            **marker_args,
        ),
    }


def render(
    *,
    main_repo: Path,
    worktree_path: Path,
    thread_id: str,
    plan_id: str,
    adjacent_threads: str | None,
) -> str:
    if not main_repo.exists():
        die(f"main repo {main_repo} does not exist")
    if not worktree_path.exists():
        die(
            f"worktree {worktree_path} does not exist.\n"
            f"  expected at: {worktree_path}\n"
            f"  create it with:\n"
            f"    git -C {main_repo} worktree add "
            f"{worktree_path} <branch>"
        )

    thread_dir = main_repo / ".threads" / thread_id
    if not thread_dir.exists():
        die(f"thread directory not found: {thread_dir}")

    plan_file = find_plan_file(thread_dir, plan_id)
    if not TEMPLATE_PATH.exists():
        die(f"template not found: {TEMPLATE_PATH}")
    template = TEMPLATE_PATH.read_text()

    # Recording discipline is required if the v2 template references it.
    # Read it lazily so v1 templates (without the placeholder) still work.
    recording_block: str | None = None
    if "{{RECORDING_DISCIPLINE_BLOCK}}" in template:
        if not RECORDING_DISCIPLINE_PATH.exists():
            die(
                f"template references {{RECORDING_DISCIPLINE_BLOCK}} but "
                f"the file is missing: {RECORDING_DISCIPLINE_PATH}\n"
                f"  create it from Artifact D of the threads-skill "
                f"design conversation, then re-run."
            )
        recording_block = RECORDING_DISCIPLINE_PATH.read_text().strip()

    # Branch resolution: prefer git's view of the actual worktree,
    # fall back to the thread.json codex_worktrees[] record, last
    # resort the thread-id slug. Real-world branches frequently differ
    # from the thread slug (e.g. branch drops the YYYYMMDD- prefix),
    # so deriving from git is the only reliable answer when the
    # worktree exists.
    branch = git_current_branch(worktree_path)
    if not branch:
        thread_json = main_repo / ".threads" / thread_id / "thread.json"
        if thread_json.exists():
            try:
                data = json.loads(thread_json.read_text())
            except (json.JSONDecodeError, OSError):
                data = None
            if data:
                for entry in data.get("codex_worktrees", []):
                    if entry.get("branch"):
                        branch = entry["branch"]
                        break
    if not branch:
        branch = thread_id.split("/")[-1]
        warn(
            "could not determine branch from worktree git or thread.json; "
            f"falling back to thread-id slug '{branch}'"
        )
    base_sha = git_rev_parse_short(worktree_path)
    handoff_dir = worktree_path / "codex-handoff" / plan_id

    briefing = (
        adjacent_threads
        if adjacent_threads is not None
        else ADJACENT_THREADS_DEFAULT_MARKER
    )
    scaffold_substitutions = build_scaffold_substitutions(
        main_repo=main_repo,
        plan_file=plan_file,
        thread_dir=thread_dir,
    )

    substitutions: dict[str, str] = {
        "REPO_NAME": main_repo.name,
        "WORKTREE_PATH": str(worktree_path),
        "BRANCH": branch,
        "BASE_COMMIT_SHA": base_sha,
        "MAIN_REPO_PATH": str(main_repo),
        "HANDOFF_DIR": str(handoff_dir),
        "HANDBACK_JSON_PATH": str(handoff_dir / "handback.json"),
        "HANDBACK_MD_PATH": str(handoff_dir / "handback.md"),
        "HANDOFF_SCRIPTS_DIR": str(handoff_dir / "scripts"),
        "HANDOFF_TEMP_DIR": str(handoff_dir / "temp"),
        "HANDOFF_ARTIFACTS_DIR": str(handoff_dir / "artifacts"),
        "ADJACENT_THREADS_BRIEFING": briefing,
        "THREAD_ID": thread_id,
        "PLAN_ID": plan_id,
    }
    substitutions.update(scaffold_substitutions)
    if recording_block is not None:
        substitutions["RECORDING_DISCIPLINE_BLOCK"] = recording_block

    rendered = template
    for key, value in substitutions.items():
        rendered = rendered.replace("{{" + key + "}}", value)

    leftover = sorted(set(re.findall(r"\{\{(\w+)\}\}", rendered)))
    if leftover:
        warn(f"unresolved placeholders remain: {leftover}")

    return rendered


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Render a codex handoff scaffold for a plan hop. "
            "Substitutes mechanical thread/git values into the threads-"
            "skill template and leaves HAND-CURATE markers for "
            "substantive prompt sections."
        ),
    )
    parser.add_argument(
        "--thread-id",
        required=True,
        help="Thread ID, e.g. receiver/20260427-chi-square-raim-design",
    )
    parser.add_argument(
        "--plan-id",
        required=True,
        help="Plan hop ID, e.g. plan-03",
    )
    parser.add_argument(
        "--main-repo",
        default=".",
        help="Path to main checkout (where .threads/ lives). Default: cwd",
    )
    parser.add_argument(
        "--worktree-path",
        default=None,
        help=(
            "Path to worktree. Default: <main-repo>-<branch> sibling, "
            "matching the threads-skill convention."
        ),
    )
    parser.add_argument(
        "--adjacent-threads",
        default=None,
        help=(
            "Path to a markdown file with the adjacent-threads briefing. "
            "If omitted, leaves a marker for the main agent to fill."
        ),
    )
    parser.add_argument(
        "--out",
        default=None,
        help="Output path for the rendered prompt. Default: stdout",
    )
    args = parser.parse_args()

    main_repo = Path(args.main_repo).resolve()

    if args.worktree_path:
        worktree = Path(args.worktree_path).resolve()
    else:
        discovered = discover_worktree_path(main_repo, args.thread_id)
        if discovered is not None:
            worktree = discovered
        else:
            # Discovery failed; fall through to a convention-based path
            # so the existence check inside render() emits a clear
            # error message naming what we tried.
            slug = args.thread_id.split("/")[-1]
            worktree = main_repo.parent / f"{main_repo.name}-{slug}"

    adjacent: str | None = None
    if args.adjacent_threads:
        adj_path = Path(args.adjacent_threads).resolve()
        if not adj_path.exists():
            die(f"adjacent-threads file not found: {adj_path}")
        adjacent = adj_path.read_text().strip()

    rendered = render(
        main_repo=main_repo,
        worktree_path=worktree,
        thread_id=args.thread_id,
        plan_id=args.plan_id,
        adjacent_threads=adjacent,
    )

    if args.out:
        out_path = Path(args.out).resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(rendered)
        print(f"rendered to {out_path}", file=sys.stderr)
    else:
        sys.stdout.write(rendered)


if __name__ == "__main__":
    main()
