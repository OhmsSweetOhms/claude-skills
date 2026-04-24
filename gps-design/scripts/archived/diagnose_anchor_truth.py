#!/usr/bin/env python3
"""Anchor-truth diagnostic for the Stage 2 scenario_static PVT path.

Measures the per-SV residual at the first accepted PVT fix after the
SoftGNSS-style ``abs_sample_frac`` + interpolation anchor replaces the
sign-flip-histogram frac_ms estimator.

Columns:
  PRN              SV id
  rx_gps           rx-clock GPS time at the first-fix strobe (s)
  true_tx_s        ground-truth SV transmit time at the strobe (s)
  meas_tx_s        PS.B13.measure_tx_time(strobe) interpolated from the
                   time table (s)
  interp_err_us    (meas_tx_s - true_tx_s) × 1e6 — the per-SV residual
                   at the strobe. Target ≪ 1 µs.
  fix_resid_us     (pr_meas - pr_true)/c × 1e6 — the pseudorange residual
                   fed to the PVT solver. Same physics, different output.
  hist_len         number of logged dumps in PS.B13's ring at the strobe

The gate is ``interp_err_us`` ≪ 1 µs per SV. If that holds, the
cold-start PVT should fix well under 200 m (c × 1e-6 ≈ 300 m upper
bound if interp_err_us were ~1 µs).
"""

import argparse
import math
import os
import time
from typing import Dict, Tuple

import numpy as np

from gps_iq_gen.gps_iq_gen import generate_gps_iq
from gps_receiver.constants import SAMPLES_PER_CODE
from gps_receiver.receiver import GPSReceiver
import gps_scenario
from gps_scenario import ScenarioResult, build_config
from scenario_engine.constants import C, OMEGA_E
from scenario_engine.orbit_propagator import propagate_sv


def _iterated_truth(engine, prn: int, rx_gps_time_s: float) -> Tuple[float, float]:
    """Return (true_tx_time_s, true_pseudorange_m) at a receive-time sample."""
    eph = next(e for e in engine.ephemerides if e.prn == prn)
    rx_ecef, _ = engine.receiver.state_at(rx_gps_time_s)
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
    true_tx = rx_gps_time_s - (tau - sv.clock_bias_s)
    true_pr = tau * C - sv.clock_bias_s * C
    return true_tx, true_pr


def _first_fix_truth_error(result) -> float:
    first = result.rx.pvt_fixes[0]
    rover_true, _ = result.engine.receiver.state_at(first.gps_time_s)
    return float(np.linalg.norm(first.position_ecef_m - rover_true))


def _find_first_fix_attempt(result):
    first_fix_gps = result.rx.pvt_fixes[0].gps_time_s
    for attempt in result.rx.pvt_attempts:
        if abs(attempt["gps_time_s"] - first_fix_gps) < 1e-9:
            return attempt
    raise RuntimeError("Could not find the PVT attempt that produced the first fix")


def _collect_first_fix_residuals(result, engine) -> Dict[int, float]:
    rx = result.rx
    first_attempt = _find_first_fix_attempt(result)
    gps_time_s = first_attempt["gps_time_s"]
    residuals_us = {}
    for prn, pr_meas in sorted(first_attempt["prs_m"].items()):
        _, pr_true = _iterated_truth(engine, prn, gps_time_s)
        residuals_us[prn] = (pr_meas - pr_true) / C * 1e6
    return residuals_us


def _print_progress(prefix: str, **fields) -> None:
    parts = [prefix]
    parts.extend(f"{key}={value}" for key, value in fields.items())
    print(" ".join(parts), flush=True)


def _format_sf_cache_summary(rx: GPSReceiver) -> str:
    parts = []
    for prn in sorted(rx.sf_cache):
        cache = rx.sf_cache[prn]
        if not cache:
            continue
        ids = "".join(str(sfid) for sfid in sorted(cache))
        tows = ",".join(f"{sfid}:{int(cache[sfid]['tow_6s'])}" for sfid in sorted(cache))
        parts.append(f"{prn}[{ids}|{tows}]")
    return ";".join(parts) if parts else "-"


