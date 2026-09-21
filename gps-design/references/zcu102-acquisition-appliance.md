# ZCU102 acquisition appliance — hard-won facts

The ZCU102 appliance is the silicon substrate for the GPS acquisition
engine: the PL capture rails and bit-select taps, the acquisition daemon
that arms jobs and services declares, the replay/injection path, and the
recording chain on NVMe. Everything below is a MEASURED fact from a board
leg or a desk reproduction; the dates are preserved as written. Live
thread state lives in `.threads/`, not here, and the durable decision
record is the ADR stores (`docs/decision-log.md`,
`docs/architecture/zynq-pl/**/decisions.md`).

Moved verbatim from the project `CLAUDE.md` (2026-09-04). Original
section opening:

> Hard-won on the ZCU102 appliance across the day-2 image board legs and the
> A2 daemon hop. Live state lives in `.threads/fpga/*multiband-acquisition-
> engine/` and the PL thread's board block, **not here**.

## Levels, bit-select windows, and rails

- **The engine's bit-select feeds are 8-bit windows, and the sky operating
  point is a fine quantiser — NOT a hard limiter.** `REG_OUTPUT_BITS` resets
  to 8 in the RTL (`pl_b1_l1c_bit_select_axi.vhd`, `output_bits_r <= x"8"`,
  ADC 12 − 8 = the plain MSB slice) and reads `0x8` on all four engine taps
  (plan-04, 2026-08-26). Live-sky rail RMS on the 16-bit container ≈ C/A 74,
  L1C 87, L2C 102, L5 320 LSB; at the sky shifts (1–2) the window
  `[shift+7:shift]` spans 2–512 LSB, so the RMS sits INSIDE it at ~12–63 %
  fill with a saturated fraction ~1e-7 (measured dark and replaying). An
  earlier version of this bullet said "deep hard limiting on a 4-bit window"
  — wrong; the empirical rule it derived still stands: a window whose LSB
  sits at or above the RMS discards the bits the signal lives in and reads
  as an empty sky (the L2C shift-4 "0/6" trap). Rule for any new band:
  **start low, walk up until the ratio falls.** A tone shift is NOT a sky
  shift (a seam tone
  drives the rail ~44 dB hotter). Shifts are registry-carried per band
  (`bit_select`), measured with `measured_on` + evidence.
- **Replay tapes are near full scale; the level lever is the tape, not the
  board.** The 2026-08-23 `dec8.l5` sky record is 190 LSB RMS per component
  (crest ~21 σ); the lossless pre-scale ceiling is `x8` (+18 dB); the
  loopback transfer tape→engine-tap was −43.3 dB through the 18 dB pad
  (measured 16.1 dB) — the pad is now OFF (ruled). `prescale_x256.py`'s
  256 is a constant and clips such a tape. Measure level at the engine tap
  (`REG_MEASURED_POWER`/`WINDOW_SAMPLES`), never the recording ring.
- **Never compare shifts (or anything) sequentially on live sky at small
  arm counts** — the same cell moves 2× at the same shift over ~40 s.
  Interleave the conditions inside one process, repeat the sweep 3–4×,
  pool. Controls come in two classes: PRNs in the sky that do NOT carry
  the band (capability controls) and PRNs that carry it but are NOT in
  the sky (geometry controls).
- **A rail power figure is meaningless without its image.** The
  decision-82 rescale put the C/A rail at ~25 M `REG_MEASURED_POWER_L` on
  `0x000b0000` vs ~5.8 M on `0x000a0000` (+7.2 dB measured); a "healthy"
  threshold copied across images fails a healthy board by 6 dB.
