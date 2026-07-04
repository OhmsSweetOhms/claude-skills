# GPS L1C — Generation, Reception, and the Dual-Rate Joint Chain

L1C is the GPS III modernized civil signal on L1 (IS-GPS-800). This
project generates and receives it ALONGSIDE L1 C/A on one joint
baseband stream at the native RX rate (20.48 MSPS, ADR-007), with the
C/A chain living at 4.096 MSPS. This chapter is the map of what
exists, the conventions that are easy to get wrong, and the hard-won
bring-up/measurement lessons. Live status is NOT here — query
`.threads/threads.json`; the owning threads are
`gps_iq_gen/20260430-l1c-generator` + `receiver/20260430-l1c-phase-a`
(Phase A, closed) and `cross-cutting/20260701-l1c-scenario-integration`
(TV-profile lift + dual-rate integration + engine scenario) with its
child `receiver/20260703-l1c-code-seed-rootcause` (gate-evaluator fix).
The whole arc landed on main at merge commit `99752c7b` (2026-07-04).

**Visual companion:** [`gps-l1c-system-diagram.html`](gps-l1c-system-diagram.html)
— a scenario-driven, system-level block diagram (open in a browser):
§1 the scenario→IQ→dual-rate→chains→Δρ/PVT context, §2 the three
interlocked loops around one correlator (DLL/PLL/FLL + carrier aiding +
state-machine control — the "how are these connected" view), §3 the
C/A→L1C seeding/cross-coupling table, §4 the state machine as the
loop-parameter scheduler. Hand-authored teaching view; block IDs are
canonical, tuning numbers are open_sky/ADR-023 as of 2026-07 (verify
against the JSON stack before quoting).

## 1. Signal structure as implemented

| Component | Modulation | Amplitude | Data/overlay | Where |
|-----------|-----------|-----------|--------------|-------|
| L1C-D (data) | BOC(1,1) | 0.5 (real axis) | 100 sps; CNAV-2 encoding DEFERRED (Phase B) — synthesis takes `l1cd_data_bits` or none | `gps_iq_gen/l1c_modulation.py::sample_l1cd_envelope` |
| L1C-P (pilot) | TMBOC(6,1,4/33) — BOC(6,1) in slots {0,4,6,29} of each 33-chip group, BOC(1,1) elsewhere | √0.75 (imaginary axis) | L1C-O overlay wipe | `sample_l1cp_envelope` |
| L1C-O (overlay) | 1800-chip LFSR @ 100 Hz on the pilot | — | one chip per 10 ms code epoch | `gps_iq_gen/l1c_overlay.py::gen_l1co` |

- Primary codes: Weil/Legendre, **10230 chips @ 1.023 Mcps = 10 ms
  period** (10× the C/A epoch). Single port for the whole project:
  `gps_iq_gen/l1c_codes.py::gen_l1cp/gen_l1cd` (PRN 1–63; receiver
  replicas import from here). Pinned bit-exact against IS-GPS-800J
  Table 3.2-2 octal vectors (`gps_iq_gen/test_l1c_codes.py`).
- Composers: `compose_l1c_signal` (L1C only, unit power) and
  `compose_l1_joint` (C/A + L1C-D + L1C-P on one carrier). Amplitude
  is applied OUTSIDE the composer at the `gps_iq_gen.py` call sites.
- IS-GPS-800J extracts + design research:
  `.research/session-20260430-135524/`.

## 2. Generator conventions (the C/A path is the semantics authority)

The `iq_gen.signals` array (`["L1CA","L1CD","L1CP"]`, back-compat
default `["L1CA"]`) + `sample_rate_hz` select the joint stream. Every
time-varying per-SV profile is consumed through the SAME semantics as
C/A — when extending, read `synthesize_satellite_tv` +
`_compute_tv_code_phase_chips_inplace` FIRST and match exactly:

- **Time base:** everything interpolates at absolute receiver time
  `t_rx = start_time_s + n/fs`.
- **Primary code phase:** nominal chip advance is analytic in
  ABSOLUTE time (`t_rx·chip_rate`), plus trapezoid-integrated Doppler
  delta from 0→start, plus per-sample cumsum. The absolute-time
  nominal term is the STREAMING CONTINUITY mechanism — a
  chunk-relative term restarts the L1C code at every chunk boundary
  that isn't a 10 ms multiple (latent Phase A bug: invisible at
  100 ms chunks, 66% corruption at 1 ms chunks).
