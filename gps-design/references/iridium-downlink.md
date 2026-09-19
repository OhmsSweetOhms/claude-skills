# Iridium downlink — hard-won facts

The Iridium signal-of-opportunity path: the on-air downlink structure, the
reference decoder used as an acceptance oracle, our own burst detector and
`PS.B<n>ir` receive chain, and the constellation/TLE work behind emitter
identity. Measured facts retain their dates and bounds; explicitly labelled
proposals are not measurements. Live thread state lives in `.threads/`, not
here, and the durable decision record is the ADR stores
(`docs/decision-log.md`, the `ADR-PS-B3IR-*` per-block store).

Originally extracted from the project `CLAUDE.md` (2026-09-04), then maintained
against source and benchmark corrections. Original section opening:

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
- **`merge_events` was O(n²) and the fix LANDED — do not shard on this
  account any more** (`199cdad1`, 2026-08-27). The original rescanned the whole
  merged list per raw event: ~2.1e10 comparisons and **~41 min of pure
  bookkeeping** on the 773.94 s live-sky capture at ~236 detector events per
  tape-second, which is one of the two reasons
  `data/plan-08-evidence/scripts/run_golden_sharded.py` exists. `_PrefixMax`
  carries `max(t_end_sample)` over the sorted merged list in O(log n) so the
  reverse scan stops at the first unreachable position; the output is identical
  **by construction**, not by test. Measured merge cost is now ~0.18 ms per
  short probe. The sharded driver still exists and still has its OTHER reason,
  but note its `run_shard` calls `measure_events` and therefore performs
  acquisition and decoding — wrong vehicle for a detection-only pass.
  **Detection-only full-record scan costs, measured 2026-09-11 on this capture:
  float ~13–18 min serial; the fixed-point backend ~77 HOURS.** Select on float;
  fixed point stays mandatory for any bit-exact gate.

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

## Bench runtime estimation — scale by REPLAY, never by SCORED duration

- **The drain is a fixed cost and does not shrink with the scored window.** A
  calibration that scores 8 ms while replaying 29.2 ms carries 21.2 ms of drain;
  scaling the whole calibration by the scored ratio multiplies that fixed cost.
  Measured on plan-36: the scored-basis formula predicted **14,644 s** against a
  true **4,079 s** (the run landed at 3,672 s) — **3.6x high**, enough to close
  a packet gate-incomplete against its own cap. Use
  `calibration_simulation_seconds / calibration_replay_ms` as the rate.
- Corollary worth keeping: **an integrated Iridium bench costs ~66 s per
  replayed millisecond** on this host at the five-module desk top, and a
  combined five-module OOC costs **~350-520 s**. Single-module OOC is 24-60 s.

## Association across bursts — bounded facts

- The earlier 24,242 Hz minimum and related separation percentiles compare
  **absolute received carriers** averaged per decoded satellite. Iridium
  satellites use different assigned RF channels, so those differences mix
  channel assignment with Doppler. They do not prove unique same-channel
  satellite separation, four-satellite subband separability, or a general
  association margin. Do not use them to size a gate.
- Channel-relative offsets also cannot be treated as continuous Doppler tracks;
  they reset on channel handoff. Association must carry nominal channel,
  absolute received frequency, time, uncertainty and identity provenance rather
  than collapsing them into one separation statistic.
- The deterministic IRA/ITL relationship remains useful within its measured
  scope: 24 structurally paired rows placed IRA four channel widths above ITL,
  within the stated ±300 Hz join tolerance. It is not a population-wide traffic
  association proof.
- IBC exposes `sat`, `cell`, `aq_sb` and `aq_ch` in the reference toolkit.
  `aq_sb`/`aq_ch` describe acquisition assignments where a handset finds the
  satellite; they are not a complete traffic-channel map or proof of a future
  duplex schedule. The current receiver does not decode them. Any predictive
  use needs a causal held-out measurement first.
