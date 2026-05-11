#!/usr/bin/env python3
"""Check ADI util_adxcvr GT parameters against cached GT Wizard references.

This helper is intentionally lightweight: normal runs do not invoke Vivado.
It reads the active generated Vivado BD when available, compares the selected
RX/TX lane rates against cached GTH4/CPLL reference parameters, and reports
whether the HDL can use one global util_adxcvr parameter family or needs a
per-channel/split-util treatment.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys
from dataclasses import dataclass
from typing import Any


REFERENCE_PARAMS = {
    # Generated with Vivado 2023.2 GT Wizard, xczu9eg-ffvb1156-2-e,
    # GTH4 CPLL, JESD204 8B/10B, 40-bit internal datapath.
    (1.6384, 204.8): {
        "CH_HSPMUX": "0x3c3c",
        "CPLL_CFG0": "0x0ffa",
        "CPLL_CFG1": "0x0021",
        "CPLL_CFG2": "0x0202",
        "CPLL_FBDIV": "4",
        "CPLL_FBDIV_4_5": "4",
        "RXCDR_CFG0": "0x0003",
        "RXCDR_CFG2_GEN2": "0x245",
        "RXCDR_CFG2_GEN4": "0x0164",
        "RXCDR_CFG3": "0x0012",
        "RXCDR_CFG3_GEN2": "0x12",
        "RX_OUT_DIV": "4",
        "RX_CLK25_DIV": "9",
        "TX_OUT_DIV": "4",
        "TX_CLK25_DIV": "9",
    },
    (2.4576, 204.8): {
        "CH_HSPMUX": "0x3c3c",
        "CPLL_CFG0": "0x01fa",
        "CPLL_CFG1": "0x0023",
        "CPLL_CFG2": "0x0002",
        "CPLL_FBDIV": "3",
        "CPLL_FBDIV_4_5": "4",
        "RXCDR_CFG0": "0x0003",
        "RXCDR_CFG2_GEN2": "0x255",
        "RXCDR_CFG2_GEN4": "0x0164",
        "RXCDR_CFG3": "0x0012",
        "RXCDR_CFG3_GEN2": "0x12",
        "RX_OUT_DIV": "2",
        "RX_CLK25_DIV": "9",
        "TX_OUT_DIV": "2",
        "TX_CLK25_DIV": "9",
    },
    (3.2768, 204.8): {
        "CH_HSPMUX": "0x3c3c",
        "CPLL_CFG0": "0x0ffa",
        "CPLL_CFG1": "0x0021",
        "CPLL_CFG2": "0x0202",
        "CPLL_FBDIV": "4",
        "CPLL_FBDIV_4_5": "4",
        "RXCDR_CFG0": "0x0003",
        "RXCDR_CFG2_GEN2": "0x255",
        "RXCDR_CFG2_GEN4": "0x0164",
        "RXCDR_CFG3": "0x0012",
        "RXCDR_CFG3_GEN2": "0x12",
        "RX_OUT_DIV": "2",
        "RX_CLK25_DIV": "9",
        "TX_OUT_DIV": "2",
        "TX_CLK25_DIV": "9",
    },
}

SHARED_PARAMS = (
    "CH_HSPMUX",
    "CPLL_CFG0",
    "CPLL_CFG1",
    "CPLL_CFG2",
    "CPLL_FBDIV",
    "CPLL_FBDIV_4_5",
)

RX_CDR_PARAMS = (
    "RXCDR_CFG0",
    "RXCDR_CFG2_GEN2",
    "RXCDR_CFG2_GEN4",
    "RXCDR_CFG3",
    "RXCDR_CFG3_GEN2",
)

DYNAMIC_PARAMS = (
    "RX_OUT_DIV",
    "RX_CLK25_DIV",
    "TX_OUT_DIV",
    "TX_CLK25_DIV",
)


@dataclass
class SourceInfo:
    project_dir: str | None = None
    hdl_project_dir: str | None = None
    bd_path: str | None = None
    system_project_tcl: str | None = None
    operating_point: str | None = None


def _load_json(path: str) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _repo_root(path: str) -> str:
    cur = os.path.abspath(path)
    if os.path.isfile(cur):
        cur = os.path.dirname(cur)
    while True:
        if os.path.exists(os.path.join(cur, ".git")):
            return cur
        parent = os.path.dirname(cur)
        if parent == cur:
            return os.path.abspath(path)
        cur = parent


def _resolve_path(base: str, value: str) -> str:
    if os.path.isabs(value):
        return value
    root = _repo_root(base)
    candidates = [
        os.path.abspath(os.path.join(base, value)),
        os.path.abspath(os.path.join(root, value)),
    ]
    for candidate in candidates:
        if os.path.exists(candidate):
            return candidate
    return candidates[0]


def _find_profile_operating_point(project_dir: str, socks_cfg: dict[str, Any]) -> str | None:
    active = socks_cfg.get("adi", {}).get("active_profile")
    if not active:
        return None
    for search in socks_cfg.get("adi", {}).get("profile_search_path", []):
        search_dir = _resolve_path(project_dir, search)
        candidate = os.path.join(search_dir, active, "operating-point.json")
        if os.path.isfile(candidate):
            return candidate
    return None


def _resolve_from_project(project_dir: str) -> SourceInfo:
    cfg_path = os.path.join(project_dir, "socks.json")
    if not os.path.isfile(cfg_path):
        raise FileNotFoundError(f"socks.json not found: {cfg_path}")

    socks_cfg = _load_json(cfg_path)
    build = socks_cfg.get("build", {})
    adi_root = build.get("adi_root")
    build_project = build.get("project_dir")
    if not adi_root or not build_project:
        raise ValueError("socks.json build.adi_root and build.project_dir are required")

    hdl_project_dir = os.path.join(_resolve_path(project_dir, adi_root), build_project)
    info = SourceInfo(project_dir=project_dir, hdl_project_dir=hdl_project_dir)
    info.bd_path = _find_bd(hdl_project_dir)
    tcl = os.path.join(hdl_project_dir, "system_project.tcl")
    info.system_project_tcl = tcl if os.path.isfile(tcl) else None
    info.operating_point = _find_profile_operating_point(project_dir, socks_cfg)
    return info


def _find_bd(hdl_project_dir: str) -> str | None:
    patterns = [
        os.path.join(hdl_project_dir, "*.srcs", "sources_1", "bd", "*", "*.bd"),
        os.path.join(hdl_project_dir, "**", "*.bd"),
    ]
    for pattern in patterns:
        matches = sorted(glob.glob(pattern, recursive=True))
        if matches:
            return matches[0]
    return None


def _norm_key(rate: float, refclk: float) -> tuple[float, float]:
    return (round(float(rate), 4), round(float(refclk), 4))


def _clean_value(value: Any) -> str:
    text = str(value).strip()
    if len(text) >= 2 and text[0] == text[-1] == '"':
        text = text[1:-1]
    return text.strip()


def _parse_int(value: Any) -> int | None:
    text = _clean_value(value).lower().replace("_", "")
    if not text:
        return None
    if "'" in text:
        match = re.search(r"'([bodh])([0-9a-fx]+)$", text)
        if not match:
            return None
        base = {"b": 2, "o": 8, "d": 10, "h": 16}[match.group(1)]
        digits = match.group(2).replace("x", "0")
        return int(digits, base)
    try:
        return int(text, 0)
    except ValueError:
        return None


def _same_value(a: Any, b: Any) -> bool:
    ai = _parse_int(a)
    bi = _parse_int(b)
    if ai is not None and bi is not None:
        return ai == bi
    try:
        return abs(float(_clean_value(a)) - float(_clean_value(b))) < 1e-9
    except ValueError:
        return _clean_value(a).lower() == _clean_value(b).lower()


def _extract_params_from_bd(path: str) -> dict[str, str]:
    data = _load_json(path)
    design = data.get("design", {})
    cells = design.get("cells") or design.get("components") or {}
    for cell in cells.values():
        if cell.get("inst_hier_path") == "util_mxfe_xcvr":
            params = cell.get("parameters", {})
            return {key: _clean_value(value.get("value")) for key, value in params.items()}
    for cell in cells.values():
        if "util_mxfe_xcvr" in str(cell.get("inst_hier_path", "")):
            params = cell.get("parameters", {})
            return {key: _clean_value(value.get("value")) for key, value in params.items()}
    raise ValueError(f"util_mxfe_xcvr parameters not found in BD: {path}")


def _extract_params_from_tcl(path: str) -> dict[str, str]:
    params: dict[str, str] = {}
    pattern = re.compile(r"ad_ip_parameter\s+util_mxfe_xcvr\s+CONFIG\.([A-Za-z0-9_]+)\s+(.+?)\s*$")
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            match = pattern.search(line)
            if match:
                params[match.group(1)] = _clean_value(match.group(2))
    return params


def _extract_lane_rates_from_tcl(path: str) -> dict[str, float]:
    rates: dict[str, float] = {}
    pattern = re.compile(r"(RX_LANE_RATE|TX_LANE_RATE)\s+\[get_env_param\s+\1\s+([0-9.]+)\s*\]")
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            match = pattern.search(line)
            if match:
                rates[match.group(1)] = float(match.group(2))
    return rates


def _infer_refclk_from_operating_point(path: str | None) -> float | None:
    if not path or not os.path.isfile(path):
        return None
    data = _load_json(path)
    clocking = data.get("clocking", {})
    if "fpga_refclk_hz" in clocking:
        return float(clocking["fpga_refclk_hz"]) / 1e6
    hmc7044 = data.get("hmc7044", {})
    for item in hmc7044.get("outputs", []):
        name = str(item.get("name", ""))
        if "FPGA_REFCLK" in name and "frequency_mhz" in item:
            return float(item["frequency_mhz"])
    return None


def _choose_info(args: argparse.Namespace) -> SourceInfo:
    if args.project_dir:
        return _resolve_from_project(os.path.abspath(args.project_dir))
    info = SourceInfo()
    if args.hdl_project_dir:
        info.hdl_project_dir = os.path.abspath(args.hdl_project_dir)
        info.bd_path = _find_bd(info.hdl_project_dir)
        tcl = os.path.join(info.hdl_project_dir, "system_project.tcl")
        info.system_project_tcl = tcl if os.path.isfile(tcl) else None
    if args.bd:
        info.bd_path = os.path.abspath(args.bd)
    if args.system_project_tcl:
        info.system_project_tcl = os.path.abspath(args.system_project_tcl)
    return info


def _reference(rate: float, refclk: float) -> dict[str, str] | None:
    return REFERENCE_PARAMS.get(_norm_key(rate, refclk))


def _line(status: str, message: str) -> str:
    return f"{status:<7} {message}"


def _build_report(args: argparse.Namespace) -> tuple[dict[str, Any], list[str]]:
    info = _choose_info(args)

    if not info.bd_path and not info.system_project_tcl:
        raise FileNotFoundError("Provide --bd, --system-project-tcl, --hdl-project-dir, or --project-dir")

    params: dict[str, str] = {}
    param_source = None
    if info.bd_path and os.path.isfile(info.bd_path):
        params.update(_extract_params_from_bd(info.bd_path))
        param_source = info.bd_path
    elif info.system_project_tcl and os.path.isfile(info.system_project_tcl):
        params.update(_extract_params_from_tcl(info.system_project_tcl))
        param_source = info.system_project_tcl

    if not params:
        raise ValueError("No util_mxfe_xcvr parameters found")

    rates = {}
    if info.system_project_tcl and os.path.isfile(info.system_project_tcl):
        rates.update(_extract_lane_rates_from_tcl(info.system_project_tcl))
    if "RX_LANE_RATE" in params:
        rates["RX_LANE_RATE"] = float(params["RX_LANE_RATE"])
    if "TX_LANE_RATE" in params:
        rates["TX_LANE_RATE"] = float(params["TX_LANE_RATE"])

    rx_rate = args.rx_lane_rate if args.rx_lane_rate is not None else rates.get("RX_LANE_RATE")
    tx_rate = args.tx_lane_rate if args.tx_lane_rate is not None else rates.get("TX_LANE_RATE")
    refclk = args.refclk_mhz if args.refclk_mhz is not None else _infer_refclk_from_operating_point(info.operating_point)

    if rx_rate is None or tx_rate is None:
        raise ValueError("RX/TX lane rates are required; pass --rx-lane-rate and --tx-lane-rate")
    if refclk is None:
        raise ValueError("GT refclk is required; pass --refclk-mhz or use --project-dir with an operating-point.json")

    rx_ref = _reference(rx_rate, refclk)
    tx_ref = _reference(tx_rate, refclk)

    report: dict[str, Any] = {
        "param_source": param_source,
        "hdl_project_dir": info.hdl_project_dir,
        "operating_point": info.operating_point,
        "rx_lane_rate_gbps": rx_rate,
        "tx_lane_rate_gbps": tx_rate,
        "refclk_mhz": refclk,
        "rx_reference_known": rx_ref is not None,
        "tx_reference_known": tx_ref is not None,
        "shared": [],
        "rx_cdr": [],
        "dynamic": [],
        "verdict": "unknown",
    }
    lines = [
        "ADxCVR GT parameter check",
        f"  param source : {param_source}",
        f"  RX lane/ref  : {rx_rate:.4f} Gbps / {refclk:.4f} MHz",
        f"  TX lane/ref  : {tx_rate:.4f} Gbps / {refclk:.4f} MHz",
    ]

    if rx_ref is None or tx_ref is None:
        missing = []
        if rx_ref is None:
            missing.append(f"RX {rx_rate:.4f}/{refclk:.4f}")
        if tx_ref is None:
            missing.append(f"TX {tx_rate:.4f}/{refclk:.4f}")
        report["verdict"] = "unknown-reference"
        lines.append(_line("UNKNOWN", "No cached GT Wizard reference for " + ", ".join(missing)))
        return report, lines

    shared_conflicts = 0
    shared_mismatches = 0
    cdr_mismatches = 0

    lines.append("")
    lines.append("Shared/global util_mxfe_xcvr parameters:")
    for key in SHARED_PARAMS:
        active = params.get(key)
        rx_expected = rx_ref.get(key)
        tx_expected = tx_ref.get(key)
        entry = {
            "param": key,
            "active": active,
            "rx_expected": rx_expected,
            "tx_expected": tx_expected,
        }
        if active is None:
            entry["status"] = "missing"
            lines.append(_line("MISSING", f"{key}: expected RX={rx_expected}, TX={tx_expected}"))
        elif not _same_value(rx_expected, tx_expected):
            shared_conflicts += 1
            entry["status"] = "conflict"
            lines.append(_line("CONFLICT", f"{key}: active={active}, RX wants {rx_expected}, TX wants {tx_expected}"))
        elif not _same_value(active, rx_expected):
            shared_mismatches += 1
            entry["status"] = "mismatch"
            lines.append(_line("FAIL", f"{key}: active={active}, expected {rx_expected}"))
        else:
            entry["status"] = "pass"
            lines.append(_line("PASS", f"{key}: {active}"))
        report["shared"].append(entry)

    lines.append("")
    lines.append("RX CDR static parameters:")
    for key in RX_CDR_PARAMS:
        active = params.get(key)
        expected = rx_ref.get(key)
        entry = {"param": key, "active": active, "expected": expected}
        if active is None:
            entry["status"] = "missing"
            lines.append(_line("MISSING", f"{key}: expected {expected}"))
        elif not _same_value(active, expected):
            cdr_mismatches += 1
            entry["status"] = "mismatch"
            lines.append(_line("FAIL", f"{key}: active={active}, expected {expected}"))
        else:
            entry["status"] = "pass"
            lines.append(_line("PASS", f"{key}: {active}"))
        report["rx_cdr"].append(entry)

    lines.append("")
    lines.append("Dynamic divider fields usually rewritten by no-OS/ADXCVR DRP:")
    for key in DYNAMIC_PARAMS:
        active = params.get(key)
        expected = rx_ref.get(key) if key.startswith("RX_") else tx_ref.get(key)
        entry = {"param": key, "active": active, "expected": expected}
        if active is None:
            entry["status"] = "missing"
            lines.append(_line("NOTE", f"{key}: not present in HDL params; expected runtime target {expected}"))
        elif not _same_value(active, expected):
            entry["status"] = "runtime-mismatch"
            lines.append(_line("NOTE", f"{key}: HDL active={active}, runtime target {expected}"))
        else:
            entry["status"] = "pass"
            lines.append(_line("PASS", f"{key}: {active}"))
        report["dynamic"].append(entry)

    if shared_conflicts:
        verdict = "needs per-channel/split util or lane-rate retarget"
    elif shared_mismatches:
        verdict = "safe global override candidate"
    elif cdr_mismatches:
        verdict = "likely RX CDR-only issue"
    else:
        verdict = "static HDL matches cached references"
    report["verdict"] = verdict
    lines.append("")
    lines.append(f"Verdict: {verdict}")
    return report, lines


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-dir", help="SOCKS project directory containing socks.json")
    parser.add_argument("--hdl-project-dir", help="ADI HDL project directory, e.g. ADI/projects/gps_streaming")
    parser.add_argument("--bd", help="Generated Vivado .bd JSON file")
    parser.add_argument("--system-project-tcl", help="ADI system_project.tcl fallback")
    parser.add_argument("--rx-lane-rate", type=float, help="Override RX lane rate in Gbps")
    parser.add_argument("--tx-lane-rate", type=float, help="Override TX lane rate in Gbps")
    parser.add_argument("--refclk-mhz", type=float, help="FPGA GT reference clock in MHz")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    args = parser.parse_args()

    try:
        report, lines = _build_report(args)
    except Exception as exc:  # noqa: BLE001 - CLI should report concise failures.
        if args.json:
            print(json.dumps({"error": str(exc)}, indent=2))
        else:
            print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print("\n".join(lines))
    return 0 if not str(report["verdict"]).startswith("unknown") else 1


if __name__ == "__main__":
    raise SystemExit(main())
