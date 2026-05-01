#!/usr/bin/env python3
"""Build <project>/.threads/threads.json and <project>/.research/INDEX.json by
walking per-thread thread.json files and per-session session-manifest.json
files.

This script is shared by the threads-skill and research-skill. Both invoke
it after mutating thread.json or session-manifest.json so the registry and
cross-references stay current. Canonical home is
~/.claude/skills/threads/scripts/index_threads_research.py.

Run from the project root (the directory containing .threads/ and .research/):

    python3 ~/.claude/skills/threads/scripts/index_threads_research.py
    python3 ~/.claude/skills/threads/scripts/index_threads_research.py --check
    python3 ~/.claude/skills/threads/scripts/index_threads_research.py --print
    python3 ~/.claude/skills/threads/scripts/index_threads_research.py --summary

Project root is discovered from the current working directory. Pass
`--project-root <path>` to override (e.g. when invoking from a tool that
doesn't cd into the project first).

`--summary` is a read-only mode designed for SessionStart hooks: it
emits a short human-readable status block (latest review, current
metrics, open threads + their current_plan, recently-closed threads,
superseded threads, goal targets) to stdout and writes nothing.
Returns 0 silently if the project has no .threads/ or .research/.

The threads.json (registry) and INDEX.json (research) are committed
artifacts so agents can read them directly without walking the tree.
Per-thread thread.json and per-session session-manifest.json remain
authoritative; the registry/indexes are derived.

Validation findings (each block exits with status 0 if empty, 1 if non-empty
in --check mode):

  threads.broken_linked_research         linked_research entry references a
                                         missing .research/session-XXXX dir
  threads.closed_without_findings        status=closed but findings[] empty
  threads.non_canonical_linked_research  uses 'session' key or project-prefixed
                                         path instead of canonical 'path' +
                                         '.research/session-XXXX'
  research.broken_spawning_thread        spawning_thread references a missing
                                         .threads/<id>/ directory
  research.non_canonical_spawning_thread spawning_thread is prefixed with
                                         '.threads/' instead of bare 'subsys/slug'
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Module-level paths are populated from the project root at main() time so a
# project root override (--project-root) can override the cwd default.
REPO_ROOT: Path = Path.cwd()
THREADS_DIR: Path = REPO_ROOT / ".threads"
RESEARCH_DIR: Path = REPO_ROOT / ".research"
THREADS_INDEX_PATH: Path = THREADS_DIR / "threads.json"
RESEARCH_INDEX_PATH: Path = RESEARCH_DIR / "INDEX.json"


def _bind_paths(project_root: Path) -> None:
    """Update the module-level path globals to point at project_root."""
    global REPO_ROOT, THREADS_DIR, RESEARCH_DIR, THREADS_INDEX_PATH, RESEARCH_INDEX_PATH
    REPO_ROOT = project_root.resolve()
    THREADS_DIR = REPO_ROOT / ".threads"
    RESEARCH_DIR = REPO_ROOT / ".research"
    THREADS_INDEX_PATH = THREADS_DIR / "threads.json"
    RESEARCH_INDEX_PATH = RESEARCH_DIR / "INDEX.json"

SESSION_ID_RE = re.compile(r"session-\d{8}-\d{6}")
THREAD_ID_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*/\d{8}-[a-z0-9-]+$")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def normalize_session_ref(raw: str) -> tuple[str | None, str]:
    """Extract a canonical session-id from a free-form reference.

    Returns (session_id_or_None, kind) where kind is one of:
      'canonical'      - matches '.research/session-XXX' exactly
      'external'       - relative path leaving repo (../..) or absolute path
                         outside repo; cannot validate against local sessions
      'non_canonical'  - has session-id but with extra decoration (project
                         prefix, trailing slash variants, 'session' instead
                         of 'path' key handled at the caller)
      'unparseable'    - no session-id pattern found at all
    """
    if not raw:
        return None, "unparseable"
    s = raw.strip().rstrip("/")
    m = SESSION_ID_RE.search(s)
    if not m:
        return None, "unparseable"
    sid = m.group(0)
    if s.startswith("../") or s.startswith("/"):
        return sid, "external"
    if s == f".research/{sid}":
        return sid, "canonical"
    return sid, "non_canonical"


def normalize_thread_ref(raw: str) -> tuple[str | None, bool]:
    """Extract a canonical thread-id (subsys/slug) from a free-form reference."""
    if not raw:
        return None, True
    s = raw.strip().rstrip("/")
    is_canonical = THREAD_ID_RE.match(s) is not None
    if is_canonical:
        return s, True
    # Strip leading .threads/ or threads/ prefix
    for prefix in (".threads/", "threads/"):
        if s.startswith(prefix):
            stripped = s[len(prefix):]
            if THREAD_ID_RE.match(stripped):
                return stripped, False
    return None, False


def utc_now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Thread extraction
# ---------------------------------------------------------------------------

@dataclass
class ThreadFinding:
    thread_id: str
    kind: str
    detail: str


@dataclass
class ThreadIndex:
    threads: list[dict[str, Any]] = field(default_factory=list)
    findings: list[ThreadFinding] = field(default_factory=list)


def extract_thread(path: Path, existing_session_ids: set[str]) -> tuple[dict[str, Any], list[ThreadFinding]]:
    findings: list[ThreadFinding] = []
    raw = json.loads(path.read_text())
    thread_id = raw.get("id", "")
    subsystem, _, slug = thread_id.partition("/")

    plan_hops_raw = raw.get("plan_hops") or []
    plan_hops = []
    closure_outcome: str | None = None
    for hop in plan_hops_raw:
        outcome = hop.get("outcome") or ""
        if hop.get("status") == "closed" and outcome:
            closure_outcome = outcome
        truncated = outcome
        if len(truncated) > 200:
            truncated = truncated[:197] + "..."
        plan_hops.append({
            "num": hop.get("num"),
            "file": hop.get("file"),
            "status": hop.get("status"),
            "outcome": truncated or None,
        })
    # Derive current_plan: prefer explicit thread.json field, else latest hop file
    current_plan = raw.get("current_plan")
    if not current_plan and plan_hops_raw:
        current_plan = plan_hops_raw[-1].get("file")

    linked_session_ids: list[str] = []
    external_session_refs: list[str] = []
    for entry in (raw.get("linked_research") or []):
        if not isinstance(entry, dict):
            continue
        # Canonical key is 'path'; one outlier thread used 'session' instead
        if "session" in entry and "path" not in entry:
            findings.append(ThreadFinding(
                thread_id, "non_canonical_linked_research",
                f"entry uses 'session' key instead of canonical 'path' key"
            ))
        ref = entry.get("path") or entry.get("session") or ""
        sid, kind = normalize_session_ref(ref)

        if kind == "unparseable":
            findings.append(ThreadFinding(
                thread_id, "broken_linked_research",
                f"entry {ref!r} does not contain a parseable session id"
            ))
            continue
        if kind == "external":
            external_session_refs.append(ref)
            continue  # cannot validate against local .research/
        if kind == "non_canonical":
            findings.append(ThreadFinding(
                thread_id, "non_canonical_linked_research",
                f"reference {ref!r} should be '.research/{sid}'"
            ))
        # canonical or non_canonical resolve to a local session id
        linked_session_ids.append(sid)
        if sid not in existing_session_ids:
            findings.append(ThreadFinding(
                thread_id, "broken_linked_research",
                f"references session {sid!r} but .research/{sid}/ does not exist"
            ))

    status = raw.get("status")
    findings_count = len(raw.get("findings") or [])
    if status == "closed" and findings_count == 0:
        findings.append(ThreadFinding(
            thread_id, "closed_without_findings",
            "thread.status='closed' but findings[] is empty"
        ))

    summary = {
        "id": thread_id,
        "subsystem": subsystem,
        "slug": slug,
        "title": raw.get("title", ""),
        "status": status,
        "started": raw.get("started"),
        "updated": raw.get("updated"),
        "current_plan": current_plan,
        "superseded_by": raw.get("superseded_by"),
        "closed_by_spawn": raw.get("closed_by_spawn") or [],
        "parent_plans": raw.get("parent_plans") or [],
        "plan_hops": plan_hops,
        "findings_count": findings_count,
        "diagnostics_count": len(raw.get("diagnostics") or []),
        "temp_count": len(raw.get("temp") or []),
        "outcome": raw.get("outcome") or closure_outcome,
        "linked_research_ids": linked_session_ids,
        "external_research_refs": external_session_refs,
        "promotions": raw.get("promotions") or [],
        "thread_json_path": str(path.relative_to(REPO_ROOT)),
    }
    return summary, findings


def build_thread_index(existing_session_ids: set[str]) -> ThreadIndex:
    idx = ThreadIndex()
    for path in sorted(THREADS_DIR.glob("*/*/thread.json")):
        try:
            summary, findings = extract_thread(path, existing_session_ids)
        except Exception as exc:
            idx.findings.append(ThreadFinding(
                str(path.relative_to(REPO_ROOT)), "parse_error", str(exc)
            ))
            continue
        idx.threads.append(summary)
        idx.findings.extend(findings)
    return idx


# ---------------------------------------------------------------------------
# Research extraction
# ---------------------------------------------------------------------------

@dataclass
class ResearchFinding:
    session_id: str
    kind: str
    detail: str


@dataclass
class ResearchIndex:
    sessions: list[dict[str, Any]] = field(default_factory=list)
    findings: list[ResearchFinding] = field(default_factory=list)


def extract_session(path: Path, session_dir: Path, existing_thread_ids: set[str]) -> tuple[dict[str, Any], list[ResearchFinding]]:
    findings: list[ResearchFinding] = []
    raw = json.loads(path.read_text())
    sid = raw.get("session_id") or session_dir.name

    spawning_raw = raw.get("spawning_thread") or ""
    spawning_id: str | None = None
    if spawning_raw:
        spawning_id, canonical = normalize_thread_ref(spawning_raw)
        if not canonical:
            findings.append(ResearchFinding(
                sid, "non_canonical_spawning_thread",
                f"spawning_thread is {spawning_raw!r}; canonical form is bare 'subsystem/slug' with no '.threads/' prefix"
            ))
        if spawning_id is None:
            findings.append(ResearchFinding(
                sid, "broken_spawning_thread",
                f"spawning_thread {spawning_raw!r} is not a parseable thread id"
            ))
        elif spawning_id not in existing_thread_ids:
            findings.append(ResearchFinding(
                sid, "broken_spawning_thread",
                f"spawning_thread {spawning_id!r} but .threads/{spawning_id}/ does not exist"
            ))

    pdf_count = sum(1 for _ in (session_dir / "pdfs").glob("*.pdf")) if (session_dir / "pdfs").is_dir() else 0
    repos_cloned = sum(1 for p in (session_dir / "repos").iterdir() if p.is_dir() and not p.name.endswith("-NOT-INCLUDED")) if (session_dir / "repos").is_dir() else 0

    summary = {
        "session_id": sid,
        "date": raw.get("date") or raw.get("started"),
        "title": raw.get("title") or raw.get("topic") or "",
        "status": raw.get("status"),
        "spawning_thread": spawning_id,
        "spawning_thread_relationship": raw.get("spawning_thread_relationship"),
        "pdf_count": pdf_count,
        "repos_cloned": repos_cloned,
        "manifest_path": str(path.relative_to(REPO_ROOT)),
    }
    return summary, findings


def build_research_index(existing_thread_ids: set[str]) -> ResearchIndex:
    idx = ResearchIndex()
    for session_dir in sorted(RESEARCH_DIR.glob("session-*")):
        if not session_dir.is_dir():
            continue
        manifest = session_dir / "session-manifest.json"
        if not manifest.is_file():
            idx.findings.append(ResearchFinding(
                session_dir.name, "missing_manifest",
                f"{manifest.relative_to(REPO_ROOT)} not found"
            ))
            continue
        try:
            summary, findings = extract_session(manifest, session_dir, existing_thread_ids)
        except Exception as exc:
            idx.findings.append(ResearchFinding(
                session_dir.name, "parse_error", str(exc)
            ))
            continue
        idx.sessions.append(summary)
        idx.findings.extend(findings)
    return idx


# ---------------------------------------------------------------------------
# Reverse cross-references
# ---------------------------------------------------------------------------

def attach_reverse_xrefs(thread_idx: ThreadIndex, research_idx: ResearchIndex) -> None:
    """Populate sessions[].linked_by_threads from thread index."""
    by_session: dict[str, list[str]] = {}
    for t in thread_idx.threads:
        for sid in t["linked_research_ids"]:
            by_session.setdefault(sid, []).append(t["id"])
    for s in research_idx.sessions:
        s["linked_by_threads"] = by_session.get(s["session_id"], [])


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def thread_summary_block(threads: list[dict[str, Any]]) -> dict[str, int]:
    out = {"total": len(threads), "active": 0, "closed": 0, "superseded": 0, "other": 0}
    for t in threads:
        s = t["status"]
        if s in out:
            out[s] += 1
        else:
            out["other"] += 1
    return out


def session_summary_block(sessions: list[dict[str, Any]]) -> dict[str, int]:
    out = {"total": len(sessions), "complete": 0, "in_progress": 0, "other": 0}
    for s in sessions:
        st = s["status"]
        if st == "complete":
            out["complete"] += 1
        elif st in ("in_progress", "active"):
            out["in_progress"] += 1
        else:
            out["other"] += 1
    return out


def flatten_promotions(threads: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Flatten per-thread promotions[] into a chronological top-level log."""
    flat: list[dict[str, Any]] = []
    for t in threads:
        for p in t.get("promotions", []):
            flat.append({
                "date": p.get("date"),
                "thread": t["id"],
                "from": p.get("from") or p.get("from_file"),
                "to": p.get("to") or p.get("to_file"),
                "reason": p.get("reason"),
                "plan_hop": p.get("plan_hop"),
            })
    return sorted(flat, key=lambda p: (p.get("date") or "", p.get("to") or ""))


