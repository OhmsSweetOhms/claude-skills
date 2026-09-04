# Iridium downlink — hard-won facts

The Iridium signal-of-opportunity path: the on-air downlink structure, the
reference decoder used as an acceptance oracle, our own burst detector and
`PS.B<n>ir` receive chain, and the constellation/TLE work behind emitter
identity. Everything below is a MEASURED fact; the dates are preserved as
written. Live thread state lives in `.threads/`, not here, and the durable
decision record is the ADR stores (`docs/decision-log.md`, the
`ADR-PS-B3IR-*` per-block store).

Moved verbatim from the project `CLAUDE.md` (2026-09-04). Original
section opening:

> Hard-won and re-derivation-costly. Live thread state lives in
> `.threads/receiver/*iridium-receiver-golden/`, **not here**.

## Signal, band split, and what is in the clear

- **No rate from the 163.84 MSPS rail can ever drive gr-iridium.** Its
  flowgraph needs `sample_rate/decimation` to be an integer multiple of
  `25000 × samples_per_symbol` (250 kHz at the default sps=10); the rail is
  2¹⁸ × 5⁴ and that requires 5⁶, which decimation cannot supply. A host-side
  **125/128 resample to 20.000 MSPS** is permanently mandatory to use that
  tool. Consequence for our own receiver: **do not inherit the constraint** —
  channelize at the native rail rate.
- **Nothing in the Iridium broadcast path is encrypted.** De-interleaving is
  a bit re-ordering, BCH/Reed-Solomon are error correction, and voice needs an
  AMBE *vocoder* (a codec, not a key). Only the Satelles **STL** layer riding
  on top — spreading/ranging code, per-second spot-beam keys, pseudorange
  formation — is closed, and STL ranging is **not publicly reproducible**.
- **Band split, verified on-air with zero contradicting frames** across
  22,143 bursts: **IRA / ITL / IMS are simplex, above ~1626.104 MHz**;
  **IBC / LCW are duplex, below 1625.979 MHz**. IBC carries the `iri_time`
  any non-STL ranging would need, so a simplex-only capture forfeits ranging.
- **Emitter identity and satellite position are broadcast in the clear.** IRA
  carries a 7-bit satellite ID, a 6-bit spot-beam ID, and SV ECEF position at
  4 km/LSB. The ID is an **Iridium-internal 0–127 index, NOT a NORAD
  number**; `iridium-toolkit`'s `itlmap` maps it to (plane, slot), which is
  what a TLE can be matched against.
- **`PS.B3ir`'s front end is frequency-first since 2026-08-30** (plans
  17–19, `ADR-PS-B3IR-001…008`): a τ-guarded 28-symbol known-prefix
  correlation on the wide signal picks two centre candidates, the ruled
  select is applied at the winning centre, and EVERY timing decision runs
  on the ±20.833 kHz selected signal — the old defect class (a neighbour's
  tail in the ~90 kHz coarse view taking the anchor; burst 59 −2 028.8 Hz)
  is closed by construction (now +0.56 Hz). The detector's tag is a 1.25 kHz
  bin that is often a payload line — never centre a filter on it; on
  DE-QPSK only the known-prefix match identifies the carrier (payload forms
  persistent lines and ridges). Abutting ≠ colliding (overlap ≥ one symbol,
  40 µs, is the census criterion). On the sky 10–12 % of records carry a
  second carrier.

## Reference decoder and tooling

- **GNU Radio tooling needs NumPy 1.x here.** The Ubuntu GNU Radio packages
  are built against the 1.x ABI while user-site carries NumPy 2.x; run those
  tools with `PYTHONNOUSERSITE=1` (per-process, nothing global). Pure-Python
  deps that must come along get symlinked into a scratch path rather than
  installed.
- **The reference decoder (gr-iridium + iridium-toolkit) is a usable
  acceptance oracle for synthetic Iridium tapes — with three traps
  (measured 2026-08-26).** It is blind for its first 512 FFTs = **0.419 s**
  (a tape shorter than ~1 s scores zero at any C/N0); its detection floor on
  our tapes sits **between 45 and 55 dB-Hz** (0/93 at 45, 99/101 at 55), so
  the contract's 35/45 dB-Hz strata can only be judged by our own detector;
  and `--file-info` must NEVER be passed to `iridium-extractor` (absolute
  timestamps make the parser reject every line). A tier-(b) DE-QPSK tape from
  `iridium_iq_gen/` passes it: 95.7 % detection, 1.2 Hz median carrier error,
  0 ID mismatches of 382 IRA/IBC lines.
- **gr-iridium's time base is 439.07 ms behind the golden's** (its
  512-block priming); a median-of-nearest alignment reads ~0 and matches
  9.5 % — align by histogram peak under a ±3 kHz gate (92.7 %).
- **The single-process golden is O(n²) in `merge_events`** — a 774 s tape
  needs the sharded driver (64 shards, 2 s pad, equivalence self-tested);
  the fix is a time-ordered break, owed.

## Detection, acquisition, and operating points