- **`tx_time_offset_profiles`** never moves code phase directly — it
  indexes DATA/OVERLAY epochs by transmit time
  (`floor((sim_start_offset_s + t_rx + tx_offset(t_rx))·rate)`),
  mirroring `expand_superframe_dynamic` for C/A nav bits. Code-phase
  motion arrives via the engine's light-time-consistent Doppler
  (`F_L1·d(tx_offset)/dt`, see `gps_scenario._collect_engine_epochs`).
  The TMBOC subcarrier is chip-locked to the primary phase and
  follows it automatically; the overlay is NOT chip-locked — it is
  tx-time-indexed (`_tx_time_epoch_index`).
- **`code_phase_offset_profiles`**: additive chips AFTER Doppler
  integration; does not move overlay/data timing (the engine adjusts
  the tx-offset side itself when stacking residuals).
- **`cn0_profiles`**: per-sample amplitude, C/A op chain verbatim.
- **Backend posture:** TV-profile L1C is `numpy_legacy` only;
  `numpy_samplemajor` raises `SAMPLEMAJOR_L1C_TV_UNSUPPORTED_MESSAGE`
  (the kernel accumulates C/A in place and would silently skip L1C).
- **Byte-parity discipline:** `signals=["L1CA"]` output is pinned
  bit-exact by golden tests. Anything L1C-conditional must leave it
  untouched — verify with a sha256 probe against pre-change HEAD, not
  just the test suite.
- Tests: `gps_iq_gen/test_l1c_profiles.py` (TV semantics, streaming
  parity, samplemajor guards), `test_l1c_codes.py` (ICD vectors).
  Known documented tolerance: on integrated-Doppler RAMPS, chunked vs
  in-RAM streaming parity is ±1 LSB — a property of the C/A authority
  itself (trapezoid re-entry vs cumsum), not of the L1C lift.

## 3. Receiver chain (Phase A architecture)

Separate L1C block family at 20.48 MSPS — NOT parameterized variants
of the C/A blocks (future RTL is a distinct LANE_COUNT=2
instantiation):

| Block | Class | File | Notes |
|-------|-------|------|-------|
| PL.B2_L1C | `PCPSAcquisitionL1C` | `gps_receiver/blocks/pl_b2_l1c.py` | C/A-AIDED mode (`center_doppler_hz`, ±500 Hz @ 50 Hz). Standalone 500 Hz-step search loses ~19 dB at 250 Hz off-bin with the 10 ms coherent FFT (F1 scalloping defect — always aid). `overlay_search` defaults to hypothesis 0 ONLY — correct at stream start, wrong mid-stream. |
| PL.B3_L1C | `L1CCorrelator` | `gps_receiver/blocks/pl_b3_l1c.py` | Dual-lane pilot(TMBOC+overlay-wipe)/data(BOC11) E/P/L; overlay index self-increments per 10 ms epoch. |
| PS.B4_L1C–B9_L1C | via `L1CTrackingState` | `gps_receiver/receiver.py` | FGI-pattern 3-state machine (COARSE → PULL_IN → FINE_TRACKING); pilot-based `pilot_atan2` PLL. FLL pull-in range ≈ ±25 Hz — seed the carrier accordingly (see §4). |
| PS.B13_L1C | `ObservablesL1C` | `gps_receiver/blocks/ps_b13_l1c.py` | Thin `Observables` subclass, fs=20.48e6, 10 ms packet cadence. Inherits the `(sample_counter − residual)/fs` t_rx formula — minus sign load-bearing. |

**Carrier NCO pitfall (bit us once, cost a day):** the loop filter
output is a TOTAL correction; the NCO is `base + adj`, never
`current + adj` — the incremental form is the documented type-IV
divergence (symptom: COARSE↔PULL_IN oscillation, PLI stuck ~0.5).
See `control-loops` skill + `receiver/20260430-l1c-phase-a`
findings (fix commit `f81244b8` on the merged branch).

**State machine + loop operating point at the 10 ms epoch (plan-03
campaign, landed on main `99752c7b`):**

- **PULL_IN exits on FLL lock ALONE.** The FGI port fix: a PLL-gated
  exit from PULL_IN is a deadlock — the PLL is still open in that state,
  so its lock bit never sets and the machine never advances. Gate the
  COARSE→PULL_IN→FINE transitions on the FLL indicator during pull-in;
  hand to the PLL only in FINE.
