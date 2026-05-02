#!/usr/bin/env python3
"""Generate the auto-generated portion of a thread status review.

Reads threads/threads.json + per-thread thread.json manifests and emits
the auto-regenerable sections of a `threads/review-<YYYY-MM-DD>.md` file.

The file has an auto / manual split enforced by AUTO-BEGIN / AUTO-END
markers. Sections 1-4 (status counts, by-subsystem, active threads,
triage candidates) live between the markers and are rewritten on each
run. Sections 5+ (strategic tiers, cross-tier concerns, critical-path
notes, recommendations, journal) live below AUTO-END and are preserved
across runs — they're hand-curated.

Usage:
    python3 ~/.claude/skills/threads/scripts/status_review.py <threads-path> --output <review-file>
    python3 ~/.claude/skills/threads/scripts/status_review.py <threads-path> --output <review-file> --stale-active-days 5 --stale-blocked-days 7
    python3 ~/.claude/skills/threads/scripts/status_review.py <threads-path> --output <review-file> --today 2026-04-27   # for testing
"""
from __future__ import annotations

import argparse
import datetime
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

AUTO_BEGIN = "<!-- AUTO-BEGIN -->"
AUTO_END = "<!-- AUTO-END -->"


def parse_date(s: str) -> datetime.date:
    return datetime.date.fromisoformat(s)


def days_since(date_str: str, today: datetime.date) -> int:
    return (today - parse_date(date_str)).days


def load_index(threads_path: Path) -> dict:
    p = threads_path / "threads.json"
    if not p.exists():
        sys.exit(f"ERROR: {p} not found")
    try:
        return json.loads(p.read_text())
    except json.JSONDecodeError as e:
        sys.exit(f"ERROR: {p} parse failure: {e}")


def load_thread(threads_path: Path, thread_id: str) -> dict | None:
    p = threads_path / thread_id / "thread.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except json.JSONDecodeError:
        return {"__corrupt__": True}


def status_counts_table(threads: list[dict]) -> str:
    counts = Counter(t["status"] for t in threads)
    total = sum(counts.values())
    rows = ["| Status | Count | Share |", "|--------|-------|-------|"]
    for s in ("active", "blocked", "superseded", "closed"):
        n = counts.get(s, 0)
        pct = (100.0 * n / total) if total else 0.0
        rows.append(f"| `{s}` | {n} | {pct:.1f}% |")
    rows.append(f"| **Total** | **{total}** | 100.0% |")
    return "\n".join(rows)


def by_subsystem_table(threads: list[dict]) -> str:
    by = defaultdict(lambda: Counter())
    for t in threads:
        subsys = t["id"].split("/", 1)[0]
        by[subsys][t["status"]] += 1
    rows = [
        "| Subsystem | Active | Blocked | Superseded | Closed | Total |",
        "|-----------|-------:|--------:|-----------:|-------:|------:|",
    ]
    for subsys in sorted(by):
        c = by[subsys]
        a, b, s_, cl = c.get("active", 0), c.get("blocked", 0), c.get("superseded", 0), c.get("closed", 0)
        total = a + b + s_ + cl
        rows.append(f"| `{subsys}` | {a} | {b} | {s_} | {cl} | {total} |")
    return "\n".join(rows)


def active_threads_table(threads: list[dict]) -> str:
    actives = [t for t in threads if t["status"] == "active"]
    actives.sort(key=lambda t: t["updated"], reverse=True)
    if not actives:
        return "*(no active threads)*"
    rows = ["| Updated | ID | Current plan | Title |", "|---------|----|--------------|-------|"]
    for t in actives:
        rows.append(f"| {t['updated']} | `{t['id']}` | `{t.get('current_plan', '?')}` | {t['title']} |")
    return "\n".join(rows)


