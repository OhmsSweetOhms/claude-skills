#!/usr/bin/env python3
"""Draft consumer triage for a Codex handback JSON artifact.

The script reads codex-handback-<plan>.json and prints a Markdown
classification table for the main session to review/edit. It is
intentionally conservative: blockers and unresolved gate caveats are
pre-merge blockers; explicit follow-ons are post-merge follow-ups
unless their text says they affect CI, clean checkouts, reproducibility,
or merge readiness.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

CLASS_PRE_MERGE = "pre-merge blocker"
CLASS_POST_MERGE = "post-merge follow-up"
CLASS_ACCEPT_AS_IS = "accepted as-is"

PRE_MERGE_KEYWORDS = (
    "clean checkout",
    "ci",
    "fixture",
    "gitignored",
    "portability",
    "portable",
    "reproducib",
    "before merge",
    "pre-merge",
    "gate",
    "silently skip",
    "untracked",
)


def load_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        sys.exit(f"ERROR: {path} not found")
    except json.JSONDecodeError as e:
        sys.exit(f"ERROR: {path} failed to parse as JSON: {e}")


def compact(text: Any) -> str:
    if text is None:
        return ""
    return " ".join(str(text).split())


def evidence_text(evidence: Any) -> str:
    if isinstance(evidence, dict):
        return compact(evidence.get("primary") or evidence.get("path") or "")
    return compact(evidence)


def text_matches_pre_merge(*parts: Any) -> bool:
    blob = " ".join(compact(p).lower() for p in parts)
    return any(k in blob for k in PRE_MERGE_KEYWORDS)


def classify_discovery(discovery: dict[str, Any]) -> tuple[str, str]:
    if not discovery.get("follow_up_needed"):
        return (CLASS_ACCEPT_AS_IS, "Discovery records context only; no follow-up requested.")
    if text_matches_pre_merge(
        discovery.get("kind"),
        discovery.get("claim"),
        discovery.get("follow_up_summary"),
        evidence_text(discovery.get("evidence")),
    ):
        return (
            CLASS_PRE_MERGE,
            "Follow-up affects gate portability, CI, clean-checkout behavior, or merge readiness.",
        )
    return (CLASS_POST_MERGE, "Follow-up requested, but no pre-merge gate risk keyword was found.")


def classify_follow_on(follow_on: dict[str, Any]) -> tuple[str, str]:
    if text_matches_pre_merge(
        follow_on.get("summary"),
        follow_on.get("rationale"),
        follow_on.get("evidence_path"),
    ):
        return (
            CLASS_PRE_MERGE,
            "Follow-on text affects gate portability, CI, clean-checkout behavior, or merge readiness.",
        )
    return (
        CLASS_POST_MERGE,
        f"Routing is `{follow_on.get('proposed_routing', '?')}`; track after merge or in the next hop.",
    )


def classify_gate_caveat(caveat: dict[str, Any]) -> tuple[str, str]:
    if caveat.get("resolved_by"):
        return (CLASS_ACCEPT_AS_IS, f"Caveat is already resolved by `{caveat['resolved_by']}`.")
    if caveat.get("kind") in {"portability", "reproducibility", "validity", "coverage"}:
        return (CLASS_PRE_MERGE, f"Unresolved `{caveat.get('kind')}` caveat affects gate trust.")
    if text_matches_pre_merge(caveat.get("summary"), caveat.get("impact"), caveat.get("recommended_action")):
        return (
            CLASS_PRE_MERGE,
            "Unresolved caveat text affects gate portability, CI, clean-checkout behavior, or merge readiness.",
        )
    return (CLASS_POST_MERGE, "Unresolved caveat should be tracked, but no pre-merge signal was found.")


def collect_items(handback: dict[str, Any]) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []

    for i, blocker in enumerate(handback.get("blockers", []) or [], start=1):
        items.append({
            "id": f"blocker-{i}",
            "source": "blockers[]",
            "summary": compact(blocker.get("summary")),
            "evidence": compact(blocker.get("evidence_path") or blocker.get("last_command")),
            "classification": CLASS_PRE_MERGE,
            "rationale": "A blocker means Codex could not complete or verify required work.",
        })

    for gate in handback.get("gates", []) or []:
        gate_id = compact(gate.get("gate_id") or gate.get("name") or "gate")
        for i, caveat in enumerate(gate.get("caveats", []) or [], start=1):
            classification, rationale = classify_gate_caveat(caveat)
            items.append({
                "id": f"{gate_id}:caveat-{i}",
                "source": "gates[].caveats[]",
                "summary": compact(caveat.get("summary")),
                "evidence": evidence_text(caveat.get("evidence")),
                "classification": classification,
                "rationale": rationale,
            })

    for discovery in handback.get("discoveries", []) or []:
        classification, rationale = classify_discovery(discovery)
        items.append({
            "id": compact(discovery.get("id") or "discovery"),
            "source": "discoveries[]",
            "summary": compact(discovery.get("claim")),
            "evidence": evidence_text(discovery.get("evidence")),
            "classification": classification,
            "rationale": rationale,
        })

    for i, investigation in enumerate(handback.get("investigations", []) or [], start=1):
        items.append({
            "id": compact(investigation.get("id") or f"investigation-{i}"),
            "source": "investigations[]",
            "summary": compact(investigation.get("question") or investigation.get("answer")),
            "evidence": evidence_text(investigation.get("evidence")),
            "classification": CLASS_ACCEPT_AS_IS,
            "rationale": "Investigation is preserved as evidence; promote manually if it changed scope.",
        })

    for i, follow_on in enumerate(handback.get("follow_ons", []) or [], start=1):
        classification, rationale = classify_follow_on(follow_on)
        items.append({
            "id": f"follow-on-{i}",
            "source": "follow_ons[]",
            "summary": compact(follow_on.get("summary")),
            "evidence": compact(follow_on.get("evidence_path")),
            "classification": classification,
            "rationale": rationale,
        })

    return items


def render_markdown(handback_path: Path, handback: dict[str, Any], items: list[dict[str, str]]) -> str:
    title = f"# Codex handback triage — {handback.get('plan_id', '?')}"
    header = [
        title,
        "",
        f"Handback JSON: `{handback_path}`",
        f"Thread: `{handback.get('thread_id', '?')}`",
        f"Status: `{handback.get('status', '?')}`",
        "",
        "Review these recommendations before merge-back or next-hop activation.",
        "",
    ]
    if not items:
        return "\n".join(header + ["*(no discoveries, investigations, blockers, gate caveats, or follow-ons)*", ""])

    rows = [
        "| ID | Source | Summary | Evidence | Recommended classification | Rationale |",
        "|----|--------|---------|----------|----------------------------|-----------|",
    ]
    for item in items:
        rows.append(
            "| `{id}` | `{source}` | {summary} | `{evidence}` | **{classification}** | {rationale} |".format(
                id=item["id"],
                source=item["source"],
                summary=item["summary"].replace("|", "\\|") or "-",
                evidence=item["evidence"].replace("|", "\\|") or "-",
                classification=item["classification"],
                rationale=item["rationale"].replace("|", "\\|"),
            )
        )
    return "\n".join(header + rows + [""])


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("handback_json", help="path to codex-handback-<plan-id>.json")
    ap.add_argument("--out", help="optional path for the Markdown triage record")
    args = ap.parse_args()

    handback_path = Path(args.handback_json).resolve()
    handback = load_json(handback_path)
    items = collect_items(handback)
    rendered = render_markdown(handback_path, handback, items)

    if args.out:
        out = Path(args.out)
        out.write_text(rendered)
        print(f"Wrote {out}")
    else:
        print(rendered)

    counts = {CLASS_PRE_MERGE: 0, CLASS_POST_MERGE: 0, CLASS_ACCEPT_AS_IS: 0}
    for item in items:
        counts[item["classification"]] += 1
    print(
        "summary: "
        f"{CLASS_PRE_MERGE}={counts[CLASS_PRE_MERGE]}, "
        f"{CLASS_POST_MERGE}={counts[CLASS_POST_MERGE]}, "
        f"{CLASS_ACCEPT_AS_IS}={counts[CLASS_ACCEPT_AS_IS]}"
    )


if __name__ == "__main__":
    main()