- **B·T portability.** C/A-heritage loop constants do NOT port by epoch:
  a 50 Hz FLL at T=1 ms (B·T=0.05) becomes B·T=0.5 at the L1C 10 ms
  epoch and rings into the cross-dot alias grid (measured +101/−100 Hz
  STABLE false locks — the cross-dot unambiguous range is ±1/(2T)=±50 Hz
  with aliases on the ±1/T grid). The bw_schedule was retuned for T=10 ms.
- **±40 Hz FLL frequency-error clip** (F2) rejects those alias captures
  inside the ambiguity; it is profile-wired (`receiver-block-profiles.json`)
  and implements the former CLAUDE.md "frequency error clipping" Deferred
  Gap for L1C. The **fll_lock_indicator is now bounded to [0,1]** (was
  measured 3.27, unbounded).
- **E-L spacing is FIXED 0.04 chip in all states** (FGI-oracle aligned).
  The q-04 per-state spacing SCHEDULE was tried and reverted: the
  normalized envelope DLL discriminator `(|E|-|L|)/(|E|+|L|)` is only
  weakly spacing-dependent (measured 0.76× over 0.04→0.08, NOT 1/spacing),
  and the real-code TMBOC-P S-curve makes non-0.04 values unsound —
  d=0.5 is an unstable origin (D′(0)=+2.09), d=0.12 an ACF-shoulder dead
  zone (D′(0)=−0.16), only d=0.04 gives a stable capture region (±0.336
  chip, 8.4× the measured acquisition seed error). Diagnostic:
  `diagnostics/tmboc_scurve_real_code.py`.

See `findings-2026-07-02-plan03-oscillation-root-cause.md` (loop) and
`receiver/20260703-l1c-code-seed-rootcause/findings-2026-07-03-code-seed-refuted-evaluator-artifact.md`
(spacing S-curve geometry).

## 4. L1C bring-up recipe (mid-stream / moving scenarios)

Cold-starting an L1C chain mid-stream or under dynamics has FOUR
coupled requirements, each measured to fail alone
(`cross-cutting/20260701-l1c-scenario-integration`
findings-2026-07-01, §debug trail):

1. **Carrier seed from C/A TRACKED Doppler**, not from acquisition
   grids. The C/A acq grid (500 Hz) + L1C aided grid (50 Hz) can hand
   the FLL an error beyond its ±25 Hz pull-in (measured 73.5 Hz →
   permanent COARSE/PULL_IN cycling). C/A tracking is sub-Hz within
   ~2 s.
2. **Overlay seed from the C/A TOW anchor:**
   `overlay_index = round(tow_ca(t_start)/10ms) mod 1800`. The
   acquisition's default overlay hypothesis 0 is wrong everywhere
   except stream start.
3. **Code-epoch-aligned dumps.** Fixed 10 ms blocks on the RECEIVE
   grid straddle two overlay chips whenever the tx-time offset ≢ 0
   mod 10 ms, corrupting the pilot wipeoff. Until the L1C cursor path
   exists (see §6), align the block grid to the signal's epoch
   boundary computed from the acquired code phase.
4. **The SIGNAL's own code/overlay synchronization must hold** (the
   requirement the plan-03 campaign added). IS-GPS-800: the L1C-O
   overlay chips are synchronous with the primary-code 10 ms epochs;
   the composer now guarantees it. If the generator lets overlay and
   primary drift, no receiver-side seeding recovers it — verify the
   signal before chasing the receiver.

**Straddle law** (why requirement 3 bites): when a dump window straddles
an overlay-chip boundary by fraction f, the wiped pilot amplitude is
**1−2f**, so it drops to 0 at f=0.5 and **SIGN-FLIPS past f=0.5**
(a π phase flip the PLL reads as a cycle slip). This is the exact
mechanism behind the ±1-overlay-chip oscillation. **Overlay-seed knife
edge:** never evaluate `floor`/`round` of the tx-time AT an epoch
boundary to pick the overlay index — a boundary sample lands the seed
one chip off (an off-by-one that reproduces the same π-flip sequence);
compute it at mid-dump, not at the boundary.