def active_codex_worktrees_table(threads: list[dict], threads_path: Path, today: datetime.date) -> tuple[str, int]:
    """Walk every thread.json and surface codex_worktrees[] entries with status:active.

    Returns (markdown_table, count). An "active" worktree on a closed
    or superseded thread is included in the table (so it's visible
    here) and also surfaces as an `orphaned_codex_worktree` flag in
    the triage table below.
    """
    rows = []
    for t in threads:
        thread_data = load_thread(threads_path, t["id"])
        if thread_data is None or thread_data.get("__corrupt__"):
            continue
        for wt in thread_data.get("codex_worktrees", []) or []:
            if wt.get("status") != "active":
                continue
            started = wt.get("started", "?")
            try:
                age = days_since(started, today) if started != "?" else "?"
            except (ValueError, TypeError):
                age = "?"
            rows.append({
                "thread": t["id"],
                "thread_status": t["status"],
                "branch": wt.get("branch", "?"),
                "base_commit": wt.get("base_commit", "?"),
                "started": started,
                "age": age,
                "path": wt.get("path", "?"),
            })
    if not rows:
        return ("*(no active codex worktrees)*", 0)
    out = [
        "| Thread | Status | Branch | Base | Started | Age (d) | Path |",
        "|--------|--------|--------|------|---------|--------:|------|",
    ]
    for r in rows:
        out.append(
            f"| `{r['thread']}` | `{r['thread_status']}` | `{r['branch']}` | "
            f"`{r['base_commit']}` | {r['started']} | {r['age']} | `{r['path']}` |"
        )
    return ("\n".join(out), len(rows))


def plan_id_for_hop(hop: dict) -> str:
    """Return canonical plan id (plan-NN) from a plan_hops[] entry."""
    filename = str(hop.get("file") or "")
    name = Path(filename).name
    if name.startswith("plan-") and len(name) >= 7 and name[5:7].isdigit():
        return name[:7]
    num = hop.get("num")
    if isinstance(num, int):
        return f"plan-{num:02d}"
    return "plan-??"


def _resolve_worktree_path(raw_path: str, project_root: Path) -> Path:
    p = Path(raw_path).expanduser()
    if p.is_absolute():
        return p
    return (project_root / p).resolve()


def handback_pair_exists(thread_dir: Path, plan_id: str) -> bool:
    return (
        (thread_dir / f"codex-handback-{plan_id}.json").exists()
        and (thread_dir / f"codex-handback-{plan_id}.md").exists()
    )


def handback_locations(threads_path: Path, thread_id: str, thread_data: dict, plan_id: str) -> list[Path]:
    """Return thread dirs where a handback pair is readable."""
    locations: list[Path] = []
    main_thread_dir = threads_path / thread_id
    if handback_pair_exists(main_thread_dir, plan_id):
        locations.append(main_thread_dir)

    project_root = threads_path.parent
    for wt in thread_data.get("codex_worktrees", []) or []:
        raw_path = wt.get("path")
        if not raw_path:
            continue
        wt_thread_dir = _resolve_worktree_path(raw_path, project_root) / ".threads" / thread_id
        if handback_pair_exists(wt_thread_dir, plan_id):
            locations.append(wt_thread_dir)
    return locations


def handback_pair_visible(threads_path: Path, thread_id: str, thread_data: dict, plan_id: str) -> bool:
    """Return true if a handback pair is readable on main or a recorded worktree."""
    return bool(handback_locations(threads_path, thread_id, thread_data, plan_id))


def handback_triage_record_exists(thread_dirs: list[Path], plan_id: str) -> bool:
    return any((d / f"codex-handback-{plan_id}-triage.md").exists() for d in thread_dirs)


def handback_has_actionable_items(handback: dict) -> bool:
    if handback.get("blockers"):
        return True
    if handback.get("follow_ons"):
        return True
    for discovery in handback.get("discoveries", []) or []:
        if discovery.get("follow_up_needed"):
            return True
    for gate in handback.get("gates", []) or []:
        for caveat in gate.get("caveats", []) or []:
            if not caveat.get("resolved_by"):
                return True
    return False