- Sharing one coarse output does **not** establish an RF collision. Output
  centres are 160 kHz apart (about 3.8 Iridium raster spacings), with 320 kS/s
  per output. Simultaneous signals can remain distinct carriers. Collision
  classification requires measured spectral/time overlap under the receiver's
  declared rule.
- Association errors are correlated and self-concealing. Keep decoded,
  geometry-matched and unresolved identity provenance distinct, preserve
  competing hypotheses, and reject ambiguity rather than force-assigning it.

## Capture pool and real-sky evidence — bounded current facts

- Capture length is a **global** `cfg_samples_i` setting. The arm-to-sink command
  carries valid/ready, slot, subband, block, epoch, token and first sample; it
  carries no per-command length. A descriptor helper accepting `samples` does
  not change that interface.
- Buffers remain owned longer than capture slots in the measured model: 30.75 ms
  versus 20.75 ms. Cost the two pools separately: an ideal service ceiling is
  `min(slot_count / slot_hold_s, buffer_count / buffer_hold_s)`, before arrival
  clustering, subband contention and PS limits. Increasing one pool only helps
  until another resource becomes limiting; it does not establish system capacity.
  Both modules currently assert `N <= 16`.
- The corrected queueing check uses 32.6 declares per 90 ms frame against 34.7
  nominal captures of capacity at eight slots, or about 0.94 offered
  utilization. Erlang-B at that assumed load gives 20.75% blocking and points
  toward 12–16 servers; it flags eight as undersized under those assumptions.
  This analytic result is not production sizing because arrival independence,
  per-subband eligibility, buffer holding, PS service and sustained target load
  are not established together.
- Do not use the old per-event 73-concurrency, 54.9/28.3/5.5/0.6% refusal curve,
  or 102-event window maximum as fabric pool sizing. Those arrays count detector
  events and omit the subband field needed to enforce one live capture per
  subband. A physical per-subband demand peak cannot be reconstructed from them.
- One retained 40 ms real-sky slice did saturate its instantiated resources:
  eight of eight slots and sixteen of sixteen buffers, with 395
  `ALREADY_ARMED` and 352 `ALL_SLOTS_BUSY` run decisions. The 763 run rows are
  candidate/decision records, not 763 unique waveforms or a sustained-rate
  measurement.
- Duplex is the short 8.28 ms burst and simplex the long 20.32 ms burst. The
  real slice remained bit-exact despite its lower converter loading. These facts
  do not by themselves select a capture length or pool size.
- IBC is duplex and carries `iri_time`; a design serving that observable cannot
  simply stop all duplex reception. About one third of IBC block-1 variants
  carry the clock, and the variant is known only after decoding.
- A generated truth file embeds the source commit. Its byte hash is a commit
  stamp as well as content authentication; do not require reproduction at a
  different commit to match the old hash.

## Plan-37 saved-buffer benchmark lessons

The immutable pre-SADR diagnostic, retained inputs/results and portable verifier
are archived under repo-root `iridium-SADR/pre-sadr-benchmark/`; start at
`iridium-SADR/README.md`. They establish a comparison baseline only; they do not
implement or qualify the proposed SADR design.

- The coarse PFB output lattice advances 64 native samples. Its group-delay
  centre is 767.5 native samples, while earliest FIR support is 1,535 samples
  before the output endpoint. Stored channel I/Q is signed integer data divided
  by the exact coefficient gain 64; preserve the original coordinate metadata.
- A fixed detector tag is band-relative and needs the fixed implementation's
  +4,032 FFT-bin origin. It is not the float detector bin. The measured 12,345
  epoch offset and 532,480 floor-prime offset belong to this retained fixture;
  they are not RTL constants.
- The accepted census has 63 receiver-relative reference groups, 44 acquired.
  Saved buffers matched 17 accepted and 14 acquired groups. One group is
  strictly preserved, already in the trigger baseline, so net additional strict
  preservation is zero. Reference decoded frames are zero.
