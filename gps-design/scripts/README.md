# Diagnostics

This directory used to bundle three diagnostic templates from the
2026-04-16 pseudorange-anchoring debug session. Those are archived in
`archived/` because they target the retired PS.B10a / PseudorangeMeasurement
architecture (pre plan-02 Step 3, 2026-04-23).

**Live, working diagnostics for the current architecture live under
the project's own thread directories.** The gps-design skill does not
try to maintain a parallel copy — the project is the source of truth.

## Where to look for current diagnostics

Path pattern:

```
gps_receiver/threads/<subsystem>/<slug>/diagnostics/
```

Highest-value examples as of 2026-04-24:

- `receiver/20260419-arch1-migration/diagnostics/`
  - `diagnose_tow_label_timing.py` — plan-03 Step D1. Per-labeled-packet
    three-way comparison (claimed `tow_s` vs scenario-truth vs
    residual-stripped). Run against a `gen_baseline_lnav.py` summary
    JSON.
  - `attribute_step6_observables_residual.py` — plan-02 Step 6
    residual attribution. Audits PS.B13 TOW labels vs
    `tx_time_offset_profiles`, recomputes saved PVT pseudoranges,
    reports between-PRN intrinsic std.
- `receiver/20260421-gnss-sdr-comparative-pipeline/diagnostics/`
  - `gen_baseline_lnav.py` — canonical cache-hit PVT measurement
    driver. Reads `.iq16` with sha256 verification, drives the
    receiver, emits a summary JSON with per-channel history,
    pvt_attempts, anchor_events, etc.
- `receiver/20260423-sv-time-at-boundary-layer-b/diagnostics/`
  - `attribute_sv_time_boundary_terms.py` — per-algebra-term
    instrumentation (from a now-closed thread; kept as a
    characterization tool).

## When you need to write a new diagnostic

1. Open a thread under `gps_receiver/threads/<subsystem>/<slug>/`
   via the `threads` skill if one doesn't already exist for your
   investigation.
2. Put the script under that thread's `diagnostics/`.
3. Follow the three-way methodology (claimed / iqgen-truth /
   oracle-truth) — see `../references/pseudorange-anchoring.md` §3.
4. If the script produces a durable contract (e.g., an argparse
   surface that survives the investigation), register it in the
   thread's `thread.json` `diagnostics[]` entry with a `purpose` and
   `first_run_result` so future sessions find it.

## What the three-way pattern looks like in practice

The live scripts in `receiver/20260419-arch1-migration/diagnostics/`
are good templates. They mostly share a shape:

- Consume a pre-existing `gen_baseline_lnav.py` summary JSON (cache
  hit, fast) rather than re-running the receiver.
- Reconstruct `iqgen_truth` from `scenario_engine` / `gps_scenario`
  primitives (same ones the IQ generator used).
- Reconstruct `oracle_truth` from `propagate_sv` + Sagnac +
  light-time iteration.
- Tabulate per-PRN `claimed` vs `iqgen_truth` vs `oracle_truth`
  residuals in microseconds and equivalent meters.
- Emit a verdict classifier at the end (H1/H2/H3/H4 style) plus a
  JSON report under `temp/` or `.claude/workspace/`.

Write yours the same way if you want the output to read like the
existing findings.
