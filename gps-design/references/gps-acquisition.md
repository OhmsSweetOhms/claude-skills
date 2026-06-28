# GPS Acquisition -- PL.B2 (PCPS FFT)

**Status: Partial.** Seeded with the weak-signal cold-floor findings
(2026-06-27, see below); the "What This Chapter Will Cover" topics are still
mostly TODO. Expand further as acquisition work surfaces design / debug questions.

---

## What This Chapter Will Cover

- PCPS (Parallel Code Phase Search) FFT implementation
- Peak1/peak2 metric and acquisition threshold calibration
- Doppler bin spacing (typically 250 Hz for 1-ms coherent integration)
- Non-coherent averaging (dwell count vs sensitivity vs false-alarm rate)
- Cold-start vs warm-start search space pruning
- Handoff to PL.B3 tracking (code phase convention, Doppler handoff)
- Weak-signal Tiers 1-3 acquisition extensions for cislunar / indoor
- FPGA implementation trade-offs (4096-point FFT, BRAM layout)

## Research Already Done

| Session | Content |
|---------|---------|
| `.research/session-20260322-142449` | Leclere comparison, PCPS vs serial |
| `.research/session-20260405-160000` | PCPS params, peak detection thresholds |

## Current Implementation

`gps_receiver/blocks/pl_b2_acquisition.py` -- FFT-based PCPS
acquisition. Backing references:

- **Universal across production implementations** -- GNSS-SDR,
  PocketSDR, SoftGNSS, sturdr all use PCPS.
- **Peak1/peak2 ratio** as acquisition metric (peak ratio > threshold
  -> declare acquired).
- **Doppler search** centered on 0 Hz by default; profile-dependent
  max doppler (5 kHz open_sky, 10 kHz high_dynamic).

## Known Gaps

- **Weak-signal Tiers 1–3** are implemented per
  `docs/implementation-plan-weak-signal-tracking.md`. Remaining
  cislunar gaps: PULL_IN extended integration beyond the current
  tiers, and decision-directed squaring for sub-nav-bit coherent
  integration.
- **Warm-start ephemeris-guided search** not yet implemented (would
  accelerate reacquisition after brief loss).
- **FFT zero-padding for sub-bin Doppler resolution** not
  implemented (could improve weak-signal sensitivity).

## Findings — weak-signal cold-floor work (2026-06-27)

From `receiver/20260522-weak-signal-decode-floor` plan-02 (acquisition floor
36 → ~32 dB-Hz). Load these before any acquisition-sensitivity or threshold work.

### The float GLRT metric mean is dwell-INDEPENDENT
The receiver's float acquisition metric is `peak_power / median(power_grid)`, and
its **mean is `1 + SNR_1ms` — independent of the non-coherent dwell count.**
Non-coherent accumulation scales the signal peak AND the noise median together, so
**more dwells at a fixed threshold do not lower the floor.** The √D sensitivity
gain is entirely in the **noise-metric-tail variance** (max over 60 trials:
1.58 @80 → 1.35 @250 → 1.23 @500 dwells), which is captured only by **lowering the
threshold as D grows** — the dwell-aware `thr(D) = 1 + k/√D` (ADR-PL-B2-009). And
**`code_doppler_comp="slewed"` (ADR-PL-B2-006) is required** at high D, or the code
phase drifts across FFT bins over the long dwell window and smears the peak (D=400
"none" → 0% valid even @36). The landed open_sky operating point: 250 dwells,
threshold 2.0, slewed → ~31–32 floor at a 0.65 noise-metric margin
(findings-2026-06-27-step3g). Note this differs from the fixed-point peak1/peak2
metric, whose value scales differently — do not port the float threshold to the FP
path.

### Cross-correlation: float vs fixed-point divergence (KNOWN GAP)
The float peak/median GLRT has **no C/A cross-correlation rejection**; the
fixed-point peak1/peak2 path does (second peak in the winning Doppler row). At a
high threshold (≥4.0) cross-corr (~2–3) is below threshold and the gap is masked;
**any float-path threshold relaxation unmasks it on multi-SV captures** (real-IQ
JT23 cold-start at thr 2.0 found 5 cross-corr candidates at metric ~2.0 alongside
the real SVs, findings-2026-06-27-step3h). So "float and FP acquisition are
detection-equivalent" holds for **noise only, not cross-correlation.** Multi-SV
cold start on the float path needs a peak1/peak2 gate or must use the FP backend.
Single-SV / noise diagnosis is blind to this — validate acquisition on multi-SV.

### Detector threshold-relaxation patterns (general, from this work)
Reusable when relaxing any lock/detect threshold for weak signal:
1. **Margin + guard:** measure the signal-statistic vs noise-statistic
   distributions; relax to capture the unused margin; add a noise-only guard test.
2. **Early-eval trap:** a "lock when ratio > thr once N events seen" gate
   false-locks on noise if you relax thr without raising N (the estimate hasn't
   converged). Bit-sync needed min_events 10 → 200 *with* dominance 0.6 → 0.10.
3. **Hybrid two-gate OR:** when relaxing would regress the strong-signal path
   (latency, transition races), keep the reference gate (fast path) and add a weak
   gate; lock on either. (Used for the bit-sync histogram lock, ADR-PL-B3A-001.)

### Diagnostics (templates)
`receiver/20260522-weak-signal-decode-floor/diagnostics/`:
`probe_acq_dwell_floor.py` (float acq valid% + noise false-acq vs dwells/thr/comp),
`diagnose_cold_acq_floor.py` (per-stage cold-floor attribution),
`validate_jt23_realiq_fullcircle.py` (real-IQ multi-SV cold-start old-vs-new).

## When to Expand This Chapter

- Writing or debugging `pl_b2_acquisition.py`.
- Porting PCPS to VHDL (FPGA build Phase 4).
- Weak-signal work -- cislunar, indoor, or high-dynamic scenarios
  where sensitivity is bounded by acquisition threshold.
- Anti-jam / anti-spoof work where acquisition becomes adversarial.
