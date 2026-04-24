"""Three-way anchor term decomposition (cursor-timing-debug-PLAN Step 5 / Root Cause A).

For each anchor event, print:

  claimed_sv  - receiver's live_anchor_tow_s = tow_6s * 6.0 (minus sub-dump corr)
  iqgen_sv    - t_rx + tx_offset_profile(t_rx), i.e. what gps_scenario._build_scenario_profiles
                embedded into the IQ via tx_offset = -obs.range_m / C
                (Euclidean range at rx-time, no Sagnac, no SV clock bias)
  oracle_sv   - _iterated_truth(engine, prn, t_rx), i.e. diagnose_anchor_truth.py's
                physically-correct oracle (light-time iterated range + Sagnac + clock bias)

Decision rule per plan:
  * |claimed - iqgen| < 1 us on all SVs  -> receiver is decoding faithfully;
                                           IQ gen is missing Sagnac + clock bias.
  * |claimed - oracle| < 1 us            -> receiver matches physical truth;
                                           oracle has an unknown over-correction.
  * Both differ significantly            -> independent receiver bug.
"""

from __future__ import annotations

import argparse
import math
import os
from typing import Dict, Tuple

import numpy as np

from gps_receiver.receiver import GPSReceiver  # noqa: F401
import gps_scenario
from gps_scenario import build_config
from scenario_engine.constants import C, OMEGA_E
from scenario_engine.orbit_propagator import propagate_sv

# Reuse the existing diagnostic harness so we get the same IQ + receiver run.
from diagnose_anchor_truth import _run_scenario_with_progress, _iterated_truth


def _iqgen_tx_time(engine, prn: int, rx_gps_time_s: float) -> Tuple[float, float]:
    """Replicate gps_scenario._build_scenario_profiles's tx_offset for a PRN.

    tx_offset(t_rx) = -obs.range_m(t_rx) / C
    tx_time (embedded) = t_rx + tx_offset = t_rx - euclid_range_at_rx/c

    Returns (iqgen_tx_time_s, euclid_range_m).
    """
    eph = next(e for e in engine.ephemerides if e.prn == prn)
    sv = propagate_sv(eph, rx_gps_time_s)  # evaluated at rx-time, matching iq-gen
    rx_ecef, _ = engine.receiver.state_at(rx_gps_time_s)
    dr = sv.ecef_m - rx_ecef
    rng = float(np.linalg.norm(dr))
    return rx_gps_time_s - rng / C, rng