- **Unique-word-aided acquisition, four things measured not assumed
  (2026-08-27, all in the golden's code):** the acquisition noise reference
  must come from the pre-trigger look-back — a search-surface median gives
  ZERO separation because it saturates at the template's own
  peak-to-sidelobe ratio; the preamble/burst-length classifiers must
  subtract the noise floor; the 4th-power bridge must be SKIPPED when the
  data-aided prior already sits inside the next stage's pull-in (running it
  anyway put one burst 49 Hz out and alone failed a bar); the burst's
  absolute carrier phase must be removed before any quadrant decision —
  DE-QPSK is rotation-invariant as a code but the soft-symbol and quadrant
  paths assume ±45° (found by its C/N0-independence: failures at 75 dB-Hz
  with symbol angles of exactly 0°/180°). The 28-symbol `preamble + UW`
  template beats the UW alone by 3.7 dB.
- **A detection P_d is conditional on the scorer's association rule** —
  `score_fx1` joins at ±20 kHz and ±`burst_len/2 + 2.56 ms`, NOT the
  detector's own |Δt| p90 of 1.57 ms; on a 45 dB-Hz stratum P_d is 0.975 /
  0.917 / 0.900 at 20 / 10 / 5 kHz (window-independent at 55+). State the
  windows beside every P_d.
- **Never select a detector operating point on a noise draw shorter than
  ~10× the budget's inverse rate** (2026-08-28). The Iridium detector's
  3.85 dB threshold was frozen on ONE 10 s noise tape (1 event = "0.1 /s");
  3,297 s reads 0.33 /s there. And a proxy budget is retired the day the
  thing it proxies for becomes measurable: the 0.1 /s figure constrained
  nothing (0 drops, 87–95 % acquire, noise 4e-5 of on-air declarations), so
  the point is now selected on knee + ring-miss (3.75 dB; lane decisions
  22–28). Report P_fa with a Garwood interval and its exposure, always.
- **The Q15 twiddle-truncation class (decision 96) costs a magnitude-only
  detector nothing** (+0.0000 P_d, exactly zero on noise, measured at
  N = 16384): with no replica product the bias pattern B(f) normalises out
  through the per-bin floor. It is a correlation-engine artefact, not an
  FFT artefact.
- **A burst detector's declared duration is the statistic's above-threshold
  EXTENT, not the burst's length** (2026-08-29, lane decision 31): at
  45 dB-Hz half the real Iridium bursts declare below the 8.28 ms minimum
  and the noise durations sit inside that distribution — no floor
  separates them. Never gate on a detector's extent without a
  two-population census first; the same applies to any GPS declare width.
- **On-air Iridium level (2026-08-27 tape):** ID-bearing bursts median
  56.7 dB-Hz (4 of 8,668 below 45), anonymous traffic median 47.2; the
  golden's ID-less count (331 /s, 2.6× the reference decoder) is proven
  one-way only — never quote it as firm without a two-way census.