def render_thread_index(idx: ThreadIndex) -> dict[str, Any]:
    by_kind: dict[str, list[dict[str, str]]] = {}
    for f in idx.findings:
        by_kind.setdefault(f.kind, []).append({"thread_id": f.thread_id, "detail": f.detail})
    return {
        "version": 1,  # legacy registry schema version, preserved for skill compat
        "generated_at": utc_now_iso(),
        "schema_version": 1,
        "summary": thread_summary_block(idx.threads),
        "validation": by_kind,
        "promotion_log": flatten_promotions(idx.threads),
        "closure_log": _build_closure_log(idx.threads),
        "threads": idx.threads,
    }


def render_research_index(idx: ResearchIndex) -> dict[str, Any]:
    by_kind: dict[str, list[dict[str, str]]] = {}
    for f in idx.findings:
        by_kind.setdefault(f.kind, []).append({"session_id": f.session_id, "detail": f.detail})
    return {
        "generated_at": utc_now_iso(),
        "schema_version": 1,
        "summary": session_summary_block(idx.sessions),
        "validation": by_kind,
        "sessions": idx.sessions,
    }


def write_index(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------------------
# Closure log (append-only thread retirement record)
# ---------------------------------------------------------------------------
#
# The closure_log block in threads.json is the one piece of registry state
# that is NOT fully derived from per-thread thread.json files. It accumulates
# one entry per thread when that thread leaves the `active` state (closed or
# superseded), and entries persist after the thread directory is deleted.
# This lets the SessionStart `--summary` section "Recently retired" survive
# the workflow where closed/superseded threads are pruned from the working
# tree (per the 2026-05-01 memory-and-docs-cleanup plan, Phase 9 / 9a).
#
# Contract:
#   - Reads existing closure_log from on-disk threads.json (preserves all).
#   - For each non-active thread.json found on disk, ensures one entry exists
#     (keyed by (thread_id, transition_date) for idempotence across reruns).
#   - The indexer must run at least once after a thread closes, BEFORE that
#     thread directory is deleted, or the entry is lost. The threads skill's
#     close-thread step rebuilds the index, so this is automatic for normal
#     workflows.

def _build_closure_log(threads: list[dict[str, Any]]) -> list[dict[str, Any]]:
    existing: list[dict[str, Any]] = []
    if THREADS_INDEX_PATH.is_file():
        try:
            data = json.loads(THREADS_INDEX_PATH.read_text())
            raw = data.get("closure_log")
            if isinstance(raw, list):
                existing = raw
        except Exception:
            existing = []

    # Only `closed` and `superseded` are terminal states. `active`, `blocked`,
    # `paused`, etc. are non-terminal and don't belong in the retirement log.
    # Filter the existing log too so a previously-mis-logged non-terminal
    # entry is dropped on rebuild rather than persisting forever.
    TERMINAL = {"closed", "superseded"}
    existing = [e for e in existing
                if isinstance(e, dict) and e.get("final_status") in TERMINAL]

    seen = {(e.get("thread_id"), e.get("transition_date")) for e in existing}
    log = list(existing)
    for t in threads:
        status = t.get("status")
        if status not in TERMINAL:
            continue
        key = (t["id"], t.get("updated"))
        if key in seen:
            continue
        outcome = (t.get("outcome") or "").split("\n")[0]
        if len(outcome) > 200:
            outcome = outcome[:197] + "..."
        log.append({
            "thread_id": t["id"],
            "subsystem": t.get("subsystem"),
            "final_status": status,
            "transition_date": t.get("updated"),
            "outcome": outcome,
            "superseded_by": t.get("superseded_by"),
        })
        seen.add(key)

    log.sort(key=lambda e: (e.get("transition_date") or ""), reverse=True)
    return log


# ---------------------------------------------------------------------------
# Summary mode (read-only; SessionStart-friendly)
# ---------------------------------------------------------------------------

# Where current_metrics lives. Phase 6 of the gps_design memory-and-docs-cleanup
# thread defines the schema and populates this block; until then, we read what's
# present on disk and emit "(not yet populated)" if absent. Once populated, the
# preservation contract is the threads skill's responsibility (close-thread step
# writes here; rebuilds preserve).
def _read_on_disk_current_metrics() -> dict[str, Any] | None:
    if not THREADS_INDEX_PATH.is_file():
        return None
    try:
        data = json.loads(THREADS_INDEX_PATH.read_text())
    except Exception:
        return None
    cm = data.get("current_metrics")
    return cm if isinstance(cm, dict) else None


def _collect_goal_targets() -> list[str]:
    """Walk known project-specific JSON sources for `targets` blocks.

    Each project that wants goal targets in the SessionStart summary lays them
    down where this helper looks. Adding a project means adding a path here;
    the helper is conservative — unknown projects produce no output.

    Currently checks scenario_engine/scenarios/*.v2.json (gps_design).
    """
    out: list[str] = []
    scenarios_dir = REPO_ROOT / "scenario_engine" / "scenarios"
    if scenarios_dir.is_dir():
        for path in sorted(scenarios_dir.glob("*.v2.json")):
            try:
                data = json.loads(path.read_text())
            except Exception:
                continue
            targets = data.get("targets")
            if not isinstance(targets, dict) or not targets:
                continue
            out.append(f"  {path.name}:")
            for k, v in targets.items():
                if isinstance(v, dict):
                    val = v.get("value", v)
                    out.append(f"    {k}: {val}")
                else:
                    out.append(f"    {k}: {v}")
    return out


def _latest_review_path() -> Path | None:
    """Return the most-recent .threads/review-YYYY-MM-DD.md file, or None."""
    if not THREADS_DIR.is_dir():
        return None
    candidates = sorted(THREADS_DIR.glob("review-*.md"), reverse=True)
    return candidates[0] if candidates else None


def render_summary(thread_payload: dict[str, Any]) -> str:
    """Emit a short human-readable status block for SessionStart hooks.

    The output is intentionally compact (~25-40 lines) since SessionStart hook
    output is injected into the agent's context and consumes tokens.
    """
    threads = thread_payload.get("threads", [])
    lines: list[str] = []
    lines.append("=" * 60)
    lines.append("Project status (auto-injected at session start)")
    lines.append("=" * 60)

    # Latest review
    review = _latest_review_path()
    if review is not None:
        lines.append(f"Latest review: {review.relative_to(REPO_ROOT)}")
    else:
        lines.append("Latest review: (none found)")

    # Current metrics (Phase 6 will populate; gracefully absent until then)
    cm = _read_on_disk_current_metrics()
    lines.append("")
    if cm:
        lines.append("Current metrics:")
        for key, val in cm.items():
            if isinstance(val, dict):
                v = val.get("value")
                scenario = val.get("scenario", "")
                asof = val.get("asof_date") or val.get("asof", "")
                tail = []
                if scenario:
                    tail.append(scenario)
                if asof:
                    tail.append(f"asof {asof}")
                tail_str = f"  ({', '.join(tail)})" if tail else ""
                lines.append(f"  {key}: {v}{tail_str}")
            else:
                lines.append(f"  {key}: {val}")
    else:
        lines.append("Current metrics: (not yet populated)")

    # Open threads with current_plan (one-line-per-thread format keeps
    # the section compact even for projects with many concurrent threads)
    open_threads = [t for t in threads if t.get("status") == "active"]
    open_threads.sort(key=lambda t: t.get("updated") or "", reverse=True)
    lines.append("")
    lines.append(f"Open threads ({len(open_threads)}):")
    for t in open_threads:
        plan = t.get("current_plan") or "(no plan)"
        lines.append(f"  {t['id']} → {plan}")
    if not open_threads:
        lines.append("  (none)")

    # Recently retired (last 7 days) — read from closure_log so the entries
    # survive thread directory deletion. Closure log captures both `closed`
    # and `superseded` transitions; superseded entries include the successor.
    today = dt.date.today()
    cutoff = (today - dt.timedelta(days=7)).isoformat()
    log = thread_payload.get("closure_log") or []
    retired_recent = [
        e for e in log
        if (e.get("transition_date") or "") >= cutoff
    ]
    lines.append("")
    lines.append(f"Recently retired (last 7 days, {len(retired_recent)}):")
    show_n = 7
    for e in retired_recent[:show_n]:
        status = e.get("final_status") or "?"
        date = e.get("transition_date") or "?"
        outcome = (e.get("outcome") or "").split("\n")[0]
        if len(outcome) > 80:
            outcome = outcome[:77] + "..."
        if status == "superseded":
            sb = e.get("superseded_by") or "(no successor)"
            # Strip plan-file suffixes from successor for compact display
            sb_short = sb.split("/plan-")[0] if "/plan-" in sb else sb
            head = f"  {e['thread_id']} ({status} {date} → {sb_short})"
        else:
            head = f"  {e['thread_id']} ({status} {date})"
        if outcome:
            lines.append(f"{head} — {outcome}")
        else:
            lines.append(head)
    if len(retired_recent) > show_n:
        lines.append(f"  ... and {len(retired_recent) - show_n} more")
    if not retired_recent:
        lines.append("  (none)")

    # Goal targets (project-specific; empty = silent)
    target_lines = _collect_goal_targets()
    lines.append("")
    if target_lines:
        lines.append("Goal targets:")
        lines.extend(target_lines)
    else:
        lines.append("Goal targets: (not yet populated)")

    lines.append("=" * 60)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--check", action="store_true",
                        help="validate only, do not write INDEX.json files; exit 1 if any findings")
    parser.add_argument("--print", action="store_true", dest="do_print",
                        help="print one-line summary + finding counts to stdout")
    parser.add_argument("--summary", action="store_true",
                        help="emit a SessionStart-friendly status block to stdout; "
                             "read-only, no INDEX writes, silent for non-thread projects")
    parser.add_argument("--project-root", type=Path, default=None,
                        help="project root containing .threads/ and .research/ "
                             "(default: current working directory)")
    args = parser.parse_args()

    project_root = args.project_root or Path.cwd()
    if not (project_root / ".threads").is_dir() and not (project_root / ".research").is_dir():
        if args.summary:
            return 0  # silent for SessionStart in non-thread projects
        print(f"error: neither .threads/ nor .research/ found under {project_root}",
              file=sys.stderr)
        return 2
    _bind_paths(project_root)

    existing_session_ids = {p.name for p in RESEARCH_DIR.glob("session-*") if p.is_dir()}
    existing_thread_ids = {f"{p.parent.name}/{p.name}"
                           for p in THREADS_DIR.glob("*/*")
                           if p.is_dir() and (p / "thread.json").is_file()}

    thread_idx = build_thread_index(existing_session_ids)
    research_idx = build_research_index(existing_thread_ids)
    attach_reverse_xrefs(thread_idx, research_idx)

    thread_payload = render_thread_index(thread_idx)
    research_payload = render_research_index(research_idx)

    if args.summary:
        # Read-only mode for SessionStart hooks: emit the status block and
        # return without touching the on-disk INDEX files.
        print(render_summary(thread_payload))
        return 0

    if not args.check:
        write_index(THREADS_INDEX_PATH, thread_payload)
        write_index(RESEARCH_INDEX_PATH, research_payload)

    finding_count = len(thread_idx.findings) + len(research_idx.findings)
    if args.do_print or args.check:
        ts = thread_payload["summary"]
        rs = research_payload["summary"]
        print(f"threads:  {ts['total']} total ({ts['active']} active, {ts['closed']} closed, {ts['superseded']} superseded)")
        print(f"research: {rs['total']} total ({rs['complete']} complete, {rs['in_progress']} in_progress)")
        print(f"findings: {finding_count}")
        for kind, items in thread_payload["validation"].items():
            print(f"  threads.{kind}: {len(items)}")
            for it in items[:3]:
                print(f"    - {it['thread_id']}: {it['detail']}")
            if len(items) > 3:
                print(f"    ... and {len(items)-3} more")
        for kind, items in research_payload["validation"].items():
            print(f"  research.{kind}: {len(items)}")
            for it in items[:3]:
                print(f"    - {it['session_id']}: {it['detail']}")
            if len(items) > 3:
                print(f"    ... and {len(items)-3} more")

    if args.check and finding_count > 0:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
