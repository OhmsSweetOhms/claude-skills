# Archived Diagnostics

These three scripts were written against the project's pre-plan-02-Step-3
architecture (before 2026-04-23, commit `f3e08df`). They reference
APIs and modules that have been deleted:

- `PseudorangeMeasurement.set_anchor_point` / `.measure_tx_time` —
  class deleted; pseudorange formation moved to `Observables`
  (`gps_receiver/blocks/ps_b13_observables.py`).
- `ps_b10a_preamble_sync.py` — module deleted; preamble sync
  consolidated into `ps_b_telemetry_decoder.py` (PS.TLM).
- `GPSReceiver(use_preamble_sync=..., align_nav_bit_boundary=...,
  pseudorange_source=...)` — kwargs removed in plan-02 Step 4
  (commit `cb90f01`).
- `run_receiver_on_iq(..., use_preamble_sync=True)` — kwarg removed.

The scripts **will not run** against the current code. They are kept
here because:

1. The **three-way diagnostic methodology** they embody (claimed /
   iqgen-truth / oracle-truth) is still the right pattern. See
   `../../references/pseudorange-anchoring.md` §3 for the current
   writeup.
2. The _iterated_truth physics (light-time iteration + Sagnac +
   SV clock bias) in `diagnose_anchor_truth.py:_iterated_truth` is
   still correct and reusable.

## For live, working diagnostics using the current architecture

Look under the project's own thread diagnostics:

- `gps_receiver/threads/receiver/20260419-arch1-migration/diagnostics/`
  - `diagnose_tow_label_timing.py` — plan-03 Step D1 three-way TOW
    comparison against `tx_time_offset_profiles`.
  - `attribute_step6_observables_residual.py` — plan-02 Step 6
    residual attribution against scenario truth.
- `gps_receiver/threads/receiver/20260421-gnss-sdr-comparative-pipeline/diagnostics/`
  - `gen_baseline_lnav.py` — canonical cache-hit PVT measurement
    driver.
  - `pseudorange_ground_truth_audit.py` (if present).
- `gps_receiver/threads/receiver/20260423-sv-time-at-boundary-layer-b/diagnostics/`
  - `attribute_sv_time_boundary_terms.py` — per-term algebra
    instrumentation (closed-thread characterization tool).

These are built against the current `Observables` /
`TelemetryDecoder` / cursor-path APIs and are good templates for
adding a new diagnostic.

## Do not re-run these archived scripts

Any use as "quick diagnostic templates" requires first porting their
`use_preamble_sync=True` / `measure_tx_time` / `set_anchor_point`
call sites to the current API. At that point you're effectively
writing a new diagnostic — do that under the relevant project thread,
not here.