- Keep **accepted**, **acquired**, **decoded**, and **strictly preserved** as
  separate levels. The 103 unmatched accepted saved rows, including 28
  acquisition passes, remain unresolved. Nineteen reference groups have no
  eligible pair and 110 reference rows lack full retained context; none of
  these counts is automatically physical-burst truth or unusable RF.
- Narrowing a timing/frequency search does not remove required preamble,
  noise-reference, filter-support, payload or tail context. Restoration results
  show leading and combined context matter, but they do not establish a universal
  minimum look-back because context also changes noise estimation and the winner.
- The full matrix made 5,231 new isolated consumer calls and completed in
  2,971.598 s. Pilot process startup/exit was about 0.40–0.45 s around only
  0.12–0.15 s of consumer work. That is host harness overhead, not PS throughput
  or proof that a target processor keeps up.

## Current SADR proposal boundary

The proposed SADR architecture and specification live at
`docs/architecture/iridium-sadr/`; its draft proof is the receiver thread's
plan 38. Per satellite, duplex admission remains closed until an identified
simplex track is causally valid. There is no pre-unlock recovery requirement.
The proposal carries continuous 1.024 MS/s simplex data through DDR and measures
held-out same-satellite carrier/UW-time residuals versus age with complete-work
accounting. The proof is draft and unrun: no performance, pool-size, gate-width,
track-lifetime, PS-throughput or implementation claim follows yet.

## Capture path: BRAM delay, arm-only recognition, subband DDR sink

Earned by plan-31 Steps 0–4 (2026-09-08/09), measured in simulation. Lane
cache decision 72 holds the full numbers; this is the re-derivation-costly core.

- **The bin→subband map is the golden's NEAREST-CENTRE map with
  ROUND-HALF-TO-EVEN ties** (`pl_b2ir_channelize.py::bin_for`), never a
  floor/right-shift. The two disagree on **4191 of 8401 band bins (49.9 %)**,
  and at signed bin −1 a shift map returns subband 127 whose centre is
  158.75 kHz away when subband 0's is 1.25 kHz away. `peak_bin` is
  **band-relative 0..8400**, not an absolute fftshifted index — the detector
  assigns `o_band1 <= shft_v - BAND_LO`. There are **66 exact-tie bins**
  (`(s−N/2) mod 128 == 64`) and half-to-even differs from half-away-from-zero
  on **33** of them; the quotient is exactly representable in binary floating
  point, so rounding half away from zero is wrong, not merely imprecise. A gate
  sampling only bin centres passes under BOTH maps and proves nothing.
- **Exactly 128 detector bins per channelizer subband** (160 kHz / 1250 Hz),
  and detector hop / decimation = 8192/64 = **128 exactly**, so every detector
  block origin coincides with a channelizer frame boundary. The detector
  watches band bins 4032..12432 = 10.50125 MHz = **1616.0–1626.5 MHz**, the
  Iridium downlink band; ~66 of 128 subbands are in band.
- **The arm-deadline budget has two readings and the smaller one governs.**
  Frame-first is 471.96 µs; the channelizer's first captured frame needs FIR
  support `[end − 1535, end]`, so the earliest required sample leaves the delay
  1535 / 20.48 MS/s = **74.951 µs earlier**, making the governing budget
  **397.01 µs**. Quoting 471.96 µs alone is optimistic by 15.9 %. Measured arm
  path: **84.32 µs**, leaving 312.69 µs. Both readings must yield the SAME arm
  cost — that identity is what validates any such measurement.
- **Recognition must be ONSET-driven.** Waiting for burst end costs 8.28 or
  20.32 ms against 1.6 ms of retention — fails by 5.2× and 12.7×. One burst
  spans ~21–51 detector blocks (burst / 400 µs hop), so arming per run would
  consume 21–51 slots for ONE waveform; refusing further runs on a live
  subband (`already_armed`) is a requirement, not a preference. Measured
  13.8–32.8 refusals per capture, the weak tape lower because marginal
  detection does not fire on every block of a burst.
