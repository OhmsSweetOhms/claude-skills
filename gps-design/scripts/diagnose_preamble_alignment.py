"""Preamble-alignment diagnostic (cursor-timing-debug-PLAN Step 6 / Root Cause B revised).

For each SV's first preamble-lock event, compare:

    claimed: lock_ev.preamble_start_sample_idx
             (rx-sample at END of first 1-ms slot of preamble bit 0,
              per ps_b10a_preamble_sync.py:105 and the dump_end_sample_idx
              convention at receiver.py:308)
    truth:   the physically correct rx-sample for that same instant,
             derived from iqgen_sv(rx_sample) = t_rx - euclid_range(t_rx)/c

At a physically-correct lock, iqgen_sv(preamble_start_sample_idx) should
land exactly 1 ms past a nav-bit boundary — i.e.,

    ((iqgen_sv * 1000) - 1.0) mod 20.0 == 0

Any deviation is the preamble lock latching onto the wrong position
within (or across) a 20-ms nav bit. Decompose the deviation into:

    - integer-20-ms component (lock on wrong nav bit entirely — e.g.,
      last ms of the previous nav bit vs first ms of the next one)
    - integer-1-ms component (lock on wrong code epoch within the 20-ms
      nav bit; 0-19 range)
    - sub-ms residual (sub-dump rounding; the generator's bit edge falls
      mid-dump and PS.B10a picked the nearest dump)

Decision rule:
    - all components near zero -> preamble lock is correct;
                                  the floor is a downstream PS.B10/B11 bug
    - nonzero integer-ms component -> PS.B10a latched onto wrong 1-ms slot
    - nonzero sub-ms residual only -> generator vs receiver dump-grid offset;
                                      receiver rounds to nearest integer dump
"""

from __future__ import annotations

import argparse
import math
from typing import Dict

import numpy as np

import gps_scenario  # noqa: F401
from gps_scenario import build_config
from scenario_engine.constants import C
from scenario_engine.orbit_propagator import propagate_sv

from diagnose_anchor_truth import _run_scenario_with_progress


def _iqgen_sv_time(engine, prn: int, rx_gps_time_s: float) -> float:
    """Replicate gps_scenario._build_scenario_profiles tx-time embedding.

    iqgen_sv(t_rx) = t_rx - euclid_range_at_rx/c    (no Sagnac, no clock bias)
    """
    eph = next(e for e in engine.ephemerides if e.prn == prn)
    sv = propagate_sv(eph, rx_gps_time_s)
    rx_ecef, _ = engine.receiver.state_at(rx_gps_time_s)
    rng = float(np.linalg.norm(sv.ecef_m - rx_ecef))
    return rx_gps_time_s - rng / C