**Overlay d-match diagnostic** (an exact root-causer, scripts in the
plan-03 inbox `codex-handoff/scenario-plan-03/scripts/lab_l1c_overlay_proof*.py`):
correlate the observed per-dump π-flip sequence against `gen_l1co` chip
products for a range of wipe offsets d; the true offset scores an exact
sequence match (measured 1.000 at d=−1 for the off-by-one, d=0 for the
clean seed). Use it to separate an overlay-seed defect from a carrier
false-lock — they look identical in the prompt I/Q alone.

**Phase-A LNAV time anchoring, general form** (L1C has no CNAV-2
decode yet — the integer epoch is stolen from C/A, sub-epoch is
purely L1C-measured):

```
sub  = (tracker_code_phase_chips mod 10230) / 1.023e6
k    = round((tow_ca(t_rx_dump) − sub) / 10 ms)
label = k·10 ms + sub
```

The Phase-A shortcut `round(tow_ca·100)·10` is a special case valid
ONLY when tx_offset ≡ 0 mod 10 ms; under a real propagation delay it
mislabels every packet by a constant (measured −4.0 ms·c at
tx0 = −3.9 ms).

## 5. Δρ (L1C − C/A) measurement methodology and its floors

Harness: `diagnostics/diagnose_l1c_pseudorange_diff.py` (`--long`,
`--moving-profile`, and the dual-rate mode per ADR-023). Both chains
replay ONE file; Δρ at common 100 ms strobes; judge the settled MEAN,
attribute the std — never tune it.

Known measurement floors (all quantified, none signal properties):

- **±0.5-sample label quantization**: any chain whose dumps are not
  code-aligned quantizes t_rx to the sample grid (one 20.48 MSPS
  sample = 14.64 m; one 4.096 MSPS sample = 73.19 m).
- **Fixed-block C/A driving is RATE-BLIND under motion**: TOW labels
  advance one code period per processed block while packet t_rx stays
  on the block grid → ρ̇ collapses to 0 (error slope = exactly
  −range-rate). Always drive C/A via the cursor path
  (`process_epoch_from_cursor`); it reads truth at ~−0.8 m mean /
  σ 1.5 m.
- **Block-aligned L1C labels** carry a fixed sub-sample bias
  (measured −6.1 m, identical at 0 and 300 m/s) plus strobe
  extrapolation scatter (σ ~13 m from 10 ms-packet rate noise
  extrapolated ≤100 ms).
- Reference numbers: Phase A same-clock both-block-aligned run:
  mean **+0.013 m** (quantization common-mode cancels); mixed
  cursor-C/A + block-L1C: mean −6.14 m. Generator-side C/A↔L1C
  incoherence is bounded at the ~cm level. Engine-driven end-to-end
  60 s gate (post evaluator fix, landed `99752c7b`): all four
  brought-up PRNs **+0.567 / −0.871 / −0.480 / −0.735 m**, slopes
  ≤ 0.11 m/s, FINE_TRACKING held.

**Two traps that make Δρ LIE (both root-caused in
`receiver/20260703-l1c-code-seed-rootcause`, landed `99752c7b`):**

- **Δρ-degeneracy trap.** If the L1C observable labels are CLONED from
  the C/A TOW interp (e.g. an `interp_tow_at` copy) instead of built
  from the Phase-A anchor (§4), Δρ ≡ 0 **by construction** and the gate
  reads a perfect pass that means nothing. L1C labels must carry an
  independent L1C-measured sub-epoch; a suspiciously exact-zero Δρ row
  is this bug, not success.
- **Live-strobe rule — never strobe Δρ POST-HOC.** `_interp_tow_debug`
  rate-extrapolates from the newest TOW label pair with a FORWARD
  staleness guard only; a strobe queried after the run, against a store
  that retains ~15 s, is silently BACK-extrapolated from the final
  ~20 ms label pair, producing a linear Δρ ramp that vanishes exactly at
  end-of-file. This manufactured the entire plan-03 F6 "FAIL"
  (post-hoc −7.3/−54.9/+10.6/−1.4 m vs live +0.9/−3.8/+0.0/−1.4 m on the
  SAME strobes). Fixes now in the tree: **D1** a
  `MAX_BACKWARD_EXTRAPOLATION_S = 1.0` guard in `ps_b13_observables`
  (drop_reason `backward_extrapolation`), and **D2** the durable
  live-strobe evaluator `diagnostics/verify_engine_delta_rho_live.py`
  (strobe at now−0.3 s during the run; settled-mean + slope verdict).
  Collect strobes LIVE, on the results contract, at the epoch they
  belong to — post-hoc re-query of a rate-extrapolating store is the
  anti-pattern.