def _format_recent_anchor_summary(rx: GPSReceiver, limit: int = 6) -> str:
    if not rx.anchor_events:
        return "-"
    recent = rx.anchor_events[-limit:]
    parts = []
    for ev in recent:
        parts.append(
            f"{ev['prn']}:{ev['subframe_id']}@{ev['tow_6s']}"
        )
    return ",".join(parts)


def _format_nav_pipeline_summary(rx: GPSReceiver) -> str:
    parts = []
    for prn in sorted(rx.channels):
        ts = rx.channels[prn]
        nb = getattr(ts, "nav_bit", None)
        dec = rx.subframe_decoders.get(prn)
        if nb is None:
            continue
        total_bits = int(getattr(nb, "_total_bits", 0))
        synced = bool(getattr(dec, "_synced", False)) if dec is not None else False
        parts.append(f"{prn}:bits={total_bits}/sync={'Y' if synced else 'N'}")
    return ";".join(parts) if parts else "-"


def _format_preamble_sync_summary(rx: GPSReceiver) -> str:
    parts = []
    for prn in sorted(rx.channels):
        ts = rx.channels[prn]
        ps = getattr(ts, "preamble_sync", None)
        if ps is None:
            continue
        state = ps.get_state()
        locked = "L" if state.get("locked") else "-"
        parts.append(
            f"{prn}:{locked}/b{state.get('buffered_ms', 0)}"
            f"/c{state.get('candidate_count', 0)}"
        )
    return ";".join(parts) if parts else "-"


