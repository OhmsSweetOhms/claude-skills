---
name: gps-design
description: "GPS L1 C/A receiver design, debug, and test for the gps_design project (Python block-level golden model -> bare-metal PS firmware -> Zynq PL VHDL). Use this skill whenever the user is working on the GPS receiver pipeline: acquisition (PCPS FFT), tracking loops (DLL/PLL/FLL with Kaplan coefficients), nav-bit extraction and subframe decode (LNAV), pseudorange anchoring and SV-transmit-time recovery, PVT solver (WLS + Cholesky), antenna geometry and link budget, or weak-signal cislunar extensions. Also triggers on debugging anchor drift, first-fix position error, sf_end_sample_idx attribution, preamble sync, dump_end_sample_idx timing, scenario_engine IQ generation, tx_time_offset_profiles, or the .research session directories for GPS receiver topology. The skill consolidates project-specific knowledge organized by receiver pipeline chapter -- each chapter is a reference file you load on demand. Apply this skill even when the user doesn't explicitly invoke 'GPS' by name, if they're touching any file under gps_receiver/, gps_iq_gen/, or scenario_engine/."
---

# GPS Design -- L1 C/A Receiver for the gps_design Project

Project-specific knowledge for the GPS L1 C/A receiver under
`/media/doogie/Work1/Claude/work/gps_design/`. The receiver is a
three-tier pipeline:

1. **Python golden model** (`gps_receiver/`) -- block-level behavioral
   spec for every FPGA/PS block. All arithmetic is floating-point.
2. **Bare-metal C firmware** (`gps_receiver/firmware/`, planned) --
   ports the PS.B4-B12 blocks to the Zynq PS (no-OS, deterministic 1 ms ISR).
3. **VHDL on Zynq PL** (via the SOCKS skill, planned) -- AD9361
   front-end, decimation, dynamic bit select, correlator engine, PCPS
   acquisition.

This skill covers the design, debug, and test of those three tiers as a
single coherent pipeline. The receiver is intentionally split by
**block ID** (PL.B1-B3 for PL blocks, PS.B4-B13 for PS blocks), and
every .json/.py/.vhd artifact references those IDs.

See the project's `CLAUDE.md` for the big-picture architecture. This
skill is the technical reference for how to do work on specific
chapters of the pipeline.

---

## How to Use This Skill

The skill is organized by receiver pipeline chapter. Each chapter is a
separate reference file. Read the chapter relevant to the task at hand;
don't read all of them. If the task spans multiple chapters, read them
in the order the data flows through.

### Chapter index

| Chapter | Reference | Status | Covers |
|---------|-----------|--------|--------|
| Tracking loops | `references/gps-tracking.md` | **Mature** | DLL/PLL/FLL, Kaplan 3rd-order, Costas, M2M4, NBPW, PLI, state machine |
| Pseudorange anchoring | `references/pseudorange-anchoring.md` | **Mature** | PS.B10a/B10/B11/B13 anchor chain, SoftGNSS pattern, three-way debug methodology |
| Acquisition | `references/gps-acquisition.md` | Stub | PCPS FFT, peak1/peak2, doppler/code-phase search |
| Nav decode | `references/gps-nav-decode.md` | Stub | LNAV subframe parity, HOW/TOW, ephemeris extraction |
| PVT solver | `references/gps-pvt.md` | Stub | WLS + Cholesky, satpos, clock bias, iteration convergence |
| Antenna geometry | `references/gps-antenna-geometry.md` | Stub | Link budget, off-boresight angle, occultation, cislunar dynamics |

### Diagnostic scripts

`scripts/` bundles three template diagnostics built during the
pseudorange-anchoring debug. Each is a *pattern*, not a drop-in:

- `diagnose_anchor_truth.py` -- harness for running a scenario end-to-end
  and extracting per-SV anchor residuals with an oracle ground-truth
  comparison. Supports cursor-path and fixed-block paths via
  `--fixed-block`. Use `ANCHOR_DECOMP=1` env var to trigger the
  per-anchor-event decomposition table.
- `diagnose_anchor_terms.py` -- three-way comparison (claimed / iqgen /
  oracle) that attributes residuals to specific physics terms (SV clock
  bias, Sagnac, light-time). Use this when the receiver disagrees with
  the oracle and you need to identify whether the bug is in the
  receiver, the IQ generator, or the oracle itself.
