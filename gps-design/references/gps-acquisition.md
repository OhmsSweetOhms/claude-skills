# GPS Acquisition -- PL.B2 (PCPS FFT)

**Status: Active chapter.** Expanded 2026-06-09 from the plan-03 RTL
thread (`.threads/fpga/20260424-pl-b2-acquisition/`) and seeded with the
weak-signal cold-floor findings (2026-06-27, see "Findings — weak-signal
cold-floor work" below). RTL-authoring counterpart:
`~/.claude/skills/socks/references/dsp/gps-acquisition-fft.md` (SDF
architecture, BRAM discipline, TB lessons — read that when writing VHDL;
read this for the algorithm/contract side).

---

## Pipeline Summary (as landed)

PCPS, N=4096 (1 ms coherent at 4.096 MSPS), per PRN per Doppler bin:

```
12-bit pre-B1 IQ -> Q15 carrier wipeoff -> DIF FFT (bit-reversed out)
  -> pointwise x conj(code spectrum)    [bit-reversed domain, no reorder]
  -> DIT IFFT (natural out) -> |x|^2 -> power grid
peak1 = grid max; peak2 = same-Doppler-row runner-up; detect by
integer cross-multiplication: peak1 * thr_den > thr_num * peak2
```

Source contract: hardware B2 taps the **pre-B1 12-bit D5 output**
(`IF.BB.IQ16_4096MSPS.V1`); B3 tracking consumes post-B1 quantized IQ.
Shared invariant = common receiver sample-index origin, not identical IQ.

Golden: `gps_receiver/blocks/pl_b2_acquisition.py::PCPSAcquisition`.
Float behavioral path (GLRT peak/median-noise) is the receiver-level
default; `backend="fixed_point"` is the RTL vector authority,
constructed explicitly by tests/generators — never through `GPSReceiver`.

## Peak1/Peak2 Semantics (hard-won)

peak2 searches **only peak1's Doppler row**, outside a circular
±`peak_exclusion_bins` (5) code-bin exclusion — GNSS-SDR
`first_vs_second_peak_statistic`. A whole-grid peak2 picks up the
signal's own adjacent-Doppler-bin leakage at the same code index
(~sinc² of bin offset) and saturates the metric near ~2.4 at ANY C/N0,
silently destroying detection margin (visible ratio 2.12 vs 18.38 after
the fix). The all-32 sweep caught this; a single-PRN test did not.
Details: `findings-2026-06-09-allprn-coverage-and-same-row-peak2.md`.

## Fixed-Point Schedules: One Golden Per Hardware Config

The fixed-point FFT has two rounding schedules, selected by
`PCPSAcquisition(fft_schedule=...)`:

| Schedule | Arithmetic | RTL config | Vector authority |
|----------|-----------|------------|------------------|
| `"r2"` (default) | Q15 twiddle multiply on every stage's diff leg, **including W⁰ = 32767 (lossy)** | `SCHEDULE="R2_EXACT"` | `modules/pl_b2_acquisition_axi/tb/vectors/` |
| `"r22"` | radix-2² pairing: exact twiddle-free butterflies, exact ∓j rotations, ONE merged-exponent Q15 multiply per pair, exponent-0 exact passthrough | `SCHEDULE="R22_DSP"` (~half the DSPs; xc7z020 enabler) | `modules/pl_b2_acquisition_axi/tb/vectors-r22/` |

**Why two goldens:** radix-2²'s DSP saving comes from skipping multiplies
the r2 golden performs lossily — `(x*32767)>>15 ≠ x` — so the schedules
are different rounding contracts, not one engine with a flag. Detection
geometry (peak indices, detected flags, rational code-phase/seed fields)
agrees across schedules on real signal; peak powers/metrics differ in
LSBs. r22 is slightly MORE accurate (fewer truncations), not less.

Validation anchors for any new schedule: DC-path exactness (r22 only —
impossible under r2), detection-geometry equivalence on the corner PRNs,
structure match vs numpy FFT at the bit-reversed placements.

## Vector Authority Rules

- Bundles are per-PRN corner set {1, 7, 15, 32} + `summary.json`;
  regenerate ONLY at a clean gps_design SHA (`golden_sha` records
  `-dirty` otherwise — the plan-02 reproducibility trap).
- Input IQ CSVs must be **byte-identical across schedule variants**
  (same `generate_gps_iq` seed); only config/model CSVs differ. Verify
  with `diff -q`.
- `fft_roundtrip/` per-stage vectors (4 frames: wiped signal, ±1 code,
  impulse, full-scale corner; `emit_fft_stage_vectors`, 12 taps per
  direction) let the RTL gate stage-by-stage instead of end-to-end.
- Generator: `modules/pl_b2_acquisition_axi/tb/gen_pl_b2_fixed_point_vectors.py`
  (worktree) with `--fft-schedule {r2,r22}` and `--emit-fft-roundtrip`.

## Handoff To Tracking (B2 → PS → B3)

No PL-to-PL wire. The path is: B2 result IRQ → A53_1 (PS.RX policy
plane, ADR-012) AXI-Lite readback → A53_1→R5_1 seed mailbox (OCM) →
R5_1 final seed advance at its epoch boundary → B3 channel cfg write
(R5_1 stays sole writer). PS latency is absorbed by **sample-anchored
rational seed fields**, not speed: the result carries exact
`code_phase_chips_num/den` and `seed_code_phase_chips_num/den` plus
`detection_sample`/`handoff_sample`; advancing from `handoff_sample` to
the actual channel-start sample uses the Doppler-coupled code rate
(GNSS-SDR pull-in convention). The seed-advance arithmetic lives in
`_advance_code_phase_fraction`.

## Doppler Ceiling (plan-04 sizing input — do not lose)

The acquisition Doppler ceiling is layered; the weak-signal binding term
is **code-Doppler smear under fixed-bin accumulation**:
`f_d,max ≈ 770/T_soak` Hz (cislunar 9.5 s soak → ~81 Hz uncompensated).
Code-Doppler-compensated accumulation is in neither the fixed-point
backend nor the plan-03 RTL; it must be decided at the weak-signal
contract level before the DDR soak engine is sized. Full analysis:
`.threads/cross-cutting/20260502-scenario-stress-matrix-framing/findings-2026-06-09-pl-chain-stress-axes-and-doppler-ceilings.md`.

## Research Already Done

| Session | Content |
|---------|---------|
| `.research/session-20260322-142449` | Leclere comparison, PCPS vs serial |
| `.research/session-20260405-160000` | PCPS params, peak detection thresholds |
| `.research/session-20260608-191014` | Authored fixed-point FFT engine basis (R2²SDF, He & Torkelson, mixed-radix-5 for L1C) |

## Known Gaps

- **Code-Doppler-compensated accumulation** (see Doppler Ceiling above).
- **Weak-signal Tiers 1–3** per
  `docs/implementation-plan-weak-signal-tracking.md`; remaining cislunar
  gaps: PULL_IN extended integration, decision-directed squaring.
- **Warm-start ephemeris-guided search** not implemented.
- **L1C N=20480** (= 2¹²·5): the SDF pipeline keeps a radix-5 stage
  additive (`FftPlan` parameterization); not implemented.
- **FFT zero-padding for sub-bin Doppler resolution** not implemented.

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

## When to Read This Chapter

- Writing or debugging `pl_b2_acquisition.py` (either backend/schedule).
- The PL.B2 RTL hop (plan-03+): vector regeneration, gate failures,
  schedule questions.
- Weak-signal / cislunar acquisition sizing (Doppler ceiling, soak).
- B2→B3 seeding bugs (code-phase convention, rational seed advance).