def _run_scenario_with_progress(
    mode: str,
    duration_s: float,
    config,
    seed: int = 42,
    use_bit_select: bool = False,
    progress_interval_s: float = 1.0,
    chunk_duration_s: float = 1.0,
    use_preamble_sync: bool = False,
    force_fixed_block: bool = False,
) -> ScenarioResult:
    iq_defaults = config.iq_defaults
    doppler_type = iq_defaults.get("doppler_type", "constant")
    if doppler_type != "scenario":
        raise ValueError("diagnose_anchor_truth requires doppler_type='scenario'")

    _print_progress(
        "[diag]",
        phase="build_profiles",
        mode=mode,
        duration_s=f"{duration_s:.1f}",
    )
    scenario_config_path = gps_scenario._resolve_scenario_config_path(
        iq_defaults, config.mode_profiles_dir
    )
    engine = gps_scenario._build_engine_from_scenario_json(scenario_config_path)
    start_gps_s = float(iq_defaults.get("start_gps_time_s", 0.0))
    step_ms = float(iq_defaults.get("epoch_step_ms", 1.0))
    (
        doppler_profiles,
        cn0_profiles,
        tx_time_offset_profiles,
        sat_configs,
    ) = gps_scenario._build_scenario_profiles(
        engine, start_gps_s, duration_s, satellites=None, step_ms=step_ms
    )

    nav_data_mode = iq_defaults.get("nav_data", "all_ones")
    nav_generator = None
    sim_start_offset_s = 0.0
    if nav_data_mode == "subframe":
        from gps_receiver.nav_gen import NavDataGenerator

        wn = int(iq_defaults.get("wn", 2345))
        tow_start = int(start_gps_s - (start_gps_s % 6.0))
        sim_start_offset_s = float(start_gps_s - tow_start)
        nav_generator = NavDataGenerator(
            ephemerides=engine.ephemerides, wn=wn, tow_start=tow_start
        )

    # Per ps-b10a-preamble-sync-PLAN.md verification step 2, the preamble-sync
    # path is the one the plan wants validated once PS.B10a is wired in. The
    # histogram-align path (align_nav_bit_boundary=True) is the legacy path
    # retained only until step 5 of the verification sequence.
    align_hist = (nav_data_mode == "subframe") and not use_preamble_sync
    _print_progress(
        "[diag]",
        phase="receiver_config",
        use_preamble_sync=bool(use_preamble_sync),
        align_nav_bit_boundary=align_hist,
        cursor_driven=bool(use_preamble_sync),
    )
    rx = GPSReceiver(
        profile=config.receiver_profile,
        use_bit_select=use_bit_select,
        start_gps_time_s=start_gps_s,
        reference_wn=int(iq_defaults.get("wn", 2345)),
        align_nav_bit_boundary=align_hist,
        use_preamble_sync=bool(use_preamble_sync),
    )
    # When the new preamble-sync path is enabled, also drive variable-read
    # PL.B3 via per-channel cursors (Phase 4 of the integration plan).
    # That removes the per-SV +/- 0.5 ms code-epoch-to-rx-sample timing
    # ambiguity that the fixed-block process_ms path has under non-zero
    # Doppler. Channels are added AFTER opening the streaming source so
    # add_channel() automatically opens cursors at sample 0.
    #
    # ``force_fixed_block`` decouples preamble-sync from cursor-drive so
    # Step 4 of cursor-timing-debug-PLAN.md can compare per-SV anchor
    # residuals between the two paths on identical IQ.
    cursor_driven = bool(use_preamble_sync) and not force_fixed_block
    if cursor_driven:
        from gps_receiver.constants import FS_HZ as _FS_HZ
        capacity = int(_FS_HZ * duration_s) + SAMPLES_PER_CODE
        rx.open_iq_source_streaming(capacity_samples=capacity)
    for sat in sat_configs:
        rx.add_channel(sat["prn"], sat["doppler_hz"], sat["code_phase_chips"])

    total_epochs = int(duration_s * 1000.0)
    epoch_results = []
    start_wall = time.time()
    next_report = start_wall + max(0.1, progress_interval_s)
    first_fix_logged = False
    epoch_idx = 0
    chunk_duration_s = float(chunk_duration_s)
    if chunk_duration_s <= 0.0:
        raise ValueError(f"chunk_duration_s must be positive, got {chunk_duration_s}")
    total_chunks = int(math.ceil(duration_s / chunk_duration_s))
    _print_progress(
        "[diag]",
        phase="tracking",
        epochs=total_epochs,
        chunk_duration_s=f"{chunk_duration_s:.3f}",
        chunks=total_chunks,
    )
    for chunk_idx in range(total_chunks):
        chunk_start_s = chunk_idx * chunk_duration_s
        chunk_s = min(chunk_duration_s, duration_s - chunk_start_s)
        _print_progress(
            "[diag]",
            phase="generate_iq_chunk",
            chunk=f"{chunk_idx + 1}/{total_chunks}",
            start_s=f"{chunk_start_s:.3f}",
            duration_s=f"{chunk_s:.3f}",
        )
        t0 = time.time()
        iq_chunk = generate_gps_iq(
            satellites=sat_configs,
            duration_s=chunk_s,
            nav_data=nav_data_mode,
            noise_rms_dbfs=iq_defaults.get("noise_rms_dbfs", -12.0),
            seed=seed + chunk_idx,
            doppler_profiles=doppler_profiles,
            cn0_profiles=cn0_profiles,
            nav_generator=nav_generator,
            sim_start_offset_s=sim_start_offset_s,
            tx_time_offset_profiles=tx_time_offset_profiles,
            start_time_s=chunk_start_s,
        )
        _print_progress(
            "[diag]",
            phase="generate_iq_done",
            chunk=f"{chunk_idx + 1}/{total_chunks}",
            wall_s=f"{time.time() - t0:.1f}",
            samples=len(iq_chunk),
        )

        if cursor_driven:
            # Append the chunk to the receiver-owned shared source, then
            # drive each channel forward by one of its OWN code epochs at
            # a time until at least one channel runs out of data. PL.B3
            # variable blksize means cursors diverge with Doppler; the
            # PS.B13 common-strobe rule tolerates that divergence.
            rx.append_iq_chunk(iq_chunk)
            while True:
                epoch = rx.process_one_code_per_channel()
                if not epoch:
                    break
                epoch_results.append(epoch)
                epoch_idx += 1

                now = time.time()
                if rx.pvt_fixes and not first_fix_logged:
                    fix = rx.pvt_fixes[0]
                    _print_progress(
                        "[diag]",
                        phase="first_fix",
                        epoch=epoch_idx,
                        gps_time_s=f"{fix.gps_time_s:.3f}",
                        n_svs=fix.n_svs,
                        gdop=f"{fix.gdop:.2f}",
                        wall_s=f"{now - start_wall:.1f}",
                    )
                    first_fix_logged = True
                if now >= next_report:
                    _print_progress(
                        "[diag]",
                        phase="tracking_progress",
                        epoch=f"{epoch_idx}/{total_epochs}",
                        sim_s=f"{epoch_idx / 1000.0:.1f}",
                        anchors=len(rx.anchor_events),
                        recent_anchors=_format_recent_anchor_summary(rx),
                        sf_cache=_format_sf_cache_summary(rx),
                        preamble_sync=_format_preamble_sync_summary(rx),
                        nav_pipeline=_format_nav_pipeline_summary(rx),
                        eph=len(rx.ephemerides),
                        fixes=len(rx.pvt_fixes),
                        wall_s=f"{now - start_wall:.1f}",
                    )
                    next_report = now + max(0.1, progress_interval_s)
            continue  # next chunk

        usable = len(iq_chunk) - (len(iq_chunk) % SAMPLES_PER_CODE)
        for start in range(0, usable, SAMPLES_PER_CODE):
            ms_block = iq_chunk[start:start + SAMPLES_PER_CODE]
            epoch = rx.process_ms(ms_block)
            epoch_results.append(epoch)
            epoch_idx += 1

            now = time.time()
            if rx.pvt_fixes and not first_fix_logged:
                fix = rx.pvt_fixes[0]
                _print_progress(
                    "[diag]",
                    phase="first_fix",
                    epoch=epoch_idx,
                    gps_time_s=f"{fix.gps_time_s:.3f}",
                    n_svs=fix.n_svs,
                    gdop=f"{fix.gdop:.2f}",
                    wall_s=f"{now - start_wall:.1f}",
                )
                first_fix_logged = True
            if now >= next_report:
                _print_progress(
                    "[diag]",
                    phase="tracking_progress",
                    epoch=f"{epoch_idx}/{total_epochs}",
                    sim_s=f"{epoch_idx / 1000.0:.1f}",
                    anchors=len(rx.anchor_events),
                    recent_anchors=_format_recent_anchor_summary(rx),
                    sf_cache=_format_sf_cache_summary(rx),
                    preamble_sync=_format_preamble_sync_summary(rx),
                    nav_pipeline=_format_nav_pipeline_summary(rx),
                    eph=len(rx.ephemerides),
                    fixes=len(rx.pvt_fixes),
                    wall_s=f"{now - start_wall:.1f}",
                )
                next_report = now + max(0.1, progress_interval_s)

    _print_progress(
        "[diag]",
        phase="tracking_done",
        wall_s=f"{time.time() - start_wall:.1f}",
        anchors=len(rx.anchor_events),
        recent_anchors=_format_recent_anchor_summary(rx),
        sf_cache=_format_sf_cache_summary(rx),
        preamble_sync=_format_preamble_sync_summary(rx),
        eph=len(rx.ephemerides),
        fixes=len(rx.pvt_fixes),
    )
    return ScenarioResult(
        iq=np.empty(0, dtype=np.complex128),
        rx=rx,
        epoch_results=epoch_results,
        mode_config=config.mode,
        satellites=sat_configs,
        acq_results={},
        config=config,
        engine=engine,
        pvt_fixes=list(rx.pvt_fixes),
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", default="scenario_static")
    parser.add_argument("--duration-s", type=float, default=40.0)
    parser.add_argument("--wn", type=int, default=2345)
    parser.add_argument("--progress-interval-s", type=float, default=5.0)
    parser.add_argument("--chunk-duration-s", type=float, default=1.0)
    parser.add_argument(
        "--use-preamble-sync",
        action="store_true",
        help=(
            "Use the PS.B10a preamble-sync nav path (plan verification step 2) "
            "instead of the legacy histogram-align path."
        ),
    )
    parser.add_argument(
        "--fixed-block",
        action="store_true",
        help=(
            "Force fixed-block process_ms() even when --use-preamble-sync is on. "
            "Used by cursor-timing-debug-PLAN.md Step 4 to compare anchor "
            "residuals between cursor-driven and fixed-block paths."
        ),
    )
    args = parser.parse_args()

    config = build_config(args.mode)
    config.iq_defaults["nav_data"] = "subframe"
    config.iq_defaults["duration_s"] = args.duration_s
    config.iq_defaults["wn"] = args.wn

    result = _run_scenario_with_progress(
        mode=args.mode,
        duration_s=args.duration_s,
        config=config,
        use_bit_select=False,
        progress_interval_s=args.progress_interval_s,
        chunk_duration_s=args.chunk_duration_s,
        use_preamble_sync=args.use_preamble_sync,
        force_fixed_block=args.fixed_block,
    )
    rx = result.rx
    if rx is None:
        raise RuntimeError("Scenario did not produce a receiver result")
    if not rx.pvt_fixes:
        raise RuntimeError("Scenario did not produce any PVT fixes")

    engine = result.engine
    if engine is None:
        raise RuntimeError("Scenario result did not include an engine")
    first_fix = rx.pvt_fixes[0]
    first_fix_err_m = _first_fix_truth_error(result)
    fix_residuals_us = _collect_first_fix_residuals(result, engine)

    # Reconstruct the strobe-sample-index that was used for the first fix.
    # PVT solver is strobed at the current sample count each dump boundary;
    # the first-fix gps_time gives us the strobe rx-time, from which we
    # back-compute the sample index.
    first_fix_gps = first_fix.gps_time_s
    strobe_sample_idx = int(rx.fs * (first_fix_gps - rx.start_gps_time_s))

    print(
        f"first_fix_gps={first_fix_gps:.3f} "
        f"first_fix_err_m={first_fix_err_m:.1f} "
        f"n_svs={first_fix.n_svs} gdop={first_fix.gdop:.2f} "
        f"strobe_sample={strobe_sample_idx}"
    )
    print(
        "PRN  rx_gps       true_tx_s   meas_tx_s   interp_err_us  "
        "fix_resid_us  hist_len"
    )

    max_interp_us = 0.0
    # Locate the LAST anchor event per PRN so we can check the anchor
    # instant's sv_time against ground truth.
    last_anchor = {}
    for ev in result.rx.anchor_events:
        last_anchor[ev["prn"]] = ev
    for prn, meas in sorted(rx.pseudorange_meas.items()):
        if not meas.has_anchor():
            continue
        meas_tx = meas.measure_tx_time(strobe_sample_idx)
        if meas_tx is None:
            print(f"{prn:3d}  {first_fix_gps:10.3f}  (interp out of bracket)")
            continue
        true_tx, _ = _iterated_truth(engine, prn, first_fix_gps)
        interp_err_us = (meas_tx - true_tx) * 1e6
        fix_resid_us = fix_residuals_us.get(prn, float("nan"))
        max_interp_us = max(max_interp_us, abs(interp_err_us))

        # Probe the anchor instant itself: at the rx-time corresponding
        # to anchor_abs_sample_frac, what does ground truth say sv_time is?
        ev = last_anchor.get(prn, {})
        anch_abs = ev.get("live_anchor_abs_sample_frac")
        anch_claimed_sv = ev.get("live_anchor_tow_s")
        anch_err_us = float("nan")
        if anch_abs is not None and anch_claimed_sv is not None:
            anch_rx_time = rx.start_gps_time_s + float(anch_abs) / rx.fs
            anch_true_tx, _ = _iterated_truth(engine, prn, anch_rx_time)
            anch_err_us = (anch_claimed_sv - anch_true_tx) * 1e6
        print(
            f"{prn:3d}  {first_fix_gps:10.3f}  {true_tx:10.6f}  "
            f"{meas_tx:10.6f}  {interp_err_us:+13.3f}  "
            f"{fix_resid_us:+12.2f}  {meas.history_len():8d}  "
            f"anch_err={anch_err_us:+.3f}us"
        )

    print(f"\nmax |interp_err_us| across SVs = {max_interp_us:.3f} µs")
    if os.environ.get("ANCHOR_DECOMP"):
        # cursor-timing-debug-PLAN.md Step 4: decompose the anchor error
        # per-SV across EVERY anchor event (not just the last one). If a
        # per-SV residual is constant across its anchors it points at a
        # fixed per-SV convention bug; if it drifts with sim-time it
        # points at an accumulated-rate issue (wrong t_dump_s, dropped
        # dumps, cursor-advance miscount, etc.).
        print("\n=== anchor-event residual decomposition ===")
        print(
            "sim_s     prn  sfid  tow_6s  anch_abs_sample     "
            "anch_rx_s   claimed_sv_s  true_sv_s     anch_err_us"
        )
        per_prn: Dict[int, list] = {}
        for ev in result.rx.anchor_events:
            if not ev.get("anchored"):
                continue
            prn = ev["prn"]
            anch_abs = ev.get("live_anchor_abs_sample_frac")
            claimed_sv = ev.get("live_anchor_tow_s")
            if anch_abs is None or claimed_sv is None:
                continue
            anch_rx_time = rx.start_gps_time_s + float(anch_abs) / rx.fs
            true_sv, _ = _iterated_truth(engine, prn, anch_rx_time)
            err_us = (claimed_sv - true_sv) * 1e6
            per_prn.setdefault(prn, []).append(err_us)
            print(
                f"{anch_rx_time:8.3f}  {prn:3d}  {ev['subframe_id']:4d}  "
                f"{ev['tow_6s']:6d}  {float(anch_abs):18.3f}  "
                f"{anch_rx_time:10.3f}  {claimed_sv:12.6f}  "
                f"{true_sv:12.6f}  {err_us:+11.3f}"
            )
        print("\n--- per-SV summary ---")
        print("prn  n  first_err_us  last_err_us  mean_us  std_us   drift_us/s")
        for prn in sorted(per_prn):
            errs = per_prn[prn]
            n = len(errs)
            arr = np.asarray(errs, dtype=float)
            first_err, last_err = errs[0], errs[-1]
            mean_us = float(arr.mean())
            std_us = float(arr.std())
            # Rough drift: first → last over ~6 s per subframe.
            drift = (last_err - first_err) / max(1, (n - 1)) / 6.0
            print(
                f"{prn:3d}  {n:2d}  {first_err:+12.3f}  {last_err:+11.3f}  "
                f"{mean_us:+8.3f}  {std_us:6.3f}  {drift:+10.3f}"
            )
    # SoftGNSS-style interpolation target: per-SV residual ≪ 1 µs.
    # At 4.096 MSPS the one-sample interpolation tolerance is 1/fs = 244 ns.
    if max_interp_us < 1.0:
        print("GATE PASS: per-SV band <1 µs — SoftGNSS anchor working.")
    else:
        print("GATE FAIL: per-SV band ≥1 µs — anchor regression.")

    return 0 if max_interp_us < 1.0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