- **45 dB-Hz is a DECODE WALL for Iridium bursts** — Es/N0 ≈ 1 dB at
  25 ksps → ~20 % differential BER, and BCH(31,21) corrects one bit in 32
  (0/38 frames decoded; mean UW bit errors 5.7 of 24), and a **duplex
  traffic burst's 28-symbol preamble caps its data-aided carrier prior at
  83 Hz** against a 15 Hz pull-in. Both floors are the air interface's.
  Receiver bars gate at ≥ 55 dB-Hz and report 45 (lane decision 15); the
  strata are not final until the on-air C/N0 distribution per burst class
  is measured (the operator's rider).

## Timing, clock, and reference offset

- **Iridium's only clear-text clock is IBC block 1 type 1's 32-bit
  `iri_time` (90 ms ticks) — a third of IBC frames; the other block-1
  variants are frozen constants, not clocks** (2026-08-28). Against GPS
  time it ticks at 0.08999999899 s (−0.011 ppm) once the 25.14 ms BC-slot
  step and geometry are removed (9 µs residual); the shipped
  `coarse_gps_time_s` is ± 2.14 ms (unremoved propagation). Its constants
  are an ANCHOR with a 3σ tripwire, never an extrapolated epoch (1 ppb of
  tick = 48 ms). The board's reference offset read from the Iridium side is
  −21.6712 ± 0.0008 ppm — the GPS engine's 21.66 to 0.011 ppm; `t_rx` runs
  slow.
  **It is also the "±9 s clock" the L1C overlay sync needs:** the 1800-chip
  L1C-O fixes GPS time only mod 18 s (research `session-20260825-l1c-future-
  wave-review`); one IBC frame + one L1C SV = absolute GPS time with no nav
  decode, no RTC (ADR-016 third amendment makes it legal; F9P/NTP were bench
  calibration only).
- **The board's free-running reference (+21.66 ppm, decision 60) EXCEEDS
  Iridium's channel half-step** — +35.1 kHz at 1621.2 MHz vs ±20.83 kHz —
  so any raster snap done on the raw carrier lands one channel low and the
  residue masquerades as a small negative bias (plan-08's "−4.05 ppm" was
  −6,513 + 41,667 = 21.68 ppm, a 0.1 % match to the GPS figure). De-bias
  the carrier by the per-boot ppm BEFORE snapping (lane decision 17,
  `ref_offset_ppm`); Iridium and GPS see the same clock cross-band. GPSDO
  stays struck (decision 35).

## Constellation, TLEs, and tracking

- **Iridium (plane, slot) is derivable from public TLEs — no current table
  exists.** RAAN clustering + an 11-station lattice fit at a COMMON epoch
  (32.7° pitch, 0.27° RMS) recovers the six planes (the ~22° Walker-star seam
  falls out between planes 6 and 1) and 10/11 slots on the planes the
  on-air `itlmap` pairs can anchor (P5S03 = IRIDIUM 159 / NORAD 43578;
  P6S02 = IRIDIUM 112 / NORAD 41925 — both must be PASSED INTO the lattice
  fit: a draft that anchored only plane 5 placed 41925 at slot 5 while
  calling it P6S02; the authority is `scenario_engine/iridium_constellation.py`).
  The slot ORIGIN of a plane is an 11-way ambiguity until one on-air pair
  anchors it — stamp unanchored planes as such; never join on a guessed
  origin. IBC is a DUPLEX frame; only IRA/ITL/IMS are simplex.
- **SGP4's returned velocity is not the exact time-derivative of its
  returned position** — ~1e-2 m/s per object, persistent, flat for
  differencing steps ≤ 0.5 s (measured on the raw TEME output and again
  with an independent `sgp4` call, 2026-08-27). Any range-rate truth built
  on a `tle` platform's velocity inherits it (0.089 Hz at L-band); a
  consistency bar below ~1e-2 m/s is below the propagator's own floor, and
  a finite-differenced rate that "passes" it is worse physics. Bound in
  force: 1e-5 of satellite speed relative + 0.025 m/s absolute (lane
  decision 13).
- **An unanchored constellation plane's derived slot is a property of the
  common-epoch STRING, not of the satellite** — it rotates one station per
  9.13 min of epoch choice (32.727° pitch ÷ 215.1°/h). Only an external
  `itlmap` cell fixes an origin; a geometry-matched ID↔NORAD pair is an
  IDENTITY, never a slot (lane decision 19).
- **Iridium blind tracking is records-only at runtime** (plan-21, merged
  `ad67eae7`): `iridium_receiver/tracking.py` — a standalone downstream
  library, deliberately OUTSIDE `blocks/` and the `PS.B<n>ir` namespace
  (`PS.B5ir` is the assembler and does NOT associate; lane decision 45) —
  joins 90 % of ID-less records at ≥ 55 dB-Hz from the record stream
  alone; TLE/ID-map are scoring-only. Per-track σ follows 1/√N to
  0.032 Hz at N = 64; an on-track collision jump is refused by track
  physics even when the spectral flag misses it.
- **The Iridium golden's shipped `sigma_doppler_hz` was the bare MCRLB
  through plan-18; DISCHARGED 2026-08-30 by plan-19, re-keyed 2026-08-31
  by plan-21** (contract §6 carries the law + provenance): `sigma_cfo`
  is profile-wired on the truth-free PAIR — the 860 Hz term applies only
  when `uw_snr_db < 21` AND measured `cn0_dbhz < 55` (at ≥ 55 dB-Hz the
  MCRLB path applies regardless of UW-SNR; a σ law keyed on one
  observable over-covered 25× in the regime the second identifies —
  lane decision 46, plan-19's synthetic gates re-proven under the new
  keying). The collision term is gated at the re-pinned **+6 dB** C/I
  harm boundary (was +8, decision-34 tripwire closed), and
  `sigma_source` reads `mcrlb | profile | collision_inflated`. σ
  validation is per truth population, never pooled (a pooled per-object
  detrend manufactured plan-20's false 15.58 alarm). The 860 Hz term is
  receiver/estimator-specific — never quote it as an Iridium constant;
  any estimator change re-fits it. B2 σ claims from plan-06 through
  plan-18 predate the discharge.

## Detector RTL

- **`PL.B1ir` detector RTL uses the original lossless fixed-point golden
  and vector family** (operator 2026-09-01, lane decision 49). Its current
  manifest is pre-freeze; the RTL hop still re-emits and freezes the full
  original lossless bundle before bit-exact gating. The program reclaims
  BRAM through image composition; compression is not on the detector RTL
  critical path. Plan-23's physical packing study remains the fallback
  evidence: lossless history + ring measured 181 BRAM36 against the former
  157-free view; E6M18 measured 139 packed tiles with the ring, zero observed
  decision-field churn, and 0.0000118 dB worst statistic error. E6M18 stays
  banked future work and may be activated only by a new operator ruling plus
  newly emitted/frozen vectors — never silently in place of the lossless
  authority. Linear 24-bit clipping remains REJECTED: 22.5 dB statistic
  corruption at exact ΔP_d parity.
