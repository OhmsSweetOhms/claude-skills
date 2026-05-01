#!/usr/bin/env python3
"""Render the codex worktree handoff prompt for a plan hop.

Reads the threads-skill template at
``~/.claude/skills/threads/assets/templates/codex-handoff-prompt.md``,
substitutes ``{{...}}`` placeholders from thread state, worktree git
state, and the plan file, and emits a ready-to-paste prompt.

The one substitution this script CANNOT auto-derive is the adjacent-
threads briefing — adjacency is curated judgment, not a derivable
fact. Pass ``--adjacent-threads <path>`` to inject one, or accept
the default marker that flags the gap for the main agent to fill
before handing the prompt to the human.

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
    {{TASK}}                     ← extracted from plan-NN.md "## Task"
    {{DELIVERABLES_BULLETS}}     ← extracted from plan-NN.md
    {{FOCUSED_TEST_MODULES}}     ← extracted from plan-NN.md
    {{REGRESSION_BASELINE_CMD}}  ← extracted from plan-NN.md or default
    {{ADJACENT_THREADS_BRIEFING}} ← --adjacent-threads file or marker
    {{RECORDING_DISCIPLINE_BLOCK}} ← read verbatim from
                                     templates/recording-discipline.md
    {{THREAD_ID}}                ← --thread-id
    {{PLAN_ID}}                  ← --plan-id

Extraction is best-effort. When a section can't be found, a TODO
marker is emitted in its place and a warning is printed to stderr.
The main agent is expected to review the rendered output before
handing it to the human (the `<!-- TODO[render_codex_handoff]: ... -->`
markers are the audit handle).
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

# Marker emitted when a substitution can't be auto-derived. The main
# agent must replace these before handing the prompt to the human.
TODO_MARKER = (
    "<!-- TODO[render_codex_handoff]: {field} not extractable from "
    "plan file. Canonical headings the renderer looks for are "
    "`## Task` / `## Deliverables` / `## Tests` (or `## Verification`). "
    "Populate this section manually OR add the missing canonical "
    "heading to the plan file and re-render. -->"
)

ADJACENT_THREADS_DEFAULT_MARKER = (
    "<!-- MAIN AGENT: populate adjacent-threads briefing here, "
    "or write 'No adjacent threads.' before handing prompt to user -->"
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


def extract_section(
    text: str, heading_pattern: str, *, max_level: int = 3
) -> str | None:
    """Extract a markdown section by heading.

    ``heading_pattern`` is a regex matched case-insensitively against
    the heading text (without the ``##`` prefix). Returns the body up
    to the next heading at the same or higher level, or ``None`` if
    the heading isn't found.
    """
    pattern = re.compile(
        r"^(#{1," + str(max_level) + r"})\s+(.+)$", re.MULTILINE
    )
    headings = list(pattern.finditer(text))
    for i, match in enumerate(headings):
        level = len(match.group(1))
        title = match.group(2).strip()
        if re.search(heading_pattern, title, re.IGNORECASE):
            start = match.end()
            end = len(text)
            for nxt in headings[i + 1:]:
                if len(nxt.group(1)) <= level:
                    end = nxt.start()
                    break
            return text[start:end].strip()
    return None


def extract_task(plan_text: str) -> str | None:
    for pattern in (r"^task$", r"^what to do$", r"^goal$", r"^objective$"):
        section = extract_section(plan_text, pattern)
        if section:
            return section
    return None


def extract_deliverables(plan_text: str) -> str | None:
    for pattern in (
        r"^deliverables$",
        r"^concrete deliverables$",
        r"deliverables",
    ):
        section = extract_section(plan_text, pattern)
        if section:
            bullets = [
                line for line in section.splitlines()
                if line.lstrip().startswith(("- ", "* ", "+ "))
            ]
            return "\n".join(bullets) if bullets else section
    return None


def extract_test_modules(plan_text: str) -> str | None:
    """Pull dotted-path test module names from a tests/verification section."""
    for pattern in (r"^tests$", r"^test ", r"verification"):
        section = extract_section(plan_text, pattern)
        if not section:
            continue
        modules = re.findall(r"[\w_]+(?:\.[\w_]+)*\.tests?\.[\w_]+", section)
        if not modules:
            continue
        seen: set[str] = set()
        unique: list[str] = []
        for module in modules:
            if module not in seen:
                seen.add(module)
                unique.append(module)
        return " ".join(unique)
    return None


def extract_regression_baseline(
    plan_text: str, default: str = "run_tests.py rx"
) -> str:
    section = extract_section(plan_text, r"regression baseline")
    if section:
        fence = re.search(r"```(?:bash|sh)?\s*\n(.+?)\n```", section, re.DOTALL)
        if fence:
            return fence.group(1).strip()
    return default


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
    plan_text = plan_file.read_text()

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

    task = extract_task(plan_text)
    if not task:
        warn(f"could not extract Task section from {plan_file.name}")
        task = TODO_MARKER.format(field="TASK")

    deliverables = extract_deliverables(plan_text)
    if not deliverables:
        warn(f"could not extract Deliverables section from {plan_file.name}")
        deliverables = TODO_MARKER.format(field="DELIVERABLES_BULLETS")

    test_modules = extract_test_modules(plan_text)
    if not test_modules:
        warn(f"could not extract test modules from {plan_file.name}")
        test_modules = TODO_MARKER.format(field="FOCUSED_TEST_MODULES")

    regression_cmd = extract_regression_baseline(plan_text)

    briefing = (
        adjacent_threads
        if adjacent_threads is not None
        else ADJACENT_THREADS_DEFAULT_MARKER
    )

    substitutions: dict[str, str] = {
        "REPO_NAME": main_repo.name,
        "WORKTREE_PATH": str(worktree_path),
        "BRANCH": branch,
        "BASE_COMMIT_SHA": base_sha,
        "MAIN_REPO_PATH": str(main_repo),
        "TASK": task,
        "DELIVERABLES_BULLETS": deliverables,
        "FOCUSED_TEST_MODULES": test_modules,
        "REGRESSION_BASELINE_CMD": regression_cmd,
        "ADJACENT_THREADS_BRIEFING": briefing,
        "THREAD_ID": thread_id,
        "PLAN_ID": plan_id,
    }
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
            "Render codex handoff prompt for a plan hop. "
            "Substitutes thread/git/plan-file values into the threads-"
            "skill template and prints a ready-to-paste prompt."
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
