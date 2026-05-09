#!/usr/bin/env python3
"""Initialize an EMI test group.

This helper creates only directories and JSON manifests. It does not touch live
hardware and does not create measurement data.
"""

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
from typing import Any


DEFAULT_ROOT = Path(os.environ.get("EMI_DATA_ROOT", "data"))
METHODS = ("ce102", "re102", "gnss", "rsa")
KINDS = ("uut", "characterization")
UUT_ID_PATTERN = re.compile(r"^uut_(\d{3})$")


def write_json(path: Path, payload: dict[str, Any], *, exist_ok: bool) -> None:
    if path.exists() and not exist_ok:
        raise FileExistsError(f"{path} already exists; use --exist-ok to keep it")
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and exist_ok:
        return
    path.write_text(json.dumps(payload, indent=2) + "\n")


def subject_payload(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "id": args.subject_id,
        "name": args.subject_name or default_subject_name(args),
        "kind": args.kind,
        "is_uut": args.kind == "uut",
    }


def default_subject_name(args: argparse.Namespace) -> str:
    match = UUT_ID_PATTERN.match(args.subject_id)
    if args.kind == "uut" and match:
        return f"UUT {match.group(1)}"
    return args.subject_id


def subject_key(args: argparse.Namespace) -> str:
    return "uut" if args.kind == "uut" else "subject"


def method_measurement(method: str, campaign_id: str, args: argparse.Namespace) -> dict[str, Any]:
    common = {
        "test_id": f"{campaign_id}_{method}",
        "measurement_label": args.label or f"{args.subject_id} {campaign_id} {method.upper()}",
        "method": method.upper(),
        subject_key(args): subject_payload(args),
        "site_conditions": {
            name: {"screen_room": args.screen_room, "notes": ""}
            for name in args.site_condition
        },
        "notes": args.note or [],
    }
    if method == "ce102":
        return {
            "schema": (
                "emi.ce102.measurement.v1"
                if args.kind == "uut"
                else "emi.ce102.characterization_measurement.v1"
            ),
            **common,
            "standard": args.standard,
            "limit": args.ce102_limit,
            "lisn": {
                "manufacturer": "Tekbox",
                "model": "TBL5016-1",
                "configuration": "50uh",
            },
            "corrections": {
                "attenuator_loss_db": args.attenuator_loss_db,
                "cable_loss_db": args.cable_loss_db,
                "external_gain_db": args.external_gain_db,
            },
        }
    if method == "re102":
        return {
            "schema": (
                "emi.re102.measurement.v1"
                if args.kind == "uut"
                else "emi.re102.characterization_measurement.v1"
            ),
            **common,
            "standard": args.standard,
            "limit": args.re102_limit,
            "antenna": {
                "model": args.antenna_model,
                "distance_m": args.distance_m,
            },
            "corrections": {
                "cable_loss_db": args.cable_loss_db,
                "external_gain_db": args.external_gain_db,
            },
        }
    if method == "gnss":
        return {
            "schema": (
                "emi.gnss.measurement.v1"
                if args.kind == "uut"
                else "emi.gnss.characterization_measurement.v1"
            ),
            **common,
            "standard": "none",
            "purpose": "rf_environment_noise_survey",
            "antenna": {"model": args.gnss_antenna_model},
            "reporting": {
                "apply_mil_limits": False,
                "use_dbuv_per_m": False,
            },
        }
    return {
        "schema": (
            "emi.rsa.measurement.v1"
            if args.kind == "uut"
            else "emi.rsa.characterization_measurement.v1"
        ),
        **common,
        "standard": "none",
        "purpose": "analyzer_smoke_or_raw_trace_capture",
    }


def initialize_method(base: Path, method: str, campaign_id: str, args: argparse.Namespace) -> None:
    method_dir = base / method
    write_json(
        method_dir / "measurement.json",
        method_measurement(method, campaign_id, args),
        exist_ok=args.exist_ok,
    )
    for child in ("calibration", "runs", "plots"):
        (method_dir / child).mkdir(parents=True, exist_ok=True)


def base_root(args: argparse.Namespace) -> Path:
    if args.root.name in ("uuts", "characterization"):
        return args.root
    if args.kind == "uut":
        return args.root / "uuts"
    return args.root / "characterization"


def next_uut_id(root: Path) -> str:
    numbers = []
    if root.exists():
        for child in root.iterdir():
            if not child.is_dir():
                continue
            match = UUT_ID_PATTERN.match(child.name)
            if match:
                numbers.append(int(match.group(1)))
    next_number = max(numbers, default=0) + 1
    if next_number > 999:
        raise ValueError(f"no uut_NNN IDs remain under {root}")
    return f"uut_{next_number:03d}"


def resolve_auto_subject_id(args: argparse.Namespace) -> None:
    if args.kind == "uut" and args.subject_id in {"next", "auto"}:
        args.subject_id = next_uut_id(base_root(args))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "subject_id",
        help=(
            "Known UUT/dataset ID. For a provisional UUT, use 'next' or "
            "'auto' to allocate the next uut_NNN directory."
        ),
    )
    parser.add_argument("campaign_id")
    parser.add_argument(
        "--kind",
        choices=KINDS,
        default="uut",
        help="Use uut for actual EUT/UUT campaigns; use characterization for bench validation, smoke checks, and reference scans.",
    )
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--subject-name")
    parser.add_argument("--uut-name", dest="subject_name")
    parser.add_argument("--label")
    parser.add_argument("--method", action="append", choices=METHODS, required=True)
    parser.add_argument(
        "--site-condition",
        action="append",
        default=[],
        help="Site/setup label stored in JSON, for example indoor, outdoor, screen_room.",
    )
    parser.add_argument("--screen-room", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--standard", default="MIL-STD-461F")
    parser.add_argument("--ce102-limit", default="basic_28v")
    parser.add_argument("--re102-limit", default="aircraft_fixed_wing_internal_lt25m")
    parser.add_argument("--antenna-model", default="TBMA1B")
    parser.add_argument("--gnss-antenna-model", default="TW3972XF")
    parser.add_argument("--distance-m", type=float, default=1.0)
    parser.add_argument("--attenuator-loss-db", type=float, default=20.0)
    parser.add_argument("--cable-loss-db", type=float, default=0.0)
    parser.add_argument("--external-gain-db", type=float, default=0.0)
    parser.add_argument("--note", action="append")
    parser.add_argument("--exist-ok", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    resolve_auto_subject_id(args)
    if not args.site_condition:
        args.site_condition = ["unspecified"]

    base = base_root(args) / args.subject_id / args.campaign_id
    base.mkdir(parents=True, exist_ok=True)
    entity = subject_payload(args)
    write_json(
        base / "campaign.json",
        {
            "schema": (
                "emi.campaign.v1"
                if args.kind == "uut"
                else "emi.characterization.campaign.v1"
            ),
            "campaign_id": args.campaign_id,
            "campaign_label": args.label or f"{args.subject_id} {args.campaign_id}",
            ("uut" if args.kind == "uut" else "subject"): entity,
            "methods": args.method,
            "site_conditions": {
                name: {"screen_room": args.screen_room, "notes": ""}
                for name in args.site_condition
            },
            "notes": args.note or [],
        },
        exist_ok=args.exist_ok,
    )

    for method in args.method:
        initialize_method(base, method, args.campaign_id, args)

    print(base)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