def _sv_clock_and_sagnac(engine, prn: int, rx_gps_time_s: float):
    """Return (clock_bias_s at rx-time, sagnac_delay_s at rx-time geometry)."""
    eph = next(e for e in engine.ephemerides if e.prn == prn)
    rx_ecef, _ = engine.receiver.state_at(rx_gps_time_s)

    # Light-time iterated tau (same as oracle) for sagnac computation.
    tau = 0.07
    for _ in range(6):
        sv = propagate_sv(eph, rx_gps_time_s - tau)
        theta = OMEGA_E * tau
        ct = math.cos(theta)
        sn = math.sin(theta)
        sv_rot = np.array(
            [
                ct * sv.ecef_m[0] + sn * sv.ecef_m[1],
                -sn * sv.ecef_m[0] + ct * sv.ecef_m[1],
                sv.ecef_m[2],
            ]
        )
        tau = float(np.linalg.norm(sv_rot - rx_ecef)) / C

    # Euclidean range at rx-time (matches iq-gen).
    sv_rx = propagate_sv(eph, rx_gps_time_s)
    euclid_rx = float(np.linalg.norm(sv_rx.ecef_m - rx_ecef))
    tau_euclid_rx = euclid_rx / C

    # Sagnac delay contribution = tau_iterated - tau_euclid_at_rx
    sagnac_plus_lightime_s = tau - tau_euclid_rx
    return float(sv.clock_bias_s), sagnac_plus_lightime_s


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", default="scenario_static")
    parser.add_argument("--duration-s", type=float, default=50.0)
    parser.add_argument("--wn", type=int, default=2345)
    parser.add_argument("--progress-interval-s", type=float, default=5.0)
    parser.add_argument("--chunk-duration-s", type=float, default=1.0)
    parser.add_argument(
        "--fixed-block", action="store_true",
        help="Force fixed-block path for comparison (default: cursor path).",
    )
    args = parser.parse_args()

    config = build_config(args.mode)
    config.iq_defaults["nav_data"] = "subframe"
    config.iq_defaults["duration_s"] = args.duration_s
    config.iq_defaults["wn"] = args.wn

    print(
        f"[diag] starting anchor-term decomposition mode={args.mode} "
        f"duration_s={args.duration_s} path="
        f"{'fixed-block' if args.fixed_block else 'cursor'}"
    )

    result = _run_scenario_with_progress(
        mode=args.mode,
        duration_s=args.duration_s,
        config=config,
        use_bit_select=False,
        progress_interval_s=args.progress_interval_s,
        chunk_duration_s=args.chunk_duration_s,
        use_preamble_sync=True,
        force_fixed_block=args.fixed_block,
    )
    rx = result.rx
    engine = result.engine
    if rx is None or engine is None:
        raise RuntimeError("Scenario produced no rx/engine")

    print("\n=== per-anchor term decomposition ===")
    header = (
        "rx_s    prn sfid tow6  claimed_sv     iqgen_sv       oracle_sv      "
        "(c-i)us   (o-i)us   (c-o)us   clk_us    sag+lt_us"
    )
    print(header)

    per_prn: Dict[int, list] = {}
    for ev in rx.anchor_events:
        if not ev.get("anchored"):
            continue
        prn = ev["prn"]
        anch_abs = ev.get("live_anchor_abs_sample_frac")
        claimed_sv = ev.get("live_anchor_tow_s")
        if anch_abs is None or claimed_sv is None:
            continue
        rx_time = rx.start_gps_time_s + float(anch_abs) / rx.fs
        iqgen_sv, _ = _iqgen_tx_time(engine, prn, rx_time)
        oracle_sv, _ = _iterated_truth(engine, prn, rx_time)
        clock_bias_s, sagnac_lt_s = _sv_clock_and_sagnac(engine, prn, rx_time)

        ci_us = (claimed_sv - iqgen_sv) * 1e6
        oi_us = (oracle_sv - iqgen_sv) * 1e6
        co_us = (claimed_sv - oracle_sv) * 1e6
        clk_us = clock_bias_s * 1e6
        sag_us = sagnac_lt_s * 1e6

        row = (
            f"{rx_time:6.2f} {prn:3d} {ev['subframe_id']:4d} "
            f"{ev['tow_6s']:4d}  "
            f"{claimed_sv:12.6f}  {iqgen_sv:12.6f}  {oracle_sv:12.6f}  "
            f"{ci_us:+9.3f} {oi_us:+9.3f} {co_us:+9.3f}  "
            f"{clk_us:+8.3f}  {sag_us:+8.3f}"
        )
        print(row)
        per_prn.setdefault(prn, []).append((ci_us, oi_us, co_us, clk_us, sag_us))

    print("\n=== per-SV summary (mean across anchors) ===")
    print("prn   n  mean(c-i)us  mean(o-i)us  mean(c-o)us  mean_clk_us  mean_sag+lt_us")
    all_ci, all_oi, all_co = [], [], []
    for prn in sorted(per_prn):
        rows = per_prn[prn]
        n = len(rows)
        arr = np.asarray(rows, dtype=float)
        means = arr.mean(axis=0)
        all_ci.append(means[0])
        all_oi.append(means[1])
        all_co.append(means[2])
        print(
            f"{prn:3d}  {n:2d}   {means[0]:+10.3f}   {means[1]:+10.3f}   "
            f"{means[2]:+10.3f}   {means[3]:+9.3f}   {means[4]:+9.3f}"
        )

    def _range(xs):
        return max(xs) - min(xs)

    print("\n=== decision rule ===")
    ci_range = _range(all_ci)
    oi_range = _range(all_oi)
    co_range = _range(all_co)
    print(f"per-SV spread of (claimed - iqgen): {ci_range:.3f} us")
    print(f"per-SV spread of (oracle  - iqgen): {oi_range:.3f} us")
    print(f"per-SV spread of (claimed - oracle): {co_range:.3f} us")
    print(f"max |mean(c-i)|: {max(abs(x) for x in all_ci):.3f} us")
    print(f"max |mean(o-i)|: {max(abs(x) for x in all_oi):.3f} us")
    print(f"max |mean(c-o)|: {max(abs(x) for x in all_co):.3f} us")

    if max(abs(x) for x in all_ci) < 1.0:
        print("\nCONCLUSION: receiver claim matches IQ-gen embedding to <1 us.")
        print("            => IQ gen is the outlier (missing Sagnac + SV clock bias).")
        print("            => Fix location: gps_scenario.py:481 (_build_scenario_profiles).")
    elif max(abs(x) for x in all_co) < 1.0:
        print("\nCONCLUSION: receiver claim matches oracle truth to <1 us.")
        print("            => IQ gen is physically correct; oracle over-corrected?")
    else:
        print("\nCONCLUSION: receiver diverges from BOTH iq-gen and oracle.")
        print("            => Independent receiver bug.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