def _decompose_nav_bit_residual_ms(iqgen_sv_s: float) -> dict:
    """Return decomposition of iqgen_sv against the physically correct pattern
    (1 ms past a 20-ms nav-bit boundary).

    The correct preamble_start_sample_idx in SV-time is
        sv_target(k) = 0.020·k + 0.001   for some integer k
    The observed deviation is  observed - sv_target(k_nearest).
    Split into integer-ms and sub-ms.
    """
    # SV-time within the 20 ms window, relative to the "+1 ms" reference.
    offset_ms = (iqgen_sv_s * 1000.0 - 1.0) % 20.0
    # Represent as signed (-10, +10] ms so "close to zero" means "close to correct".
    if offset_ms > 10.0:
        offset_ms -= 20.0
    int_ms = int(round(offset_ms))
    sub_ms = offset_ms - int_ms
    return {
        "total_err_ms": offset_ms,
        "int_epoch_err": int_ms,
        "sub_epoch_err_us": sub_ms * 1000.0,
    }


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
        f"[diag] starting preamble-alignment diagnostic mode={args.mode} "
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

    # Build a per-PRN lookup of first anchor rx_time and tow_6s, for
    # downstream cross-check.
    first_anchor: Dict[int, dict] = {}
    for ev in rx.anchor_events:
        if not ev.get("anchored"):
            continue
        prn = ev["prn"]
        if prn in first_anchor:
            continue
        first_anchor[prn] = ev

    print("\n=== preamble-lock alignment ===")
    header = (
        "prn  lock_epoch  preamble_start_sample  rx_time_s    iqgen_sv_s    "
        "total_err_ms  int_epoch_err  sub_epoch_err_us"
    )
    print(header)

    missing = []
    rows = []
    for prn in sorted(rx.channels):
        ch = rx.channels[prn]
        lock_ev = getattr(ch, "_last_preamble_lock_event", None)
        if lock_ev is None:
            missing.append(prn)
            continue
        if lock_ev.preamble_start_sample_idx is None:
            missing.append(prn)
            continue

        sample = int(lock_ev.preamble_start_sample_idx)
        rx_time = rx.start_gps_time_s + sample / rx.fs
        iqgen_sv = _iqgen_sv_time(engine, prn, rx_time)
        decomp = _decompose_nav_bit_residual_ms(iqgen_sv)
        row = (prn, int(lock_ev.preamble_start_epoch or -1), sample,
               rx_time, iqgen_sv,
               decomp["total_err_ms"], decomp["int_epoch_err"],
               decomp["sub_epoch_err_us"])
        rows.append(row)
        print(
            f"{prn:3d}  {row[1]:10d}  {sample:21d}  {rx_time:10.3f}  "
            f"{iqgen_sv:12.6f}  {decomp['total_err_ms']:+11.3f}  "
            f"{decomp['int_epoch_err']:+13d}  {decomp['sub_epoch_err_us']:+13.3f}"
        )

    if missing:
        print(f"\n[warn] PRNs with no lock event: {sorted(missing)}")

    # Classification
    if rows:
        total_errs = [r[5] for r in rows]
        int_errs = [r[6] for r in rows]
        sub_errs = [r[7] for r in rows]
        print("\n=== classification ===")
        print(f"n_svs: {len(rows)}")
        print(f"total_err_ms  min={min(total_errs):+.3f}  max={max(total_errs):+.3f}  "
              f"spread={max(total_errs)-min(total_errs):+.3f}")
        print(f"int_epoch_err  unique values: {sorted(set(int_errs))}")
        print(f"sub_epoch_err_us  min={min(sub_errs):+.3f}  "
              f"max={max(sub_errs):+.3f}  spread={max(sub_errs)-min(sub_errs):+.3f}")

        max_abs_int = max(abs(x) for x in int_errs)
        max_abs_sub = max(abs(x) for x in sub_errs)
        print()
        if max_abs_int == 0 and max_abs_sub < 50.0:
            print("CONCLUSION: preamble lock is correct to <50 us.")
            print("            => The per-SV floor is a DOWNSTREAM bug.")
            print("            => Investigate PS.B10 300-bit forward-count or")
            print("               PS.B11 subframe_end_sample_idx computation.")
        elif max_abs_int != 0:
            print(f"CONCLUSION: PS.B10a latches to wrong 1-ms slot within the")
            print(f"            20-ms nav bit (int_epoch_err range: "
                  f"{min(int_errs)} to {max(int_errs)} ms per SV).")
            print("            => Investigate PS.B10a _evaluate_candidate's")
            print("               selection of 'start' buffer index.")
        else:
            print(f"CONCLUSION: sub-ms rounding (max |residual| = "
                  f"{max_abs_sub:.1f} us).")
            print("            => PS.B10a picks the enclosing 1-ms dump;")
            print("               generator's bit edge falls mid-dump.")
            print("            => Preamble timestamp needs sub-sample")
            print("               interpolation to avoid per-SV rounding.")

    # Downstream cross-check: compare preamble_start_sample_idx vs
    # (sf_end_sample_idx - 300 bits).
    print("\n=== downstream cross-check (preamble vs sf_end - 300 bits) ===")
    from gps_receiver.constants import SAMPLES_PER_CODE
    SAMPLES_PER_SUBFRAME = 300 * 20 * SAMPLES_PER_CODE  # nominal, no Doppler
    print("prn  preamble_start  sf_end_sample  delta_samples  delta_vs_300bits  delta_us")
    for prn, anch in sorted(first_anchor.items()):
        ch = rx.channels.get(prn)
        if ch is None:
            continue
        lock_ev = getattr(ch, "_last_preamble_lock_event", None)
        if lock_ev is None or lock_ev.preamble_start_sample_idx is None:
            continue
        preamble_start = int(lock_ev.preamble_start_sample_idx)
        sf_end = int(anch["live_anchor_sample_idx"])
        delta = sf_end - preamble_start
        delta_vs_nominal = delta - SAMPLES_PER_SUBFRAME
        delta_us = delta_vs_nominal / rx.fs * 1e6
        print(
            f"{prn:3d}  {preamble_start:14d}  {sf_end:13d}  "
            f"{delta:13d}  {delta_vs_nominal:+16d}  {delta_us:+8.3f}"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