Zero-ISC caveat: the generator synthesizes NO inter-signal bias, so
ISC_L1CD/ISC_L1CP is unobservable synthetically; Phase B (CNAV-2 +
ISC handling) stays deferred until a real-SV capture. Note ADR-023's
group-delay constant IS an inter-signal bias of exactly this shape —
budget it in any future ISC accounting.

## 6. Dual-rate joint chain (ADR-023)

Decided architecture for engine scenarios (one 20.48 MSPS joint
stream in): **L1C passthrough at native rate; C/A through a Python
PL.DECIMATOR /5 golden to 4.096 MSPS**, mirroring the ADR-007
hardware ladder. Non-negotiables:

- Coefficients from the single design authority
  (`.threads/fpga/20260424-pl-decimator/pl-fir-coeffs-f_decint_5.csv`:
  F_DECINT_5, 301 taps, Kaiser β 5.653) — never re-derive.
- The FIR group delay (**150 input samples = 30 output samples =
  7.3242 µs ≈ 2195.7 m**) is compensated in PS.B13's C/A t_rx
  labeling as a NAMED constant (subtracted — the decimated counter is
  late relative to the joint clock), never absorbed into a
  sample-counter offset. A wrong/missing compensation shows in Δρ as
  a fixed ~73 m·k offset and cannot hide — that is the acceptance
  gate (re-run the §5 experiment THROUGH the decimator, mean ≈ 0).
- Cursor-path L1C correlation (code-aligned variable reads mirroring
  `pl_b3_correlator.py::process_one_code`) removes the §5 block-label
  floors by construction.
- The fs-parameterize-the-orchestrator alternative was REJECTED — see
  ADR-023 (`docs/decision-log.md`) for why; don't reopen it casually.

**Bring-up ownership (ADR-024).** The §4 cold-start recipe is not
scenario-tooling glue — it is **GPSReceiver policy**: C/A-aided,
detection-gated, per-PRN (mirrors ADR-012's PS.RX plane; future firmware
home is A53_1). Capability is discovered by the aided-acq detection gate,
so C/A-only SVs degrade gracefully by construction; per-SV generator
signal masks are explicitly deferred. Profile knobs live under the
receiver profile, not in `gps_scenario.py` (the naive scenario-path seeds
it replaced are gone). See ADR-024 (`docs/decision-log.md`).

The whole arc (TV-profile lift → dual-rate → engine scenario →
gate-evaluator fix) LANDED on main at merge commit `99752c7b`
(2026-07-04). Implementation state and the closing measurements live in
`.threads/cross-cutting/20260701-l1c-scenario-integration/` and its child
`.threads/receiver/20260703-l1c-code-seed-rootcause/` — read their
handoff.md/findings, not this file, for the point-in-time picture.

## 7. Cross-references

- Threads: `gps_iq_gen/20260430-l1c-generator`,
  `receiver/20260430-l1c-phase-a` (closed; merged `1e35c3f9`),
  `cross-cutting/20260701-l1c-scenario-integration` (arc owner) +
  its child `receiver/20260703-l1c-code-seed-rootcause` (gate-evaluator
  root-cause + fix). Whole arc merged to main at `99752c7b`.
- ADRs: **ADR-023** (dual-rate), **ADR-024** (L1C cold-start aided
  acquisition is GPSReceiver policy), ADR-007 (substrate rates; L1C at
  native 20.48 MSPS RX).
- Findings with the measurement details:
  `receiver/20260430-l1c-phase-a/findings-2026-07-01-delta-rho-closure.md`,
  `cross-cutting/20260701-l1c-scenario-integration/findings-2026-07-01-plan01-tv-profile-lift.md`;
  the evaluator-artifact root-cause + gate PASS:
  `receiver/20260703-l1c-code-seed-rootcause/findings-2026-07-03-code-seed-refuted-evaluator-artifact.md`
  and `.../findings-2026-07-03-plan02-gate-pass.md`.
- Research: `.research/session-20260430-135524` (IS-GPS-800J).
- Related chapters: `gps-tracking.md` (loop math),
  `pseudorange-anchoring.md` (t_rx/TOW conventions the L1C anchor
  extends), `gps-acquisition.md` (PCPS semantics the aided L1C search
  inherits).