def load_handback_json(thread_dir: Path, plan_id: str) -> dict | None:
    p = thread_dir / f"codex-handback-{plan_id}.json"
    try:
        return json.loads(p.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def hop_expected_codex_handback(hop: dict) -> bool:
    """Heuristic guard to avoid flagging non-Codex historical plan hops."""
    outcome = str(hop.get("outcome") or "").lower()
    return "codex" in outcome or "worktree" in outcome


def flag_triage(
    threads: list[dict],
    threads_path: Path,
    today: datetime.date,
    stale_active_days: int,
    stale_blocked_days: int,
) -> list[dict]:
    """Identify threads needing attention. Returns list of flag records."""
    flags = []
    seen_ids = set()
    valid_ids = {t["id"] for t in threads}

    for t in threads:
        tid = t["id"]
        days = days_since(t["updated"], today)

        # Lifecycle staleness
        if t["status"] == "blocked" and days >= stale_blocked_days:
            flags.append({
                "id": tid,
                "kind": "blocked_stale",
                "days": days,
                "summary": f"Blocked for {days} days. Investigate whether the original blocker still applies; consider unblock/close/supersede.",
            })
            seen_ids.add(tid)
        elif t["status"] == "active" and days >= stale_active_days:
            flags.append({
                "id": tid,
                "kind": "active_stale",
                "days": days,
                "summary": f"Active but no update for {days} days. Verify still in flight; consider close (PASS/FAIL/inconclusive) or supersede.",
            })
            seen_ids.add(tid)

        # Per-thread JSON validity + sub-checks
        thread_data = load_thread(threads_path, tid)
        if thread_data is None:
            flags.append({
                "id": tid,
                "kind": "missing_thread_json",
                "days": days,
                "summary": "Top-level threads.json lists this thread but per-thread thread.json is missing on disk. Manifest corruption — investigate.",
            })
            continue
        if thread_data.get("__corrupt__"):
            flags.append({
                "id": tid,
                "kind": "corrupt_thread_json",
                "days": days,
                "summary": "Per-thread thread.json failed to parse as JSON. Repair before any further operations on this thread.",
            })
            continue

        # superseded_by sanity (top-level field, optional)
        sb = t.get("superseded_by")
        if sb:
            # Strip plan-file suffix if present (e.g., "subsys/slug/plan-NN-foo.md")
            sb_thread = sb.split("/plan-")[0] if "/plan-" in sb else sb.rstrip("/")
            # Trim any trailing parenthetical or path tail beyond the slug
            sb_thread = sb_thread.split(" ")[0]
            if sb_thread and sb_thread not in valid_ids:
                # Not strictly broken — could be a sub-path reference — but worth flagging
                flags.append({
                    "id": tid,
                    "kind": "supersede_ref_unverified",
                    "days": days,
                    "summary": f"superseded_by references `{sb_thread}` which is not a top-level thread id. Verify the target exists; the reference may be a deep-path that doesn't resolve to a thread directory.",
                })

        # Active plan-hop missing outcome on a stale-active thread is a leading
        # indicator of "in-progress work that stalled" — useful triage signal.
        if t["status"] == "active" and days >= stale_active_days:
            for hop in thread_data.get("plan_hops", []):
                if hop.get("status") == "active" and hop.get("outcome") is None and days >= stale_active_days * 2:
                    # Already flagged via active_stale; add detail
                    pass  # primary flag suffices

        # Orphaned codex worktree: status:active worktree on a closed/superseded thread.
        # The thread is supposed to be done but the worktree never landed — cleanup
        # needed (either merge-back was skipped or the worktree should be abandoned).
        if t["status"] in ("closed", "superseded"):
            for wt in thread_data.get("codex_worktrees", []) or []:
                if wt.get("status") == "active":
                    flags.append({
                        "id": tid,
                        "kind": "orphaned_codex_worktree",
                        "days": days,
                        "summary": (
                            f"Thread is `{t['status']}` but codex worktree at "
                            f"`{wt.get('path','?')}` (branch `{wt.get('branch','?')}`) "
                            f"is still status:active. Either run **Codex worktree merge-back** "
                            f"or set the worktree's status to `abandoned` with a notes line."
                        ),
                    })

        # Missing retroactive handback: a closed/superseded codex-backed plan hop
        # references Codex/worktree execution but no structured handback pair is
        # visible on main or in the recorded worktree path.
        if thread_data.get("codex_worktrees"):
            for hop in thread_data.get("plan_hops", []) or []:
                if hop.get("status") not in ("closed", "superseded"):
                    continue
                if not hop_expected_codex_handback(hop):
                    continue
                plan_id = plan_id_for_hop(hop)
                if plan_id == "plan-??":
                    continue
                if not handback_pair_visible(threads_path, tid, thread_data, plan_id):
                    flags.append({
                        "id": tid,
                        "kind": "missing_codex_handback",
                        "days": days,
                        "summary": (
                            f"Plan hop `{plan_id}` is `{hop.get('status')}` and "
                            "appears to have used Codex/worktree execution, but no "
                            f"`codex-handback-{plan_id}.json` / `.md` pair is visible "
                            "on main or the recorded worktree path. Run **Retroactive "
                            "handback** before relying on the closure record."
                        ),
                    })

        # Untriaged handback findings: the handback exists and contains
        # actionable content, but no codex-handback-plan-NN-triage.md record
        # is visible alongside it yet.
        if thread_data.get("codex_worktrees"):
            for hop in thread_data.get("plan_hops", []) or []:
                if hop.get("status") == "active":
                    continue
                if not hop_expected_codex_handback(hop):
                    continue
                plan_id = plan_id_for_hop(hop)
                if plan_id == "plan-??":
                    continue
                locations = handback_locations(threads_path, tid, thread_data, plan_id)
                if not locations or handback_triage_record_exists(locations, plan_id):
                    continue
                handback = load_handback_json(locations[0], plan_id)
                if handback and handback_has_actionable_items(handback):
                    flags.append({
                        "id": tid,
                        "kind": "untriaged_codex_handback",
                        "days": days,
                        "summary": (
                            f"`codex-handback-{plan_id}.json` contains blockers, "
                            "follow-ons, discovery follow-ups, or unresolved gate "
                            "caveats, but no triage record is visible. Run "
                            "`triage_codex_handback.py` and classify each item "
                            "before merge-back or next-hop activation."
                        ),
                    })

    # De-duplicate by (id, kind)
    deduped = []
    seen = set()
    for f in flags:
        key = (f["id"], f["kind"])
        if key not in seen:
            deduped.append(f)
            seen.add(key)
    # Sort: blocked_stale first (highest priority), then orphaned worktrees,
    # then active_stale by descending days, then validity issues.
    kind_order = {
        "blocked_stale": 0,
        "missing_thread_json": 1,
        "corrupt_thread_json": 2,
        "orphaned_codex_worktree": 3,
        "missing_codex_handback": 4,
        "untriaged_codex_handback": 5,
        "active_stale": 6,
        "supersede_ref_unverified": 7,
    }
    deduped.sort(key=lambda f: (kind_order.get(f["kind"], 99), -f.get("days", 0)))
    return deduped


def triage_table(flags: list[dict]) -> str:
    if not flags:
        return "*(no triage candidates flagged — tree is healthy)*"
    rows = [
        "| Thread | Flag | Days since `updated` | Auto-summary | Proposed disposition |",
        "|--------|------|---------------------:|--------------|----------------------|",
    ]
    for f in flags:
        days = f.get("days", "?")
        rows.append(
            f"| `{f['id']}` | `{f['kind']}` | {days} | {f['summary']} | *(fill in: keep_active / close_pass / close_fail / close_inconclusive / supersede / unblock — with one-line rationale)* |"
        )
    return "\n".join(rows)


def render_auto_block(
    threads: list[dict],
    threads_path: Path,
    today: datetime.date,
    stale_active_days: int,
    stale_blocked_days: int,
) -> str:
    flags = flag_triage(threads, threads_path, today, stale_active_days, stale_blocked_days)
    active_count = sum(1 for t in threads if t["status"] == "active")
    worktrees_table, worktrees_count = active_codex_worktrees_table(threads, threads_path, today)
    parts = [
        AUTO_BEGIN,
        "",
        "## 1. Status counts",
        "",
        status_counts_table(threads),
        "",
        "## 2. By subsystem",
        "",
        by_subsystem_table(threads),
        "",
        f"## 3. Active threads ({active_count})",
        "",
        "Sorted by `updated` date, most recent first.",
        "",
        active_threads_table(threads),
        "",
        f"### Active codex worktrees ({worktrees_count})",
        "",
        "Long-lived isolated worktrees on which a Codex agent is doing source-code work for the named thread. A `merged` or `abandoned` worktree is omitted. A `status: active` worktree on a `closed` or `superseded` thread surfaces as an `orphaned_codex_worktree` flag in the triage table below — either run **Codex worktree merge-back** or set the worktree's status to `abandoned`.",
        "",
        worktrees_table,
        "",
        f"## 4. Triage candidates ({len(flags)})",
        "",
        "Threads whose state suggests they need attention. **Auto-flagged; disposition is a Claude+user decision.** For each candidate, propose `keep_active` / `close_pass` / `close_fail` / `close_inconclusive` / `supersede` / `unblock`, then route accepted closures through the **Close-thread** workflow.",
        "",
        triage_table(flags),
        "",
        AUTO_END,
    ]
    return "\n".join(parts)


def find_template(skill_root: Path) -> Path:
    return skill_root / "assets" / "templates" / "review-template.md"


def initial_review_body(template_text: str, date_str: str, prev_review: str | None) -> str:
    body = template_text.replace("{{DATE}}", date_str)
    if prev_review:
        body = body.replace("{{PREV_REVIEW_LINK_OR_NONE}}", f"[`{prev_review}`]({prev_review})")
    else:
        body = body.replace("{{PREV_REVIEW_LINK_OR_NONE}}", "*(none — first review pass)*")
    return body


def regenerate_existing_review(existing_text: str, new_auto_block: str) -> str:
    """Replace the AUTO-BEGIN..AUTO-END block in existing_text with new_auto_block."""
    begin_idx = existing_text.find(AUTO_BEGIN)
    end_idx = existing_text.find(AUTO_END)
    if begin_idx == -1 or end_idx == -1 or end_idx < begin_idx:
        sys.exit(
            "ERROR: existing review file lacks AUTO-BEGIN / AUTO-END markers. "
            "Refusing to overwrite without the auto/manual split."
        )
    end_marker_end = end_idx + len(AUTO_END)
    return existing_text[:begin_idx] + new_auto_block + existing_text[end_marker_end:]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("threads_path", help="path to threads/ dir (containing threads.json)")
    ap.add_argument("--output", help="path to review-<YYYY-MM-DD>.md (default: threads/review-<today>.md)")
    ap.add_argument("--stale-active-days", type=int, default=5)
    ap.add_argument("--stale-blocked-days", type=int, default=7)
    ap.add_argument("--today", default=None, help="override 'today' date (YYYY-MM-DD) for testing")
    ap.add_argument("--prev-review", default=None, help="filename of predecessor review (for the header link)")
    args = ap.parse_args()

    threads_path = Path(args.threads_path).resolve()
    if not threads_path.is_dir():
        sys.exit(f"ERROR: {threads_path} is not a directory")

    today = parse_date(args.today) if args.today else datetime.date.today()
    today_str = today.isoformat()

    output_path = Path(args.output) if args.output else (threads_path / f"review-{today_str}.md")

    index = load_index(threads_path)
    threads = index["threads"]

    auto_block = render_auto_block(
        threads, threads_path, today, args.stale_active_days, args.stale_blocked_days
    )

    skill_root = Path(__file__).resolve().parent.parent

    if output_path.exists():
        existing = output_path.read_text()
        new_text = regenerate_existing_review(existing, auto_block)
        output_path.write_text(new_text)
        print(f"Regenerated AUTO block in {output_path}")
    else:
        template_path = find_template(skill_root)
        if not template_path.exists():
            sys.exit(f"ERROR: template not found at {template_path}")
        body = initial_review_body(template_path.read_text(), today_str, args.prev_review)
        # Replace the placeholder AUTO block with the rendered one
        new_text = regenerate_existing_review(body, auto_block)
        output_path.write_text(new_text)
        print(f"Created {output_path}")

    # Print a concise summary
    counts = Counter(t["status"] for t in threads)
    flags = flag_triage(threads, threads_path, today, args.stale_active_days, args.stale_blocked_days)
    print(f"  Status: {dict(counts)}")
    print(f"  Triage candidates: {len(flags)}")
    for f in flags[:10]:
        print(f"    [{f['kind']}] {f['id']} ({f.get('days','?')}d)")
    if len(flags) > 10:
        print(f"    ... and {len(flags) - 10} more")


if __name__ == "__main__":
    main()