- `diagnose_preamble_alignment.py` -- decomposes preamble lock residuals
  into integer-20-ms, integer-1-ms, and sub-ms components. Use this to
  localize preamble-sync mechanics vs downstream bit-chain bugs.

These scripts were written against this project's specific layout.
When adapting to other scenarios, preserve the three-column (receiver
claim / IQ truth / oracle truth) pattern -- it's the methodological
contribution, not the specific code.

---

## The Three-Way Diagnostic Pattern

This project's hardest bugs live at the boundary between blocks, not
inside them. The debug pattern that consistently localizes them is:

**Compare the receiver's claim against two independent physical-truth
sources.** If the two truths agree and the receiver disagrees, the bug
is in the receiver. If the truths disagree, the bug is in the
IQ-generator-vs-oracle convention. If all three disagree, chase the
closest anomaly first.

Concretely, for timing work:

1. `claimed_sv` -- what the receiver says SV-time is at a specific
   rx-sample (extracted from `anchor_events` or equivalent).
2. `iqgen_sv` -- what the IQ generator actually embedded into the
   synthesized signal at that rx-sample. This is the ground-truth the
   receiver *should* recover, because it's what was in the input IQ by
   construction (`gps_scenario.py:_build_scenario_profiles`).
3. `oracle_sv` -- what a fully physics-correct computation says SV-time
   is at that rx-sample. For this project,
   `diagnose_anchor_truth.py:_iterated_truth` uses light-time-iterated
   range + Sagnac rotation + SV clock bias.

If `oracle_sv == iqgen_sv`, then the IQ gen's model is already
physics-correct for this scenario class (static ground RX, idealised
constellation). Don't "fix" the IQ gen without independent evidence.

If `oracle_sv != iqgen_sv`, you've found a convention difference
between the ground-truth oracle and the IQ generator. Resolve by
checking whether the IQ gen models the physics you're oracle-checking
(e.g., SV clock bias in `make_24sv_constellation` is ~10 ns; Sagnac for
a ground receiver is ~100 ns -- neither is in the Euclidean-range-
at-rx-time embedding, but in this scenario they cancel).

See `references/pseudorange-anchoring.md` for a worked example where
this pattern refuted three consecutive hypotheses and ultimately
localized the bug to a SoftGNSS-vs-our-receiver anchor convention
mismatch.

---

## The JSON Spec Stack

Four JSON files form the configuration contract. Their content is the
single source of truth for block structure, parameters, telemetry, and
test scenarios. Code reads from these at runtime; don't hardcode
values that should come from profiles.

```
tracking-mode-profiles.v1.json       <- "test this scenario"
  |-- iq_gen_defaults                -> gps_iq_gen satellite config
  +-- receiver_profile: "open_sky"   -> receiver-block-profiles.v1.json
        +-- per-block params         -> shared-interfaces.v1.json
              +-- telemetry signals  -> monitor-signals.json
```

| File | Purpose | Consumed By |
|------|---------|-------------|
| `shared-interfaces.v1.json` | Block IDs, Python module/class, I/O contracts, transport | Code structure, tests, FPGA build |
| `receiver-block-profiles.v1.json` | Runtime parameters per profile per block | `GPSReceiver(profile=...)` at init |
| `monitor-signals.json` | Telemetry signals with types, rates, `source_block` attribution | Telemetry frame packing, viewer, tests |
| `tracking-mode-profiles.v1.json` | Operational scenarios mapping dynamics envelopes -> IQ gen config + receiver profile | End-to-end test fixtures |

### Invariants

- Parameter names in `receiver-block-profiles.v1.json` must match
  `.get()` keys in `receiver.py`. If you add a parameter, wire both.
- Block IDs (`PL.B1`, `PS.B4`, etc.) are canonical across all four
  files. Don't invent new IDs without updating all files.
- `tracking-mode-profiles.v1.json`'s `receiver_profile` values are
  keys into `receiver-block-profiles.v1.json`'s `profiles`. `null`
  means IQ generation only (no matched receiver config).

---

## Research Sessions

The project has a rich local research directory at
`.research/session-<date>-<time>/`. These are cross-validated notes
from specific investigations. **Read them before making nontrivial
changes to a block's topology** -- six production implementations have
been extracted and compared, and the existing choices are backed by
that consensus.

Key sessions:

| Session | Focus | Key Content |
|---------|-------|-------------|
| session-20260405-140000 (A) | PS.B7-B8 | FLL discriminator, PLL coefficients, anti-windup gap |
| session-20260405-150000 (B) | PS.B4-B6, B9 | DLL/PLL discriminator consensus, C/N0 M2M4, lock |
| session-20260405-160000 (PL) | PL.B1-B3 | Quantization, PCPS, correlator architecture |
| session-20260405-170000 (C) | All | Cross-implementation synthesis |
| session-20260414-132039 | Anchor timing | SoftGNSS preamble-position anchor; IS-GPS-200 TOW convention |
| session-20260322-142449 | Acquisition | Leclere comparison, PCPS vs serial |

Per CLAUDE.md preference: **scan local `.research/` directories before
external queries.** Many questions have already been investigated; the
answers are in those directories, not elsewhere.

---

## Key Architecture Decisions

These are the project-level constants. Changing them requires
architectural review, not a block-level tweak.

- **Sample rate:** 61.44 MSPS at AD9361 -> /15 decimation -> 4.096 MSPS
  (4096 samples per 1 ms code period = 2^12, natural FFT size).
- **Data format:** 12-bit I/Q sign-extended to 16-bit (int16 containers).
- **Quantization:** Dynamic bit-select 12 -> 4 bit (literal bit-slice,
  NOT Lloyd-Max).
- **Correlator input:** 4-bit default, 2-bit optional (0.55 dB loss per
  Hegarty 2011).
- **PS software:** Bare-metal (not Linux) for deterministic 1 ms ISR.
- **Tracking on PS:** Floating-point, 1 kHz update, <1% of one A9 core.
- **Noise model:** Fixed absolute `noise_rms_dbfs` relative to
  `adc_full_scale`.
- **Carrier method:** `fll_assisted_hard_switch` -- FLL pull-in only,
  PLL steady-state.
- **Ethernet transport:** TCP/IP only. Python = client, FPGA lwIP = server.
- **Serial fallback:** `telemetry_control` over USART via
  `socks/modules/usart`.

---

## Conventions

- **Units in variable names:** `doppler_hz`, `code_phase_chips`,
  `cn0_dbhz`. The type-and-unit suffix is load-bearing -- don't drop
  it when renaming.
- **Physical constants in `constants.py`**, never hardcoded inline.
- **Deterministic RNG:** `np.random.default_rng(seed)` everywhere.
  Never use `np.random.*` module-level functions.
- **Block IDs from `shared-interfaces.v1.json` are canonical
  everywhere.**
- **No matplotlib dependency in generator or receiver** -- visualization
  is the sdr-viewer's responsibility.
- **Use `np.floor()` for chip indexing (not round)**, matching
  `gps_iq_gen`.
- **Code phase convention:** Generator uses `code_phase_chips` as a
  forward offset. Receiver acquisition returns the
  complement-converted value. Tracking `set_initial()` expects the
  acquisition output directly.
- **DLL feedback sign is negated:** when the code is behind the
  signal, L > E, discriminator < 0. The code rate correction must be
  SUBTRACTED to speed up the code.

---

## When Debugging

Follow this ordering before making changes:

1. **Read the relevant chapter reference** (`references/gps-*.md`).
2. **Scan `.research/` for prior investigation.**
3. **Run a diagnostic script from `scripts/`** appropriate to the
   symptom. Don't write a new diagnostic before trying the existing
   three-way methodology.
4. **Check the JSON spec stack** -- is the parameter actually wired from
   JSON to code, or is there a silent rename / missing `.get()`?
5. **If the bug looks like a between-blocks issue** (timing,
   convention, polarity): apply the three-way diagnostic pattern.
   Document the prediction BEFORE running it, so a refuted prediction
   becomes a finding in itself.
6. **Instrument first, read code second** once top-down reasoning has
   refuted a hypothesis. The bug is where you don't expect it.

See `references/pseudorange-anchoring.md` for a case study where this
ordering saved a costly wrong fix.

---

## Related Skills

- **`control-loops`** -- general (provider-neutral) digital PLL/FLL/DLL
  design, bilinear-z discretization, NCO convention, 2nd/3rd-order
  filter math, lock detection theory. This skill reads `control-loops`
  when you need the general mathematics; `control-loops` delegates
  GPS-specific details back to this skill's `references/gps-tracking.md`.
- **`socks`** -- System-On-a-Chip Kit for Synthesis. The FPGA build and
  HIL test pipeline for the PL blocks once they're ported to VHDL.
- **`research`** -- structured technical research agent. Use this when a
  question can't be answered from the existing `.research/` sessions
  and needs external literature / reference-code scanning.