- **Delaying the channelizer OUTPUT instead of the raw IQ costs 3.5× MORE
  BRAM**, not less: the PFB is 2× oversampled (128 branches / 64 decimation =
  40.96 MS/s aggregate) and the word grows 24 → 42 bits. Raw-IQ delay is
  21.3 BRAM36 ideal; all-subband output delay is 74.7; even restricting to the
  ~66 in-band subbands is worse at 38.5. Do not re-propose this.
- **Never backpressure the detector's run stream to solve sink contention.**
  Its run FIFO is 512 records and **drops-and-counts when full**
  (`pl_b1ir_detector.vhd:34,60`), where a downstream consumer cannot see the
  loss. The proven answer is to never stall (`run_ready_o <= rst_n and not
  clear_i`) and count a `COMMAND_BUSY` refusal instead: upstream pressure
  becomes structurally zero and the loss lands somewhere visible. Losing an arm
  is recoverable; losing detector runs corrupts the detection record itself.
- **A published capture descriptor cannot be edited, so epoch revocation needs
  its own retained notification.** Without one, PS can hold an
  apparently-good descriptor from a revoked epoch and act on corrupt samples.
  Every descriptor from a revoked epoch is invalid **including already-published
  ones**, and the notification must be retained until acknowledged before
  reset/rearm — otherwise a clear silently erases a fault PS never observed.
  Related: a refusal must ALWAYS publish a terminal descriptor, because silent
  absence is indistinguishable from "not yet".
- **A gapped epoch cannot be inferred from the rail.** Channelizer and detector
  inputs are valid-only and free-running, so ordinary invalid cycles are legal
  cadence. A loss witness must come from upstream (`epoch_fault_i`), and the
  absolute sample counter must count the detector's **enabled valid edges** — a
  shared reset alone does not establish an epoch, and `A0 + b*8192` holds only
  in a loss-free one.
- **Coupled-lifecycle lesson:** holding an arm slot until PS release made slot
  occupancy depend on PS latency. In that earlier experiment, PS release
  1 ms → 10 ms took peak live slots 4 → 5. The current decoupled lifecycle
  releases the slot at capture retirement; PS service/release instead extends
  buffer ownership. Apply the latency term to the pool that actually retains
  ownership, not indiscriminately to capture-slot count.
- **There is no UltraRAM on the xczu9eg** (912 BRAM36, 0 URAM). Do not offer
  URAM as a BRAM-relief option on this part.
- **`PL_B1IR_DEPTH = 7` is empirical and was already swept** at fixed P_fa:
  depth 5 drops P_d at 45 dB-Hz from 0.992 to 0.827, failing the P_d ≥ 0.9 bar;
  depths 4 and 6 were never measured. Separately, 30 of the detector's 166
  BRAM36 were recoverable bit-exact with no ADR amendment: 19 tiles removed
  (13 from an unread history row and six
  from narrower history words) and 11 relocated (nine from FFT tails and two
  from `rf_mem`). The read address already leads the write by the RAM's 2-cycle
  latency. **All thirty LANDED
  2026-09-10** (detector 166 → 136, bit-exact, no ADR amendment, no cycle
  cost), and the five-module chain re-measured **166.5 BRAM36 / 76 DSP /
  WNS +2.742 ns** on socks main 2026-09-11. Distributed RAM accounts for the
  11 relocated tiles, not all 30 removed from the BRAM total. Combined LUT
  rose 44,522 → 48,806 with 4,758
  LUT-as-memory, and setup/hold endpoints grew **34 %** because LUTRAM and
  SRL cells are timing endpoints where a BRAM is not — invisible at unplaced
  OOC, and exactly the class of thing that bites at placement and routing.