- **A gate number taken through an int16 container below ~1 LSB RMS is a
  fiction — recover σ, don't read the PSD.** The dec25 recording rails sit
  at 0.17–0.65 LSB; round-to-nearest turns a +6 dB real rise into +14–16 dB
  and under-reports a sub-LSB leak tone by up to 3.8 dB (the day-1 "62.4 dB
  isolation" was ≈58.6). The live-ADC gate now fits σ from P(sample=0)
  (verified against P(±1)); ceilings are recovered-σ (decisions 89/90).
  Never carry a dBFS/Hz ceiling derived from a sub-LSB reading — it fails a
  HEALTHY linear rail by ~9 dB the day the rail rises past 1 LSB.
- **A CW injection can never make the engine declare** — a tone is flat
  in code phase, so peak1/peak2 stays ≈1 (measured: +91 dB on the C/A tap,
  75 jobs, no declare). Forced injection is a rail/level/isolation
  instrument, not a declare source; only replay of a coded recording is
  (2026-08-25).
- **The replay path has no level knob and never had one.** Level is a
  tape pre-scale (`prescale_x256.py`, fresh `S` per tape) plus an analog
  pad; the engine-tap need for the `20260823T154718Z` `dec8.l5` tape on
  `0x000b0000` is ≈45 dB (2026-08-25).

## Daemon, bring-up, and arming

- **Under the no-sky posture the daemon cannot believe its own declares.**
  `VoteCredibility` corroborates every declare against gpsd's sky list;
  with no device every declare — even PRN 18 at ratio 12 against a bar of
  2 — is `credible=false reason=gpsd_dark`, so the offset servo never
  re-centres and the ratio-model C/N0 is never published (plan-04,
  2026-08-26: 22/22). `require_credible_votes: false` frees only the servo
  vote. Declares are still counted and journaled — the decision-45
  replay-vs-dark contrast works; anything downstream of "credible" does not.
- **`serving` now implies the engine code-lib is loaded and verified.** A
  cold boot wipes the DDR carve; `gps-live-ctl bringup` carries a `code-lib`
  stage (verify → load → verify, PASS required; `loaded_this_bringup`
  distinguishes cold from warm) and the daemon re-verifies at warmup-hold
  entry and refuses to arm on MISMATCH. Before 2026-08-23 neither existed
  and a cold-booted daemon would arm against garbage with no signal.
- **`REG_JOB_BAND @0x0e8` is shadowed with `REG_JOB_NSEL` at GO** (legal
  pairs L1C 0/16, C/A 1/12, L5 2/15, L2C 3/16; STATUS bit 31
  `band_refused`, which also ORs into `cfg_error`). Every hand-armed
  diagnostic must restore mux / taps / shift / injector / **band word**,
  or the daemon inherits a band it cannot arm — band 0 silences C/A and
  with it every seeded band.
- **Seeded bands must reach their seed error.** The installed
  `ladder_coverage.py --gate` is the authority; it only checks LOADABLE
  rows, so an unloadable band hides its own geometry defect (L2C: 50 Hz
  seeded reach vs 245 Hz C/A-derived seed error). Run it against the
  post-flip registry BEFORE committing a `bench_verified` flip.
- **The daemon cannot cold-start on the new unit until plan-16 lands:**
  `COLD_START_CENTER_HZ_DEFAULT = 34000` is the OLD unit's offset compiled
  in, and the ±60 kHz validator cap cannot express the new unit's offset
  (config exit 2, terminal under the unit file). A wide cold ladder also
  reaches the decision-106 sum bound `|centre| + doppler_max ≤ 65 535`,
  which `EngineProfile.center_errors` does NOT enforce (it checks the
  retired 2 044 500 Hz carrier-phase bound) — an arm at 65 500 Hz wedges
  the engine for the boot. The bound has two homes; the generated regmap
  is the truth.
- **`gps_pl_control CONTROL @0xa0005008` bit 0 is the capture START** (the
  cpack→FIFO path the recorder and `ring_fanout` live on) — not a DDS
  enable. Tone injection is `DDS_FREQ @0x0c` + `INJECT_MASK @0x10` only.
  Toggling CONTROL stops the whole capture chain while `gps-live-ctl
  status` keeps reporting `serving` (stage results are latched at
  bring-up); the next `record-start` then strands its preallocation.
- **Never call `mmap.flush()` on a `/dev/mem` mapping** — `msync` returns
  EINVAL on device memory; the write has already landed but the exception
  kills the block BEFORE its readback-verify, and on an injector/replay arm
  path that leaves the board driving a full-scale tone with nobody
  checking. Write, then verify from a separate read-only pass; keep the
  restore in a `finally`/EXIT trap (daemon-deploy board leg, 2026-08-25).

## Engine behaviour and known artefacts

- **A dark (near-empty) input makes the acquisition engine declare at
  code-phase bin k ≈ 0 (±10) on every PRN and every Doppler row** — an
  engine artefact, not signal (root-caused 2026-08-24, decision 96): the
  Q15 twiddle stage truncates (floor, ADR-PL-B2-002), which stamps a
  deterministic bias pattern B(f) on every transform; the code-lib
  replicas carry the same B, so the correlation product holds |B|² ≥ 0 on
  every bin → a lag-0 pulse. Buried on sky (input transform ≫ B), it wins
  ~1 % of jobs when the input after the bit-select window is sparse ±1
  (dark rail, or a window set too high). Until the round-to-nearest fix
  ships, read any |k| ≤ ~16 declare on a quiet rail as this defect, and
  never accept a "declaring" band as proof a window is right. Reproduce on
  the desk with the engine thread's `diagnostics/l5_zero_lag_*.py`; no
  Xsim or board needed.
- **The L5 coherent fold is PROVEN ON SILICON (2026-09-03, image
  `0x000e0002`, new ZCU102 unit):** `fold_n = 8` jobs complete with zero
  stall, the ILA shows the spill-fetch MM2S handshake during a sweep, fed
  replay declares from the exact winner at N=2/4/8 and the N=8 gain lands
  within 1.5 dB of `20·log10(N)` on 4/4 PRNs (the weak PRN 27, 24 %
  ordinary declare rate, declared on every N≥4 sweep). The Phase-B
  starvation signature was a same-edge FIFO race in
  `pl_b2_l1c_sample_spill.vhd` (`beae1610`). **`REG_FOLD_PORT_BEATS/CYCLES`
  were a DEAD INSTRUMENT** — declared, read, never assigned since the fold
  RTL row (`730b397e`); the "beats=0" in every trace since was that, not
  the fold. Rule: a bar clause that reads a register must first prove the
  register is DRIVEN (grep the assignment), or it is not evidence.
- **The engine vector bench never instantiates the real sample-spill /
  grid-spill / replica-fetch / async-FIFO / arbiter units** (the engine
  alone); the spill tb has the real spill and a modelled engine. A
  change confined to those units cannot move any `--case all` number in
  either direction; every wrapper bench oversupplies MM2S ~10×. The
  real-unit authorities are `--smoke-fold-only` (N=2) and the unit tbs.
  `run_vector_gates.sh` runs a ~79-min auxiliary layer on EVERY call,
  even one `--case X`; the engine bench runs ~1,450 clocks per
  wall-second (N=8 fold shard ≈ 3.75 h, `cadwell_d64` ≈ 2.3 h).

## Recording, capture rails, and pulls

- **Recording two members at once can lap the dec25 ring in seconds** — a
  `dec8.iridium + dec25.l1` record at 108 MB/s went fatal at 11.4 s
  (`R5 overrun_count=1`, a full 128 MiB lap); the NVMe has NO characterised
  write profile (`unknown_drive`), so no rate is known-safe. `record-stop`
  returns 0 on a run that is already dead — read the published-run listing,
  never the exit code. The sink sweep runs at bringup only, never between
  two records in one chain lifetime (a stale pair blocks finalize). Bulk
  pulls: stop the viewer relay first (1.8×); `sftp_xfer.py`'s 2 MiB window
  caps at ~21 MB/s against a 118 MB/s wire.
- **The fast rail was left on `dec8.iridium` (`fast_mask 0xc0`) from the
  2026-08-27 override until 2026-09-04** while every doc said `dec8.l5`
  — read `fast_mask` from the board at custody, never from a handoff.
  Per-band configs generated from the row-1 bytes with only the intended
  fields changed hash byte-identical to the historical L5 (`f428375e…`)
  and Iridium (`8a8a443b…`) configs — generation is the verified put.
- **A reclaim list is derived from the recordings registry, never from a
  size listing:** the operator's 2026-09-04 NVMe reclaim deleted the
  2026-08-23 `dec8.l5` recording of record (no host copy). A tape of
  record carries a digested host copy BEFORE any board-side deletion.
- **Bulk pulls through paramiko SFTP cap at ~21 MB/s on a 118 MB/s wire
  (2 MiB window, Python-bound); decision 129 moves bulk bytes to plain
  HTTP served per pull from the NVMe** (`plan-bulk-pull-http-path`, bar
  ≥ 80 MB/s measured). Stop the spectrum relay during any pull (1.8×).
- **On the loopback cabling the F9P sees a silent DAC:** `nSat` looks
  healthy, `uSat 0` / `mode 1` give it away, and `decrail:gnss_sidecar`
  validates presence and hashes only — a fix-less sidecar publishes
  `complete`. Check `uSat` before trusting any sidecar recorded off-sky.
- **A capture-only decision-17 gate is not the analyzed gate.**
  `e2e_gate.py --capture-only` returns before pull+analysis (no
  `analysis_manifest`); the image of record advances only on the
  `--resume-from-pull` verdict that compares noise floors and tone margins
  to the ceilings. Recordings stay resumable on the board.
- **The decision-17 live-ADC gate row refuses a serving appliance by
  design** (`e2e_gate.py`: "mutually exclusive owners of the
  R5/S2MM/ring stack") — its only path is `--teardown-appliance`, its
  contract is `fast_mask 0x03`, and its ceilings assume a terminated or
  pad-loopback ADC0. Never schedule it inside a recording window; it is
  minutes in any window that already has a teardown.
- **Images are composed per flavor (ADR-030, ACCEPTED 2026-09-03):**
  RECORD (capture rails, no engines, no TX) and TEST (TX/replay + unified
  engine + Iridium rung, no capture chain); the legacy C/A engine is
  retired from all images (decision 124: keep-frozen, drop-from-recipe,
  historical-only). Composition is already env-gated in
  `post_bd_mods.tcl` via `GPS_STREAMING_PIPELINE` tokens; the TX/replay
  and capture legs are sourced unconditionally today. A RECORD image has
  NO acquisition window — probing `0xa0008008` on it is the wedge that
  cost a board; flavor identity lives in an always-present register.

## Board clock and reference offset

- **The appliance board keeps correct UTC across a power gap and disciplines
  it by NTP (timesyncd stratum 3, ~100 µs)** — measured 2026-08-27 after a
  ~19 h gap. Do NOT hand-set the clock over the paramiko channel (its
  asymmetry is ~1 s and a later NTP step can land inside a tape). The
  "no RTC battery" belief was wrong — re-refuted 2026-09-06 under
  instruments: `rtc_zynqmp ffa60000.rtc` set the system clock correct to
  ≲ 3 s across a 72.2 min (4 331 s) unpowered gap BEFORE any network time,
  with `fake-hwclock` rejected as stale and `htpdate` failed, so neither
  alternative explains it. The mechanism (battery / supercap / rail-powered
  RTC) is still uninvestigated — it is backed, not why. Wall-clock is for
  EVENT STAMPING only, never for ppm (decision 138 as amended).
- **A warm-measured `sky-servo` prior is usable from cold:** after 72 min
  off, the crystal had walked 750 Hz = 0.476 ppm from the card's warm final
  — 15.9 % of the 3.0 ppm prior floor and 4.3 % of the armed 5-rung C/A
  ladder — and the daemon declared 14.3 s after warm-up on the prior ladder
  with `refused_rounds 0` (plan-19 board leg continuation, one sample).
- **The board's reference offset is one number across bands:** the GPS
  engine's decision-60 value (21.66 ppm at L1) reproduces on Iridium
  carriers at 1621.2 MHz to 0.1 % (21.68 ppm, lane measurement
  2026-08-27). Any band's channel/bin resolver that spans less than the
  offset needs the per-boot ppm fed in, not a per-band re-measurement.
- **The reference offset is a PER-BOOT measurement (decision 125), never
  a per-unit constant.** The new ZCU102 unit reads ≈ −47 to −51 ppm by
  the rail-vs-NTP desk fit (±5 ppm, channel-selection grade only) —
  opposite in sign and 68 ppm away from the old unit's +21.66; loopback
  cannot measure it (TX/RX share the reference); the program sign
  convention is "reference minus nominal, positive = clock runs fast"
  and the desk-fit value's sign is unverified against it.
- **The reference offset is measured by a CHAIN, and the links ADD
  (2026-09-04 sky window):** `gps_capture_s2mm` `BLOCK_COUNT`
  (`0xa0010014` dec8 / `0xa0030014` dec25) against board `CLOCK_REALTIME`
  gives rail-vs-BOARD-clock (this boot −19.203 ± 0.890 ppm over 960 s,
  both rails agreeing to 0.05 ppm, daemon-free, nothing armed); the F9P's
  gpsd TPV against arrival stamps gives board-clock-vs-GPS (−3.0 ppm in
  the same slice); rail-vs-GPS is their SUM (−22.2 ppm). Two numbers on
  one page from different link pairs are not a disagreement — name the
  pair beside every ppm. The sign is verified negative (clock SLOW) on
  this unit; the fold-proof boot's engine-stamp fit read −46.9 ± 5.1,
  27.7 ppm away and unexplained (warm-up vs instrument systematic) —
  decision 125's per-boot rule is the only safe reading. The banked
  engine-stamp recipe (`ppm_fit.py`) cannot run with the daemon stopped
  (`REG_CAPTURE_STAMP` is latched at job completion) and reads ±350 ppm
  under the four-band mux config.

- **A measurement tool must prove its source MOVED (decision 131, board
  leg 2026-09-04).** The plan-16 `ref-offset` bring-up stage (position 6)
  samples the S2MM block counters before `t0-live-fanout` (position 8)
  drives `CONTROL` bit 0, fits a flat line, and publishes a well-formed
  ACCEPTed record of −1 000 000 ppm that passes every guard — monotonic,
  ZERO residual, rails agreeing to 0.000 ppm, sign consistent, the
  consumers' own `load_record` — because a stopped rail out-scores a live
  one on every statistic. The detached 900 s continuation launched from
  stage 6 always straddles the fanout restart; the restart RESETS the drop
  counters, the direction-agnostic health gate (`pre != post`) reads it as
  a drop, the final never publishes, and `status.json` keeps a live
  `final_pid` with `refusal: null`. Same defect class as the dead
  `REG_FOLD_PORT_BEATS` instrument. Until the fix lands: hand-fire the
  installed tool on a serving board (a tool artefact, never a hand-written
  record) and gate it against an independent measurement.
- **The daemon's servo ppm sign WAS inverted (decision 132) and is FIXED —
  do NOT negate it.** Until socks main `3eda8b76` (plan-17, 2026-09-05)
  `acq.json engine.offset_servo.ref_offset_ppm` carried the received-carrier
  sign under the reference-minus-nominal convention string (record −21.662
  vs servo +21.264 on one boot). The fix derives ppm through
  `ref_offset.sign_of()` and was PROVEN on silicon by the plan-19 board leg
  (2026-09-06, row 5: `sign self-test: ppm opposes estimate_hz`; the servo
  produced −21.58 ppm from `estimate_hz +34 000` — a LOW LO shows a nominal
  signal HIGH). Since plan-19 the servo IS the record's producer
  (`method sky-servo`), so record and servo agree by construction; negating
  the servo now double-inverts by ~43 ppm ≈ 69 kHz at L1, which is exactly
  the Iridium-channel error (1.66 steps) the old advice warned about. Read
  the decision-125 record; its `method` may read `sky-servo` or
  `persisted-prior` (a prior's `sigma_ppm` may be 3.0). Whether decision
  60's +21.66 on the old unit was the same inversion is still OPEN.
- **The daemon is autonomous for its reference offset (ADR-031, proven
  2026-09-06):** no record → full field on C/A + L5 (17 / 45 rungs,
  ±59 500 / ±45 000 Hz; L1C / L2C held unless they carry operator
  `centers_hz`, which this board's config does); a persisted prior on
  `/boot/gps-receiver-state.json` (+ `.prev`) is served at ≥ 3.0 ppm and
  buys a 5-rung C/A ladder; the servo hears four bands and writes the
  record to the live path and the card; the NTP fit is a bench diagnostic
  only (`ref_offset_measure.py`, default `--seconds 120` — σ ≈ 7 ppm at
  that length, 900 s for a σ ≈ 0.9 final). Measured on one good sky: 174 s
  power-on → `serving` (8 stages), 4 s from arming to the first wide-field
  declare, 38 s to the first servo record; on the prior 8.6 s vs 20.0 s to
  first declare (n=1 per arm). The wide field is cheap when the sky is
  strong. `ladder_coverage.py` reads only the tmpfs live path (blind to
  the prior) and ignores `centers_hz` in `--no-record` pricing.
- **The sky-gate is the receiver's live-sky health check (brainstorm S1,
  decisions 126/139 as amended; landed 2026-09-06 socks main `abd05b5a`):**
  `sky_gate_collect.py` gathers a ≥ 600 s bundle (job-stats slice,
  `acq.json`/record/status polls, gpsd `SKY` every 30 s, F9P sidecar;
  posture flags `--antenna/--lna/--pad` are REQUIRED operator words — the
  board cannot sense the LNA); `sky_gate.py` computes six PRN-agnostic
  statistics per band and judges them against envelopes keyed by image
  VERSION (banked only with `--ruling`). Shapes that first light forced:
  bank per statistic; the alarm rate's envelope is `[0, garwood95_hi]`; the
  ppm is additive `± max(3σ_combined, 0.5 ppm)` with sigma an upper bound
  only (the crystal walks ~0.9 ppm boot to boot); the C/N0-vs-F9P censor is
  SUBJECT-side (`subject_min + 1 dB`) because `ratio-model-inversion`
  compresses weak SVs UPWARD at its ~41.4 dB-Hz C/A floor; judge REFUSES a
  bundle whose registry/config/capabilities shas differ from the envelope's
  unless `--allow-config-differs`. Envelopes of record for `0x000e0002`
  live under `systems/builds/sky-gate-first-light-20260906/` (worktree
  `socks-sky-gate-board-leg`); n = 1 window — two windows 15 min apart
  disagreed on 9 of 31 components, so a median-of-windows bank is the open
  design question. The e2e gate's live-ADC row cannot run on the antenna
  posture (its setup schema demands loopback topology).
- **Dwell and fold on a serving board (sky-gate leg row 4b, 2026-09-06,
  n = 1):** a round is a full band×PRN sweep with NO time budget or fence;
  `engine.dwell.ca.dwell_target 8` (one RTL-accumulated capture 8× longer)
  plus `engine.fold.l5.fold_n 8` (20 hypothesis jobs per PRN) stretched the
  round ~20× — total jobs 1 929 → 580, C/A 690 → 34, L1C and L2C ZERO
  declares in 600 s. Per job C/A declared 1.91× more at D = 8; the L5 fold
  declared ~2.9× more per search opportunity (n = 1, no rate yet). The
  published C/N0 moved −3.19 dB from D = 2 to D = 8 against a fixed F9P
  (≈ `5·log10(4)`): `Cn0FromRatio` models D exactly, so the defect is in
  the ratio or dwell count it is handed — under investigation
  (`dwell-fold-investigation`). Do not raise a dwell count on a serving
  board without a scheduler ruling.
- **The F9P TPV arrival-stamp board-vs-GPS term is load-sensitive** (board
  leg 2026-09-04): −3.5 ppm idle vs +8.1 ppm under ~12 k jobs/15 min on
  the same tool and cabling while rail-vs-board moved 0.78 ppm; the loaded
  slice bulges mid-window (CPU-load signature) and χ² cannot see it. The
  sky window's composed −22.172 inherits the caveat. Only a
  contemporaneous, idle-board slice is usable, and only as a bound.
- **The record's σ is precision, not accuracy:** the board's NTP peer is
  a public pool server at a 2048 s poll (RootDelay ~10 ms, jitter 0.5 ms,
  kernel correction −1.27 ppm), so a 900 s window can fall entirely
  between polls and the usual offset-corral rate bound does not apply
  (0.25–2.4 ppm reference uncertainty, unpublished). The only accuracy
  statement is the record-vs-servo cross-check, two instruments sharing
  no reference (0.517 ppm on the 2026-09-04 boot).
- **No warm-up transient is visible from uptime ~205 s on a cold boot**
  (off overnight; Δχ² flat-vs-exponential 0.17–0.45 against 7.8 at 95 %):
  a bound, not a null — the rails only count once the chain is `serving`
  (~160 s), and a τ ≈ 125 s transient is 19 % of amplitude by then.
  Decision 71's 900 s hold is neither contradicted nor confirmed; a 120 s
  window's quantisation floor (6.67 ppm) exceeds the transient's amplitude
  (3.94 ppm), so single-width ladders are vacuous — use a width ladder
  and a window-mean forward model.
- **`sftp_xfer.py --sha256` is GET-only** — a push with it fails `RC=2`
  and a watcher grepping for its own pattern in `ssh_run`'s echo fires
  instantly (use the `[b]racket` idiom and check the artefact).

### Facts from the plan-17 board leg (2026-09-04/05, socks `daemon-ref-offset-fix`)

- **The board runs the 252-slot LIBRARY code-lib staging** (`bundle_set
  library`, whole-node `78c6b771…`; `$STAGED` = a symlink into
  `/root/capture/linux/codelib/staged`, same bytes as `$SHARE`,
  `code_map_path null`). The 12-slot corner map in `codelib/code-lib-map.json`
  is the anchor test's set, NOT the deployed artifact.
- **`install.sh` from a clone installs NO blobs** (`staged-full/regions/` is
  gitignored build output) and its no-blobs branch (`:114`) overwrites
  `$SHARE/code-lib-map.json` with the 12-slot corner map — the running
  daemon's map drops from 252 to 12 slots. Never clear `$STAGED/regions`
  from a clone (the blobs are the only copies; the frozen 12-slot bundle-set
  cannot restage). Deploy = install.sh with the daemon DOWN, then re-install
  the correct 252-slot map to `$SHARE` and verify `sha($SHARE) == sha($STAGED)`
  before anything starts the daemon. The L1C relabel (`l1cd` → `l1cp_boc11`)
  was applied by byte-identical COPY of the 63 blobs plus a scripted
  127-field map transform, old files left in place (315 blobs after).
- **`jq` is not installed on the board** (rc 127). Read JSON fields with
  `python3 -c`; a `$(jq …)` in a command string yields an empty argument
  silently.
- **`RefOffsetGate` (pre plan-18) never re-checks a held FINAL's expiry**:
  `valid_until` is evaluated only in the loader on the re-read path, so the
  daemon arms indefinitely on an expired record (`refused_rounds 0`,
  `reads 1`) and a record hand-fired to the live path is never read until a
  restart (one restart recovers in ~109 s). Fix = plan-18
  `ref-offset-gate-freshness`. Until it lands, a running daemon's record can
  only be replaced by a restart.
- **The 120 s hand-run of `ref_offset_measure.py` is 6× noisier than the
  900 s stage final** (σ ≈ 7 vs ≈ 1.2 ppm); it confirms, it does not
  tighten. Both measure rail-vs-BOARD-clock; on a fresh boot the kernel's
  NTP frequency correction is still settling (+12 → +4 ppm applied over the
  first ~40 min) and moves both — the record's `sigma_note` term. The tool
  is non-intrusive under a running daemon. **Measured 2026-09-06: a 900 s
  fit run 19–34 min after a cold boot read −26.50 ± 0.91 ppm against the
  servo's −21.90 ± 0.68 — a 4.6 ppm, 4.04 σ disagreement — while the two
  rails (dec8 / dec25, independent S2MM counters) agreed to 0.084 ppm and
  `kernel_freq_ppm` was +7.65 with `poll_interval_s 1024` (a 900 s window
  can fall between two NTP polls). The same tool at 120 s on a warm ~4 h
  boot read −21.57. So: the NTP fit is NOT trustworthy inside ~40 min of a
  boot; its σ is fit precision only; the servo is the instrument whose
  stability is demonstrated (three warm finals within 0.64 ppm and a cold
  one 0.48 ppm off). Unsettled experiments: the same 900 s fit ≥ 90 min
  into a boot; `kernel_freq_ppm` banked beside every tool record.**
- **`record_vs_servo_ppm` is not a clock-vs-clock check**: the servo's
  estimate is a median over its current fresh-PRN set and moved 2750 Hz
  (1.75 ppm at L1) as that set grew 4 → 9 during a 12-min soak. A bar of
  `σ_record + 0.16 ppm` was mis-specified; observed 1.8–3.5 ppm on a good
  boot.
- **Power-cycle stamps:** when the operator sits at the worker's own
  terminal, their direct "go" is the stamp (host UTC at the word); a relay
  from another session is confirmation only. A warm `reboot` wedges this
  unit intermittently — power cycle, ≥ 60 s off.
- **Evidence dir:** `/mnt/nvme/gps-acq/ref-offset` does not exist on the
  board and `engine.ref_offset_evidence_dir` is null, so the stage falls
  back to `RUN_DIR` (tmpfs) with a logged warning — one `mkdir`/key.

## R5 firmware loading, and the reference-receiver sidecar (as of 2026-09-10)

- **R5 firmware loads through Linux remoteproc, never a BOOT.BIN
  partition.** `linux/capture/board_bringup.py` inserts
  `zynqmp_r5_remoteproc.ko` by path, writes the firmware name to
  `/sys/class/remoteproc/remoteproc0/firmware`, writes `start`, and checks
  an OCM marker; `COLD-REBUILD-GATE.md` gates `no_r5_partitions == true`.
  ADR-011's rejection is of OpenAMP with rpmsg, not of the loader. The
  firmware is a "bare-rproc" ELF: no BSP boot object, its own boot shim and
  linker (`no-os/capture/r5_0_dmac_probe_rproc.{S,ld}`, ATCM at 0, BTCM at
  0x20000, DDR carve at `0x7000_0000`), and the **`standalone_v8_0` BSP is
  a hard pin** — v9_0 wedges in `Xil_SetMPURegion` (bench-proven
  2026-07-09). The device tree declares `r5f_1` in split mode on
  `0x7800_0000` with TCM 1a/1b, so R5_1 follows the same recipe through
  `remoteproc1`; whether `remoteproc1` enumerates on the running board is
  unverified until the firmware thread's step 0 runs it.
- **The reference receiver is now the EVK-X20P all-band module** (replaces
  the ZED-F9P). The sidecar reaches the daemon through gpsd JSON rawdata
  rows, not raw UBX. **GPS L1C has no UBX sigId on the X20P** (HPG
  2.02–2.11: a `gps_L1C` capability bit in `MON-GNSS` only), so L1C
  observables cannot be expressed in UBX without a documented extension.
  **`RXM-SFRBX` word packing is unspecified by both vendor interface
  descriptions**, and gpsd (per-word D30* inversion) and RTKLIB (none)
  disagree — the in-tree PS.TLM-vs-F9P harness's assumption needs one real
  capture. `pyubx2` encodes and decodes UBX and tracks the X20 firmware.
  Interface descriptions and the vendor JSON: `.research/session-20260910-144850/`.
- **The reference drift is a trajectory; every board row carries uptime
  and measured drift** (operator ruling; tracking cache and plan-30
  `drift-both-halves`); the `measured_declares` slot in `AcqFdTolerance`
  is the tier-1 input, unwired until that hop.

- **There is no free-running u-blox capture on the appliance (checked
  2026-09-10).** The reference-receiver sidecars exist only inside a
  recording window: `gps-live-ctl record-start` spawns them beside the IQ
  recording and `record-stop` finalizes them. Both go through gpsd, which
  OWNS `/dev/ttyACM0` as the sole tty reader by design (`gps_live_ctl.py`
  §F9P GNSS truth sidecar): one logger is `gpspipe -r` (NMEA only, no
  Doppler), the other `gpspipe -w` (gpsd's DECODED JSON of `RXM-RAWX`).
  Nothing we have ever kept is raw UBX — which is why the `RXM-SFRBX`
  word-packing convention (gpsd inverts per-word D30*, RTKLIB does not) is
  unverified. The UBX contract's raw-capture recipe
  (`docs/spec-ubx-output-contract.md` §16: two `CFG-VALSET` frames, the
  nine-message set at 1 Hz, 460800 on UART because the 38400 default
  silently drops ~30 % of it, `cat`/`gnssdump` to a `.ubx` file) keeps gpsd
  OUT of the path, so on the board it needs gpsd stopped; host-attached
  over USB CDC (no baud to set) is the simpler route. A free-running raw
  logger would be a new `gps-live-ctl` verb, not a flag.
- **`test_acq_engine.HostSpansInstrumentTests` is flaky by construction**
  (2026-09-10): its wall-clock self-tests compare millisecond spans with a
  1.0 ms tolerance and fail about half the time even in isolation (missed by
  3.18 ms under load), so "suite green" on the boot-autonomy tree is a coin
  flip until they get the load-tolerant or serial-only treatment from
  `docs/test-conventions.md`. Re-run a failing suite once alone before
  believing either result.
- **Per-unit state matters the day the hardware is swapped** (2026-09-10):
  the SD card's `/boot/gps-receiver-state.json` prior and the
  `engine.centers_hz` lists belong to the unit that wrote them; a different
  unit's prior narrows the sweep around the WRONG ppm, worse than none. Move
  it aside before first sky on a new unit; the drift anchors (+3.4 Hz/s at
  40 min, −0.65 at 6 h) and the 441 s excursion-gate floor derived from them
  are one unit's measurements, re-derived from each unit's own record.

## Vectors and gating

- **Freezing a vector tree after a split must be a metadata STAMP; a
  re-emit undoes the split** (`--fold-cases` emits every `fold_n` into one
  directory, so re-emit-then-split and split-then-re-emit both restore the
  un-simulable N = 16 bundles). Split-then-stamp is the only correct order
  (`--fold-split-n16` + `--fold-freeze-gated`, `VECTORS.md`); a family with
  NO split still freezes by re-emit, because the re-emit re-derives from
  the golden and the byte-compare is a drift check a stamp cannot give
  (`vectors-l2c` 24/24 identical). A frozen tree's `freeze_provenance` is
  emitter-owned and carries one family's history — never hand-write one.
- **`run_cycles` identity is a stronger bit-exactness claim than
  `max_abs_lsb = 0`, and it is free:** the FSM taking the same number of
  clocks (525 88x at N = 1, 19 554 95x at N = 8, 12 098 425 on `cadwell_d64`)
  moves on any observable change in the arithmetic's shape; sixteen
  auxiliary pass lines byte-identical says the whole layer is unmoved.
  Quote it beside every re-gate.

### RF-path tells (2026-09-05, LNA-off day)

- **An unpowered bench LNA looks like this in the daemon's own journal:**
  the pilot bands sit FLAT for hours (L1C ~33 dB-Hz, L2C ~30 dB-Hz, ratio
  2.0–2.2) while C/A is silent or appears only in a narrow band at
  ~41.5 dB-Hz — that value is the 1 ms detection FLOOR as
  `ratio-model-inversion` reports it, not a real level — and the F9P's sky
  list shows every PRN at 0.0 dB-Hz. Powering the LNA lifts every band's
  maxima ~10 dB at once (C/A to ~51, L1C ~43, L2C ~46, L5 ~52). A "C/A
  came back" that sits at 41.5 is satellites grazing the floor, not gain.
- **The LNA supply is separate from the board's** (the F9P sits behind a
  DC block, so nothing else biases the antenna). After any board power
  event, check the LNA supply BEFORE trusting a sky number.
- **Sky statistics from an LNA-off window are weak-signal samples, never
  a baseline.** Tag them.
- **`journalctl -o short-iso` on this board prints BOARD-LOCAL time
  (UTC+1)**; `acq.json` `updated` is UTC. Convert before computing any
  seconds-to figure.
- **The manual-ssh lesson, re-learned 2026-09-05:** "host key changed" +
  "publickey denied" from a host shell were both host-side (a stale ECDSA
  `known_hosts` line; no key on the board). `platforms/tools/bench/ssh_run.py`
  is the reach of record; the board's ED25519 fingerprint is the one
  recorded under §Network connection of the zynq-boot chapter.

### The round's cost, measured (2026-09-07, from three 2026-09-06 sky-gate windows)

- **Sum the records before you name a cause.** Row 4b's "the deep C/A dwell
  consumed the round" was refuted by its own `job_wall_ms`: the L5 fold was
  485 s of a 509 s round, the C/A D = 8 dwell 8 s (30 ms per job vs 12 at
  D = 2). Cost model `T_round = T_between + Σ(wall + gap)`, every term a
  measured mean, reproduces a round to 0.4 % and a slice to 0.5 %.
- **A fold hypothesis rung is real engine time: `(4N+1)·(N+1)` SDF passes
  of `n + FLUSH_LEN` cycles at 100 MHz** — 297 passes = 194.8 ms predicted
  vs 199.5 ms measured at N = 8 (fed-tape ladder 7.6 / 20.8 / 59.0 /
  199.5 ms at N = 1/2/4/8). A 20-hypothesis sweep is ~20 s of engine time
  per PRN. The flush (`2^log2n − 1 + 3·log2n ≈ n`) is the common SDF core's
  own serialized-frame period by contract (`common_sdf_fft_pipe.vhd`
  header; measured: two gapless 16-point frames emit 17 of 32) — the engine
  cannot schedule it away; gapless frames are a core change.
- **The C/N0 estimator must be told the dwell count the HARDWARE summed**
  (`ratio_dwells` = `dwell_total` × any oracle accumulation), never the
  retired software accumulator's count. Told 1 for a D-epoch ratio it reads
  high by `10·log10(D·E_D/E_1)`: +1.8 dB at C/A D = 2, +4.7 dB at 8. The F9P
  offset moved −3.185 dB between a D = 2 and a D = 8 window (predicted
  −2.87); with the fix three windows agree to 0.2 dB at ≈ +1.2 dB (F9P −
  subject). A correct model handed the wrong argument is an instrument
  defect of the same class as a wrong model.
- **The daemon's own overhead dominated the baseline round (88 %):**
  0.222 s per job was two `write_status` builds of a 64 KB `acq.json`
  (`indent=2` alone 69 % of a publish; tmpfs was never the cost), and the
  5.2 s between rounds was the legacy C/A twin sweep on the old block
  (2.4 s one-row probes plus a 17 s 19-rung discovery every fifth round,
  `sweep_id` stepping on those rounds, no viewer connected). `utilization()`
  is not a feasibility number for a once-sorted serial round — the honest
  test is `round_period / min staleness budget` (4b: 6.08 on L2C while
  `utilization` read 0.25).
- **Model floors at the declare bar 2.0** (`Cn0FromRatio` inverted): C/A
  D = 1/2/8/256/512 → 41.3/39.4/36.1/31.6/31.1 dB-Hz (non-coherent gain
  saturates); L5 fold N = 8 → 33.6; L1C D = 1 (10 ms) → 32.6; L2C D = 1
  (20 ms) → 29.6. The D = 1 and D = 8 rows match the observed floors.
- **A dark census is per (band, D, N):** zero alarms in n jobs bounds the
  rate at 3.69/n per job (95 %); 1e-3 needs ≥ 3 700 clean jobs. The fold
  sweep tests 660 row-hypotheses per opportunity vs 5 for PCPS — on sky 2
  of 8 fold declares were noise at ratios 2.05–2.09, above the 283-job fed
  N = 8 ceiling (1.94).

## Hard-won facts (moved 2026-09-20)

Verbatim, moved from the project `CLAUDE.md` §Durable facts on 2026-09-20 (thread
`cross-cutting/20260920-orchestrator-context-diet`, plan-01 Step 3): the bench and
the board clock, the R5_1 attach chain and tick path, and what the declare stream
can and cannot tell you. `mmap.flush()` on `/dev/mem` is also in §Daemon above;
the bullet here adds the 2026-09-13 re-find.

- **A stub-linked ELF proves the linker script only as far as the stub reaches
  (measured 2026-09-12).** `rproc-link-check` substitutes its own `_startup`, so
  `xil-crt0.o` never joined the link and the whole crt0-to-linker-script contract
  was unexercised — three real link failures (`.sbss` symbols, `_init`/`_fini`
  under `-nostdlib`, a weak `_exit` behind newlib's in one archive pass) had been
  sitting behind a green placement check. A placement gate is not a link gate.
- **The bench is not a quiet room: it cycles 2.5 °C peak-to-peak every ~2040 s,
  indefinitely (measured 2026-09-12, 5400 samples at 1 Hz from a true cold
  start).** All four dies move together (PS against remote r = +0.983, PL +0.974,
  AD9081 +0.942) against per-sample sigma of 0.67 °C, so it is the room, not the
  sensors. The monotonic warm-up is over by ~800 s and then the board never
  settles — it oscillates. **Consequences: a "settled" criterion that waits for
  temperature to stop changing never fires here; the ruled 900 s fit window is
  44 % of the cycle, so two honest fits at different phases differ by the full
  amplitude, which is bias and does not average out; and every banked
  settled-drift number (+3.4, +2.28, −0.65, +0.426, the pooled +2.04 with the
  wrong sign) was taken over a window shorter than this cycle.** A drift number
  from this bench without its uptime AND its cycle phase is not comparable to any
  other. Die temperatures are a proxy for what the crystal sees, never its own.
  Evidence: appliance-ops `findings-2026-09-12-coldboot-testgps-thermal.md`.
- **The reference's DRIFT is a trajectory, not a constant, and it silently
  prices the scheduler (measured 2026-09-09).** The 122.88 MHz reference is an
  unovened free-running VCXO that follows bench temperature: about **+3.4 Hz/s
  at L1 forty minutes into a boot** while the board heats (servo estimate
  31985 → 34250 Hz over 660 s, with `warmup.state` already reporting
  `settled`), **+2.28 Hz/s** on another boot, and a slow **−0.65 Hz/s** tail six
  hours in. Code that assumes a settled clock pins `drift_hz_per_s` at 0.05,
  i.e. 13× low at six hours and 68× low at forty minutes. That term is not
  cosmetic: `budget_s = null_width_hz / (clock + geometry)` and the acquisition
  round fence's whole time budget is `min over bands of budget_s`, so a wrong
  drift silently shrinks or inflates every band's staleness budget AND the
  round's cap together (L2C ~12–40 s, not 84 s). Three consequences worth not
  re-deriving: **any design that stores drift as a configured constant is wrong
  by construction**; a board number without its uptime and measured drift beside
  it is not comparable to any other board number, and that need grows once the
  drift is wired live because the cap then MOVES during a run; and "oven warm"
  language removes only the positive heating term. The live slot exists —
  `drift_hz_per_s` tier 1 `measured_declares` — and was unwired as of
  2026-09-09.
- **`mmap.flush()` raises EINVAL on a `/dev/mem` mapping and is not a
  barrier (banked 2026-08-27, re-found 2026-09-13):** `msync` is invalid on
  device memory; the daemon's `DevMemOcmRegion.barrier()` called it anyway,
  so every seed publish would have died between the write and its readback.
  Never call `flush()` on `/dev/mem`; readback-verify every header word.
- **No drift fit before the board is settled by a MEASURED criterion
  (decision 166).** The daemon's pooled declare fit read +2.04 Hz/s with a
  220 Hz residual and the wrong sign against a servo that fell 977 Hz over the
  same row; the clean number was the servo endpoint slope (−0.43 Hz/s). A fit
  whose residual exceeds a rung-derived bound is not a measurement. The unit's
  `drift.json` keeps the settled-phase number until a cold-boot row measures
  the true worst case (queued).
- **R5_1's first daemon-owned cold attach on silicon refused (2026-09-15, shard
  7): the firmware halts in its init path before the status header** — the boot
  shim's second marker present (it is written BEFORE `b _startup`, so `xil-crt0.S`'s
  `__cpu_init`, `XTime_StartTimer`, MPU config, `__libc_init_array` and
  `Xil_ClockInit` and `main` itself are all UNPROVEN on silicon), `REG_SAMPLES_PER_MS`
  4096 (read by Linux, not by the firmware), the seed mailbox header
  present, the dump-capture header at `0x7900_0000` never written, the ring
  header stale, `epoch_irq_enable` 0, the boot-fault word 0. The header is
  published BEFORE the interrupt start (`r5_1_tracking_main.c:139-142`), so a
  missing header with a zero fault word is never an interrupt problem. `main`'s
  early returns and a `pl_fault` abort leave nothing Linux can read; the fake
  bus has no OCM, no DDR carve and a stubbed cycle counter, so it cannot see
  this class. Plan-07 instruments the path (sixteen OCM progress slots at
  `0xFFFE_4020`, slot = boot step, cleared by `main` before step 0; the abort
  record at `0xFFFE_4060`). OCM survives an R5_1 stop/start: a boot-page trail
  not cleared at boot is a previous boot's. **LOCATED 2026-09-16 (appliance-ops
  hop 6): slots `00`–`03` ok, `gps_tracker_init` pending, one UNDEFINED-instruction
  abort — the remoteproc boot shim never enables the FPU.** The BSP's `boot.S`
  (`standalone_v8_0/src/boot.S:228-239`) grants CPACR `cp10/cp11` and sets
  `FPEXC.EN`; a custom rproc shim that replaces `boot.S` must do both, or the
  first hard-float instruction (`-mfloat-abi=hard`) traps, including the ISR's
  own `vmrs FPEXC`. Host tests (FPU always on) and placement checks cannot see
  it; the R5_0 shim it was copied from never ran float code.
- **The R5_1 attach chain on silicon, 2026-09-16 (appliance-ops hops 7–9; arm
  plans 08/09, PL plan-37) — three more defects a clean desk hid, one class:**
  - **Hop 7: the status header left words unwritten.** R5_1 booted through its
    boot check (slots `00`–`0A` ok), and the daemon refused
    `reserved_geometry 0x910003fd`. The firmware's `gps_tracker_status_ring_init`
    never wrote `reserved_geometry` or `reserved_1..15`; the daemon's reference
    writer zeroes them and its validator checks one.
  - **Hop 8: the daemon trusted a leftover header.** It refused in the same
    second as `remoteproc1` start, on the PREVIOUS run's `magic`-valid header:
    `_wait_for_status_header` trusted `ready()` (`magic` only), and the stale
    guard covered only a nonzero head. Its exit then froze the C/A rail under
    R5_1's boot check, so R5_1 faulted `NO_TICK_WALL` (code 4) exactly as designed.
  - **Hop 9: the fix held.** With the daemon waiting for a changed
    `prod_generation`, **the first successful cold attach on silicon** (tracking
    active, coordinate valid).
  - **Why no desk gate saw them:** the fake bus's zero-initialized memory and a
    fake `remoteproc.start()` that published synchronously. **OCM and the carve
    are never zero at boot and a real writer is never synchronous:** a fake
    backing store must be POISONED (arm plan-09) and a fake writer must publish
    after the caller polls (PL plan-37), or the test proves the wrong contract.
    Same class as "one capture per job".
- **RESOLVED 2026-09-17 (was OPEN): a live R5_1 wedged an APU core within minutes of that first attach
  (2026-09-16, hop 9).**
  - **The symptom:** SSH logins died while ICMP and TCP/22 answered.
  - **The serial console** (`/dev/ttyUSB0`, 115200, the CP2108's first port) had
    a shell that echoed but ran nothing.
  - **Read-only Magic SysRq over a serial break** (`m`, `w`, `l`, `t`, with `8`
    first so the task dumps actually print; the default console level hides them
    and makes `w` look empty) showed:
    - no reset and healthy memory;
    - the daemon in D state in an AD9081 IIO read, behind the SPI controller's
      runtime-PM resume;
    - that `pm_runtime_work` "running" on CPU 3, which never answered a backtrace;
    - an expedited RCU grace period stalled, and systemd-logind blocked in
      `synchronize_rcu_expedited`, which kills logins.
  - **CAUSE CONFIRMED 2026-09-17: a memory overlap (arm plan-10's rank-1 candidate;
    tracking cache decision 189; proven by appliance-ops hop 10, where the relocated ring
    took 37,334 records in 30 min with no hang and BL31 code intact).** The boot image loads the
    ARM Trusted Firmware (BL31, 49,300 B) into OCM at `0xFFFEA000`, and R5_1's
    channel-status ring at `0xFFFED000` (8 KiB) lies inside it. Four banked board
    pre-states read BL31's instruction words at `0xFFFED000` before any R5_1
    write; the `reserved_geometry 0x910003fd` hop 7 refused on is BL31's
    `mov x29, sp`. Hop 9 was the first attach to write the record slots; CPU 3 then
    hung inside a firmware call. The BSP-to-PMU lead weakened: the ELF links no
    XPm/IPI client and the BSP defines no `XCLOCKING`. **Any OCM channel at or
    above `0xFFFEA000` writes into EL3 code**; no R5_1 attach until every channel
    is clear of it; the socks generator now refuses such a layout and the ring is
    at `0xFFFE5000`. Evidence: hop 9's `serial-console-20260916/`, plan-10's
    investigation, hop 10's `PROMOTED.md`.
  - **SysRq over the serial console is the diagnosis path when userspace is
    wedged but the kernel answers.**
- **Board-side bench traps (2026-09-16):** the board's `journalctl --since`
  rejects a `Z`-suffixed timestamp, and its Python predates 3.11, so
  `datetime.fromisoformat` rejects `short-iso`'s `+0100` (normalize to `+01:00`);
  both crashed attach-wait scripts after the attach had run. **And (2026-09-17):**
  the ZCU102's USB-UART bridge stays enumerated from USB power, so
  `/dev/ttyUSB*` survive a board power-off with their old device-node times and
  no host USB event. Their presence or age says nothing about board power or a
  reboot; read the board kernel's own timestamp (one misread became a phantom
  "self-reset" record the same morning).
- **The R5 ELF's hash is a per-build identity, never a reproducibility claim
  (2026-09-15):** `gps_firmware_identity.h` embeds the commit words AND
  `GPS_FW_BUILD_UNIX_TIME`, so two builds of one commit differ. Placement checks
  hash the placed file; source identity is the commit words.
- **The daemon's `engine.tracking.active` does not prove R5_1 is alive, and a
  null read off a dead core looks like a triumph (measured 2026-09-19,
  appliance-ops hops 11 and 13).** The flag stayed true while R5_1 sat halted at
  its boot check and while it sat wedged inside one ISR pass; a first "zero
  overruns at nine channels" was a halted core with the enable bit clear (the
  overrun counter only counts while `epoch_irq_enable` is 1). **A window counts
  only with, at both ends:** `REG_EPOCH_IRQ_ENABLE` (`0xA0003068`) = 1;
  boot-trail slot `0x0A` (`0xFFFE4048`) = `0x54330A01`; the capture header's
  firmware word (`0x79000038`) equal to the ELF under test; `serviced_ticks`
  (`g_epoch_irq` + `0x20`, APU `0xFFEB01F8` on today's ELFs) ADVANCING; and no
  `[EPOCH-IRQ-FAULT]` line on the serial console for that run — the cheapest
  tell of all. R5_1's BTCM is readable from Linux at `0xFFEB0000` while the core
  runs, which also gives the firmware's own `service_timing`/`append_timing`
  histograms (10 ns per tick, MEASURED from TTC3) with no firmware change.
- **The dump-capture header is a per-run object with three traps (2026-09-19):**
  it is written at the first `MSG_ASSIGN` of an R5_1 run, not at boot, so
  before the first declare it holds the previous run's image or power-on DDR;
  `write_count` is published only at freeze and reads 0 throughout ACTIVE; and
  the capture freezes at 90,000 epochs (`GPS_DUMP_CAPTURE_WINDOW_EPOCHS`) as
  well as at capacity. A capture-state read is valid only beside a magic of
  `0x47444331` taken in the same sample.
- **The R5_1 tick path, as measured on silicon (2026-09-19), and the class that
  produced it.**
  - The ISR's drain loop left only on FIFO empty, so at the twelve channels the
    daemon assigns (NOT two — every plan before this day assumed two) the core
    entered one pass and never returned: mailbox, status and every later tick
    dead while dumps drained. Fixed by arm plan-15 (bounded by the FIFO depth);
    ADR-034 clause 3 had already said it. The fake bus could not see it: it
    calls the service function once per epoch and its FIFO drops on overflow.
  - Bounded, the path cost **~425 µs per TRACKED dump** (two windows and the
    firmware's histograms agree within 1 %), of which the 26-transaction AXI-Lite
    drain PLUS the whole capture path is **17 µs**. An APU-side AXI-Lite read is
    ~0.15 µs. The extraction is not the bottleneck.
  - The ~410 µs was the pull-in test: in `PULL_IN`, once its window fills, the
    firmware recomputed a 500-sample median (`memcpy` + quickselect) on EVERY
    dump, in buffers that live in DDR — and **the remoteproc boot shim runs this
    core with the MPU and both caches OFF, so every DDR access is a single-beat
    bus transaction.** The golden runs the same algorithm on a cached host and
    never notices. Parity with the golden is a statement about VALUES, never
    about where they live or how often they are recomputed (the pairwise-sum
    lesson again). Arm plan-17 decides it by an exact running count.
  - It is self-sustaining: lost dumps keep a channel in pull-in, pull-in costs
    the median, the median saturates the ISR. And a declare that never locks
    held its slot forever — ADR-033 had no edge out (decision 193).
  - Per-dump costs quoted before this day (43 µs, 96 µs) were diluted by dumps
    the loop pops and discards for channels not yet tracking. A per-dump number
    without the channel STATES beside it is not a number.
- **A sysfs stop/start of `remoteproc1` faulted `NO_TICK` two of two on the
  wedging firmware and worked three of three on the bounded one (2026-09-19).**
  Linux's stop is a PMU force-power-down of the core alone; nothing resets the
  RPU GIC (shared with R5_0), B3's `epoch_irq_enable`/pending bits, or the DDR
  carve. The firmware now ends an inherited active INTID 121 at init and
  publishes what it found in boot-marker slot `0x0B` (`0x54330B00` = clean —
  NOT "pending"). The stale-interrupt hypothesis is UNTESTED: no stale state was
  ever found; what is established is that the fault went away with the wedge.
  **A Linux `reboot` resets the PL and gives R5_1 a clean start** (B3's
  AXI-reset-only counters restart); DDR and OCM across a reboot are unexamined.
- **Two firmware-build traps, each cost real time on 2026-09-19** (the day's
  third, an emitter trap, is in the threads skill's `references/orchestration.md`).
  The firmware
  Makefile never regenerates `build/rproc_tracking/r5_1_tracking_rproc.stripped.elf`
  — a stale 2026-09-13 file sits there; strip the fresh ELF yourself and check
  its segment sizes. The R5 object rule does not track flags, so a `-D` variant
  build silently relinks the previous objects.
- **THE RECEIVER CANNOT MEASURE ITS OWN ANTENNA FROM THE DECLARE STREAM, and a
  board leg that tries produces a confident wrong answer (measured 2026-09-20,
  appliance-ops hops 14 and 15).** Hop 14 concluded "no channel completes
  pull-in" — a receiver finding — on a board whose **antenna LNA was
  unpowered**, and nothing in its plan, its fourteen-finding pre-emission audit
  or its handback asked whether the RF plant was alive. Two later attempts to
  build a plant gate out of the declared-PRN set BOTH failed by construction,
  for one reason: **the acquisition detector's false-alarm rate is
  uncalibrated** (`threshold_num/den = 2/1`, which the project's own as-built
  audit calls *"the single most load-bearing acquisition number in the system,
  and the one with the weakest recorded basis"* — `claimed_basis: none for the
  value`), so it declares continuously whether or not anything is there. Two
  consequences that will recur until that bar is calibrated: **the declared-PRN
  set is CUMULATIVE per daemon process and never plateaus** (measured 16 → 20 →
  23 against daemon age, 30 of 32 on an older one, resetting to 3 on a daemon
  restart — it is a statement about uptime, not about the sky), and **a full
  twelve-channel pool does not suppress declares** (12 assigned while the set
  climbed 17 → 23). **The gate that DOES work is C/N0 with PLI beside it**, and
  the noise floor is now measured on this board: **~20 dB-Hz with
  `carrier_lock_pli` ≈ 0 and random sign**, against 35–45 dB-Hz with PLI
  climbing toward +1 for a real satellite. **PLI is the sharper half and costs
  nothing** — it rides at +36 in the same 64-byte status record as `cn0_dbhz` at
  +40, both `float32`. Any future plant check reads both, and reads them BEFORE
  believing any receiver conclusion.
- **The OCM channel-status ring is a rolling report HISTORY, not a per-channel
  array — and `seq` and `epoch` BOTH reset on every R5_1 boot (measured
  2026-09-20).** 64 slots written continuously, so one channel owns several
  slots and a channel can be absent entirely: a board snapshot held 64 records
  spanning only **nine** distinct channels with channels 9–11 missing, while
  twelve were assigned. **Anything counted must come from the latest record per
  channel**, never from raw slots. And the naive dedupe is worse than useless
  across a restart: `memset(tracker, 0, sizeof(*tracker))` (`gps_channel.c:1557`)
  zeroes `status_seq`, so after an R5_1 restart the FRESH records carry small
  `seq`/`epoch` while stale slots carry large ones and **"highest seq wins"
  selects the PREVIOUS run's records** — observed as 63 stale records beside a
  daemon reporting one assigned channel. Anchor on the slot the producer wrote
  last (`prod_head_lo` at the ring header +36) and accept only the cluster near
  its epoch; check `msg_type` (+0) so never-written slots do not read as a
  phantom channel 0; and the writer laps a slow reader with no per-slot seqlock,
  so re-read `seq` after a slot and discard on change.
- **The assignment hold's clock and the channel's progress toward lock are
  DIFFERENT CLOCKS, and they diverge exactly when dumps are lost (measured
  2026-09-20).** The status record's `epoch` is the FABRIC's count
  (`ch->epoch = dump->epoch`, `gps_channel.c:1283`, zeroed at channel start
  `:998`), but `pull_in_epochs` (`:1074`) and `tracking_epochs` (`:1125`)
  advance only on PROCESSED dumps. Under the 80 %-loss regime measured before
  plan-17, a 2000-epoch hold would have torn down **every** channel before any
  could lock. Plan-17 fixed the loss (zero drops and zero overruns across a
  600 s window on 2026-09-20), so the condition is satisfied today — but **the
  mechanism is permanent, so any hold counted in record epochs carries a
  loss≈0 precondition**, and the policy plane cannot see `pull_in_epochs`
  because it is not a field of `channel_status_msg_t`. Related: that `epoch`
  field is described THREE different ways across `seed_mailbox.h`,
  `seed_mailbox_layout.py` and `gps_channel.h`, and none matches the code.
- **`engine.offset_servo`, never `engine.servo` — and a reader of the wrong key
  returns `None` on every board, forever (measured 2026-09-20).** The daemon
  emits `offset_servo` (`acq_engine.py:14059`); a sampler reading
  `("servo", "estimate_hz")` produced a null that was reported up the chain as
  evidence of a dead plant, while the servo was in fact running with 502
  estimate points over 9,617 s. **A null from an instrument is not a measurement
  until the key is verified**, and note the servo proves nothing either way: a
  least-squares fit runs through noise declares as readily as through satellites.
