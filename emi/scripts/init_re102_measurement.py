#!/usr/bin/env python3
"""Create a starter RE102 measurement directory and measurement.json."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


def screen_room_value(text: str) -> bool | None:
    value = text.strip().lower()
    if value in {"yes", "true", "1", "screened", "screen-room"}:
        return True
    if value in {"no", "false", "0", "ambient", "lab"}:
        return False
    if value in {"unknown", "unspecified", "none"}:
        return None
    raise argparse.ArgumentTypeError("screen room must be yes, no, or unknown")


def safe_id(text: str) -> str:
    if not text or text in {".", ".."} or "/" in text or "\\" in text:
        raise argparse.ArgumentTypeError("test id must be a single path-safe name")
    return text


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Create data/re102/measurements/<test_id> skeleton."
    )
    parser.add_argument("test_id", type=safe_id)
    parser.add_argument("--root", type=Path, default=Path("data/re102/measurements"))
    parser.add_argument("--label", help="human label; defaults to test id")
    parser.add_argument("--uut", help="UUT name; defaults to test id")
    parser.add_argument(
        "--limit",
        default="aircraft_fixed_wing_internal_lt25m",
        help="RE102 limit key",
    )
    parser.add_argument("--antenna-model", default="TBMA1B")
    parser.add_argument("--distance-m", type=float, default=1.0)
    parser.add_argument("--cable-loss-db", type=float, default=0.0)
    parser.add_argument("--screen-room", type=screen_room_value, default=None)
    parser.add_argument("--site-note", default="")
    parser.add_argument("--note", action="append", default=[])
    parser.add_argument("--force", action="store_true", help="overwrite measurement.json")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    measurement_dir = args.root / args.test_id
    measurement_json = measurement_dir / "measurement.json"

    if measurement_json.exists() and not args.force:
        print(f"exists: {measurement_json} (use --force to overwrite)", file=sys.stderr)
        return 2

    for subdir in (
        measurement_dir / "runs",
        measurement_dir / "reports",
    ):
        subdir.mkdir(parents=True, exist_ok=True)

    payload = {
        "schema": "emi.re102.measurement.v1",
        "test_id": args.test_id,
        "measurement_label": args.label or args.test_id,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "standard": "MIL-STD-461F",
        "method": "RE102",
        "limit": args.limit,
        "uut": {
            "name": args.uut or args.test_id,
        },
        "site": {
            "screen_room": args.screen_room,
            "notes": args.site_note,
        },
        "antenna": {
            "model": args.antenna_model,
            "distance_m": args.distance_m,
        },
        "corrections": {
            "cable_loss_db": args.cable_loss_db,
        },
        "runs": [],
        "reports": [],
        "notes": args.note,
    }

    measurement_json.write_text(json.dumps(payload, indent=2) + "\n")
    print(measurement_dir)
    print(measurement_json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
