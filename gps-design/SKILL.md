---
name: gps-design
description: "GPS L1 C/A receiver design, debug, and test for the gps_design project (Python block-level golden model -> bare-metal PS firmware -> Zynq PL VHDL). Use this skill whenever the user is working on the GPS receiver pipeline: acquisition (PCPS FFT), tracking loops (DLL/PLL/FLL with Kaplan coefficients), nav-bit extraction and subframe decode (LNAV), pseudorange anchoring and SV-transmit-time recovery, PVT solver (WLS + Cholesky), antenna geometry and link budget, AD9986/AD9081 front-end NCO/JESD profile planning, L1C / GPS III signal work (TMBOC, L1C-D/L1C-P/L1C-O overlay, IS-GPS-800, compose_l1_joint, delta-rho experiments, the dual-rate decimator joint chain), or weak-signal cislunar extensions. Also triggers on debugging anchor drift, first-fix position error, sf_end_sample_idx attribution, preamble sync, dump_end_sample_idx timing, scenario_engine IQ generation, tx_time_offset_profiles, ZCU102 AD9986 profile work, regenerating the docs/json-structure spec-stack HTML atlas (tools/build_json_atlas.py) or the docs/results-dashboard run-results dashboard (tools/build_results.py), creating and running a scenario (scenario_engine/scenarios/*.v2.json) and capturing its outcome to the dashboard (outputs.results sink, gps_scenario.py --stream-replay), or the .research session directories for GPS receiver topology. The skill consolidates project-specific knowledge organized by receiver pipeline chapter -- each chapter is a reference file you load on demand. Apply this skill even when the user doesn't explicitly invoke 'GPS' by name, if they're touching any file under gps_receiver/, gps_iq_gen/, scenario_engine/, or the AD9986/ZCU102 GPS streaming profiles."
---

# GPS Design -- L1 C/A Receiver for the gps_design Project

Project-specific knowledge for the GPS L1 C/A receiver under the
`gps_design/` workspace (path varies by host). The receiver is a
three-tier pipeline:

1. **Python golden model** (`gps_receiver/`) -- block-level behavioral
   spec for every FPGA/PS block. All arithmetic is floating-point.
2. **Bare-metal C firmware** (`gps_receiver/firmware/`, planned) --
   ports the PS blocks (B4-B9, TLM, B12-B13) to the Zynq PS (no-OS,
   deterministic 1 ms ISR).
3. **VHDL on Zynq PL** (via the SOCKS skill) -- front-end (active:
   ZCU102 + AD9986 JESD204B per ADR-007; AD9361 survives as the
   divergent small-fabric target), rate conversion, dynamic bit
   select, correlator engine + bit-sync histogram, PCPS acquisition.

This skill covers the design, debug, and test of those three tiers as a
single coherent pipeline. The receiver is intentionally split by
**block ID** and every .json/.py/.vhd artifact references those IDs:

- **PL blocks:** PL.B1 (bit select), PL.B2 (acquisition), PL.B3
  (correlator), PL.B3a (bit-sync histogram).
- **PS blocks:** PS.B4–B9 (DLL / PLL / FLL / C-N0 / lock), PS.TLM
  (telemetry decoder — bit-sync + preamble + TLM/HOW + parity +
  SF1/2/3 ephemeris decode), PS.B12 (PVT), PS.B12a (RAIM), PS.B13
  (Observables).
- **L1C block family** (separate 20.48 MSPS family, NOT parameterized
  C/A variants): PL.B2_L1C, PL.B3_L1C, PS.B4_L1C–B9_L1C, PS.B13_L1C
  — see `references/gps-l1c.md`.

See `gps_receiver/CLAUDE.md`, `shared-interfaces.json`, and
`blocks_map.json` for the authoritative current block inventory.

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
| Tracking loops | `references/gps-tracking.md` | Current | DLL/PLL/FLL, Kaplan 3rd-order, Costas, M2M4, NBPW, PLI, state machine |
| Pseudorange anchoring | `references/pseudorange-anchoring.md` | Current | IS-GPS-200 TOW convention, three-way debug methodology, PS.TLM → PS.B13 chain pointers |
| Acquisition | `references/gps-acquisition.md` | Current | PCPS pipeline, same-row peak1/peak2 semantics, r2/r22 fixed-point schedules (one golden per RTL config), vector-authority rules, B2→PS→B3 rational seed handoff, Doppler ceiling / code-Doppler smear |
| Nav decode | `references/gps-nav-decode.md` | Current | PS.TLM LNAV semantics, TOW forward-projection, TOW continuity gate |
| L1C (GPS III) | `references/gps-l1c.md` | Current | L1C-D/P/O structure + code port, TV-profile generator semantics (C/A authority mapping), Phase-A receive chain, mid-stream bring-up recipe (carrier/overlay seeding, epoch alignment), Δρ methodology + measurement floors, ADR-023 dual-rate joint chain (decimator /5 + group-delay constant + cursor-path L1C) |
| PVT solver | `references/gps-pvt.md` | Current | PS.B12 WLS + Cholesky, PVTFix contract, firmware-port notes |
| Antenna geometry | `references/gps-antenna-geometry.md` | Stub | Link budget, off-boresight angle, occultation, cislunar dynamics |
| FPGA PL bring-up | `references/fpga-pl-bringup.md` | Current | ADR-007 substrate (ZCU102/AD9986 txm8l4: RX 20.48 MSPS native, C/A via /5, L1C passthrough; AD9361 divergent target), parametric (×N, /M) rate-conversion ladder + F_DECINT_5 authority, PL block inventory (R2²SDF PL.B2, fixed-point vector authorities), the 4-stage verification gate stack (bit-exact xsim → IP-boundary handshake gate → interface-integrity gate → HIL), HIL-as-system substrate pattern, per-block durable homes + SOCKS conventions |
| AD9986 GPS NCO planning | `references/ad9986-gps-nco-frequency-planning.md` | Current | 3.93216 GHz clean converter-clock math, L1/L2/L5/Iridium NCO plans, RX CDDC/FDDC vs TX CDUC/FDUC placement, JESD/profile validation checklist |
| Adding a scenario **& running it** | `references/adding-a-scenario.md` | Current | Author a `scenario_engine/scenarios/*.v2.json` root (`signal_source` branches, platform/entity/`receiver_profile`/iq_gen/outputs blocks), validate, register a mode, regen the atlas — **then run it and capture results to the dashboard** (`outputs.results` sink, `--stream-replay` for heavy runs, `tools/build_results.py`). Read this whenever the user asks to create/run a scenario or see its results. |
| Using the Cesium viewer | `references/using-the-cesium-viewer.md` | Current | The viewer's **payload-field → render contract** (`window.RESULTS.runs[]` schema v2; every optional block is feature-detected, absence = silent no-op), `?run=<run_id>` stable addressing, the `resolvePlatform()` name-resolution chain (`run.mode` → mode registry → scenario `.v2` → platform name/antennas; failure = generic "rx" label), taxonomy-axis → viewer-control mapping (`docs/scenario-taxonomy.md`), and the off-pipeline-payload anti-pattern (always emit via `gps_scenario._build_results_payload` / `_build_interference_geometry`). Read when a run renders wrong/differently in the viewer or when authoring a scenario that must render in both dashboard and viewer. |
| Ingesting a foreign capture | `references/ingesting-foreign-iq.md` | Current | Rare one-off: land an **outside-source** raw IQ capture (foreign rate / bits / layout / center) on our 4.096 (or 49.152) rail and run the receiver/analysis on it. `read_iq_raw` → `to_receiver_rail` (exact-rational `resample_poly`, optional `f_shift_hz`) → cold-start `GPSReceiver` → C/N0 / `compare_rails` / `detect_interference`. Uncalibrated-amplitude + onset-detector + don't-commit-raw-IQ caveats. NOT for our own captures (those choose a clean rate → on-rail `.iq16`). |

For project-wide thread state across all active work, the
auto-generated registry `.threads/threads.json` is the source of
truth (regenerate/summarize with
`python3 ~/.claude/skills/threads/scripts/index_threads_research.py --print`);
the threads skill's **Status review** workflow produces the triage
snapshot. (The old `gps_receiver/threads/` layout and its weekly
`tiered-execution-flow.md` are retired — threads live at the repo
root under `.threads/`.)

### Diagnostic scripts

**Current-architecture diagnostics live in the project**, under
`diagnostics/` at the repo root and
`.threads/<subsystem>/<slug>/diagnostics/`. See
`scripts/README.md` for pointers to the highest-value examples
(`diagnose_tow_label_timing.py`, `attribute_step6_observables_residual.py`,
`gen_baseline_lnav.py`, etc.).

The three-way methodology those diagnostics embody is written up in
`references/pseudorange-anchoring.md` §3. New diagnostics should be
written under the relevant project thread, not bundled into this
skill.

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
   is at that rx-sample. Canonical form: light-time-iterated range +
   Sagnac rotation + SV clock bias via `scenario_engine.propagate_sv`.
   See `references/pseudorange-anchoring.md` §3 for the snippet and
   the live diagnostics under
   `.threads/receiver/*/diagnostics/` for concrete use.

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

The stack pivoted in 2026-05 (plan-02 of the receiver-consolidation
sprint): a v2 scenario JSON at `scenario_engine/scenarios/<name>.v2.json`
is now the **single root of truth** for an end-to-end run. It carries the
receiver location, platform, entities, environment, and the `iq_gen` block
(cn0, doppler envelope, duration, noise, dynamics) that used to live inline
in `tracking-mode-profiles.json`. Modes in
`tracking-mode-profiles.json` are now **thin pointers**:
`{scenario_root, diagnostic_overlay?, notes}`. Code reads from these at
runtime; don't hardcode values that belong in a scenario or profile.

```
tracking-mode-profiles.json        thin-pointer registry ("test this scenario")
  └─ scenario_root: "<name>.v2.json"  ← single root of truth for the run
       platforms.<p>.{trajectory, antennas}
       entities.<e>.{platform, antenna, receiver_profile, kind}
       environment.{elevation_mask_deg, t_sys_k, cn0_floor_dbhz}
       iq_gen.{doppler_type, nav_data, duration_s, noise_model, ...}
         └─ receiver_profile (string)  → receiver-block-profiles.json
              └─ per-block params       → shared-interfaces.json
                   └─ telemetry signals → monitor-signals.json
  validated by:   scenario_engine/schemas/scenario.schema.json (Draft-07)
  physics inputs: rf.json (GPS tx + propagation), receiver.json (hardware)
```

| File | Purpose | Consumed By |
|------|---------|-------------|
| `scenario_engine/scenarios/*.v2.json` | **Scenario root**: platform/entity/environment/iq_gen/outputs(/targets). Single source of truth per run. | `gps_scenario.load_scenario_root_v2`, RINEX export |
| `scenario_engine/schemas/scenario.schema.json` | Draft-07 validator for the v2 shape (conditional per `signal_source`: synthetic / test_24sv / rinex / live). | `gps_scenario._validate_v2_scenario` |
| `tracking-mode-profiles.json` | Thin-pointer mode registry: `{scenario_root, diagnostic_overlay?, notes}`. | `gps_scenario.build_config(mode)` |
| `receiver-block-profiles.json` | Runtime parameters per profile per block (open_sky, urban, high_dynamic, indoor, cislunar). | `GPSReceiver(profile=...)` at init |
| `shared-interfaces.json` | Block IDs, Python module/class, I/O contracts, transport. | Code structure, tests, FPGA build |
| `monitor-signals.json` | Telemetry signals with types, rates, `source_block` attribution. | Telemetry frame packing, viewer, tests |
| `rf.json` / `receiver.json` | GPS tx + propagation physics / receiver hardware (antenna, AD9361, decimation, bit-select). | `scenario_engine` link budget + synthesis |

### Invariants

- Parameter names in `receiver-block-profiles.json` must match
  `.get()` keys in `receiver.py`. If you add a parameter, wire both.
- Block IDs (`PL.B1`, `PS.B4`, etc.) are canonical across all the files.
  Don't invent new IDs without updating all of them.
- A scenario binds its receiver via `entities.<e>.receiver_profile` (a
  string key into `receiver-block-profiles.json::profiles`) — **not**
  via `tracking-mode-profiles`. For pure IQ generation with no matched
  receiver config (e.g. `extreme_dynamic`), omit the entity entirely
  under `signal_source: synthetic` (the old `receiver_profile: null`
  convention is gone).
- Adding a scenario = write `scenario_engine/scenarios/<name>.v2.json`,
  validate it (`tests.test_scenario_root_schema` auto-discovers any
  `*.v2.json`), then optionally add a thin-pointer mode — or load it
  directly with `--scenario-root <path>`.
- "Profile" is overloaded: `tracking-mode-profiles` are **selectors**
  above the scenario (a mode just points at a `.v2`), while
  `receiver-block-profiles` are **receiver tuning** the scenario binds
  below it. The scenario is the hub, not either profile file.

### Visualizing & regenerating the spec stack (HTML atlas)

`docs/json-structure/index.html` is a browser view of the spec stack —
three tabs: **Module Map** (scenario `.v2.json` = root of truth),
**Schema Explorer** (nested `.v2` shape), **Scenarios** (inventory of
every `scenario_engine/scenarios/*.v2.json`, with mode/targets/orphan
flags). The data that churns (scenario inventory, per-file counts, drift
flags) is **generated**, not hand-coded.

**When a user asks to "regen the project JSON HTML":**

```bash
python3 tools/build_json_atlas.py        # rewrite docs/json-structure/atlas-data.js
# then reload docs/json-structure/index.html in a browser
```

The page reads the generated `atlas-data.js` (`window.ATLAS`) on load —
the HTML *structure* isn't rebuilt, only the data. Re-run after
adding/removing/editing any scenario `.v2.json` or changing config-file
counts (modes, profiles, blocks). Variants:

- `--print` — also dump the spec-stack drift audit (orphans, missing
  `targets`, three-way version drift, stale rationales). This is the
  **canonical drift-walker** (it replaced a standalone diagnostic).
- `--check` — exit 1 if `atlas-data.js` is stale; use as a pre-commit/CI gate.

Runs from any cwd (resolves the repo root from its own location). The
curated module-map **SVG layout** and the schema **teaching tree** are
hand-authored in `index.html` and are NOT regenerated — changing those is
a manual edit. Pipeline: `spec/spec-manifest.json` → `tools/build_json_atlas.py`
→ `docs/json-structure/atlas-data.js` → `index.html`. See
`docs/json-structure/README.md`.

**Count labels are data-driven (the layout is curated, the numbers are
not hand-typed).** The SVG boxes and schema tree are hand-authored, but
the counts inside them — Scenarios tab `(N)`, the intro count + `n/N`
targets ratio, the schema `targets` note, the v2 click-detail
"N shipped files" — are populated from `window.ATLAS` on load. They were
hand-typed once and drifted (`22` vs `23`) when a scenario landed, so
they were wired to the data. **Never re-introduce a hand-typed scenario
count in `index.html`**; `tests/test_json_atlas_labels.py` guards it
(Pattern C, browser-rendered, skips without Playwright — see
`docs/test-conventions.md`).

**Inspecting the rendered page — you cannot see a browser, and reading
the HTML source tells you nothing** (the tables and count labels are
built by JS on load). Render it instead:

```bash
# pixels: full-page render at an exact size (no display server, no crop)
google-chrome --headless=new --disable-gpu --hide-scrollbars \
  --virtual-time-budget=3000 --window-size=1400,2400 \
  --screenshot=/tmp/atlas.png "file://$(pwd)/docs/json-structure/index.html"
# content: dump the post-JS DOM and grep what actually rendered
google-chrome --headless=new --dump-dom --virtual-time-budget=3000 \
  "file://$(pwd)/docs/json-structure/index.html"
```

The headless screenshot only shows the default **Module Map** tab. To
capture **Schema Explorer** / **Scenarios** (each needs a click) or to
assert that rendered text equals the data, drive Playwright: click
`.tabs button[data-pane="scenarios"]`, `wait_for_function` on
`window.ATLAS`, read counts via `page.evaluate`, and screenshot the
active `.pane`. This is exactly how `tests/test_json_atlas_labels.py`
verifies the labels.

### Results dashboard (run outcomes)

The atlas above visualizes the scenario *config*; the **results dashboard**
(`docs/results-dashboard/index.html`) visualizes a run's *outcome* — PVT
accuracy vs engine truth, satellites tracked + health, C/N0 over time, an az/el
skyplot, and a drag/playback 3D track. Same generated-data pattern as the
atlas: the page reads `results-data.js` (`window.RESULTS`) on load; the HTML is
hand-authored, the data is generated. Full create→run→view how-to is
`references/adding-a-scenario.md` §6.

**When a user asks to run a scenario and see its results:**

1. **Run it** — every run emits a results JSON via the `outputs.results` sink
   (default on), serialized by `gps_scenario._build_results_payload` through the
   one `_emit_sidecar` writer (you don't write a serializer):
   - light / short (in-RAM): `gps_scenario.py --scenario-root <…>.v2.json --output /tmp/run.iq16` → `/tmp/run.iq16.results.json`.
   - heavy / long (in-RAM OOMs — IQ is 16 B/sample): `gps_scenario.py --mode <m> --duration <N> --stream-replay` → `results/<scenario-slug>_<N>s.results.json` (memory-bounded streaming IQ→disk + chunked receiver replay via the `run_receiver_on_iq_file` primitive; explicit, warm-start only).
2. **Aggregate + view:** `python3 tools/build_results.py` (`results/*.results.json` → `results-data.js`; `--check` is the staleness gate, like `build_json_atlas.py --check`), then reload `index.html`.

`results/` is the canonical **committed** store (small durable JSONs; the bulky
`.iq16` stays in gitignored `/temp/`). Panels are richest for `test_24sv` +
`nav_data=subframe` (engine truth + PVT); `all_ones` runs gray out the PVT
panels, synthetic `--prn` runs have no 3D-track/skyplot truth. Render test:
`tests/test_results_dashboard.py` (Playwright, system python3, auto-covers every
run in the store). Don't hand-edit `results-data.js`. Pipeline:
`results/*.results.json` → `tools/build_results.py` → `results-data.js` →
`index.html` (thread `cross-cutting/20260524-results-dashboard`).

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
| session-20260704-080759 | Acquisition | Leclere FFT-acquisition corpus (re-banked; the original session-20260322-142449 is missing on disk) + the 204800-pt L1C PCPS architecture trade (x8/25 -> 2^16, BOC(1,1)-only replica) |

Per CLAUDE.md preference: **scan local `.research/` directories before
external queries.** Many questions have already been investigated; the
answers are in those directories, not elsewhere.

---

## Key Architecture Decisions

These are the project-level constants. Changing them requires
architectural review, not a block-level tweak.

- **Sample rate:** GPS C/A app boundary is 4.096 MSPS (4096 samples
  per 1 ms code period = 2^12, natural FFT size). Active substrate
  (ADR-007, `2048-quad-band-txm8l4`): RX 20.48 MSPS native — L1C
  consumes it as passthrough, C/A via the /5 decimator tap; TX via
  the parametric (×N, /M) PL.INTERPOLATOR/PL.DECIMATOR ladder. The
  historical clean-6144 ladder (61.44 MSPS, /15//30 cascades) is
  archaeology, not the active path — check `docs/decision-log.md`
  ADR-007 before trusting any rate figure. AD9361 (61.44 → /15)
  survives as the divergent small-fabric target. See
  `references/ad9986-gps-nco-frequency-planning.md`.
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
- **Block IDs from `shared-interfaces.json` are canonical
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
3. **Run an existing diagnostic from the project**
   (`.threads/receiver/*/diagnostics/`) before writing a
   new one. The three-way methodology is embodied in several of them.
   See `scripts/README.md` for the current-architecture pointers.
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

## Verification Rules of Thumb (hard-won)

Cross-cutting principles distilled from this project's gate failures.
Each has a concrete incident behind it; none is hypothetical.

1. **A gate that passes on the first try should make you suspicious —
   pick gate parameters adversarially against the implementation's
   structure.** Every latent defect found in the L1C TV-profile hop
   was invisible to the gate that nominally covered it, because the
   driver sat in a structural blind spot: 100 ms chunks masked a
   per-chunk code restart (a mod-10230 identity), tx_offset=0 masked
   a TOW-anchor assumption, stream-start masked an overlay-hypothesis
   default. Choose the chunk size that is NOT a multiple of the code
   period, the offset that is NOT zero, the start that is NOT sample
   zero. "Green on the natural configuration" and "correct" are
   different claims.

2. **For coherence-class experiments, the measurement chain is the
   project, not the device under test.** Expect most of the debug
   budget to go to the harness (all five defects behind the
   moving-profile Δρ gate were measurement-chain; zero were in the
   code under test). Harness fixes compound: moving the Δρ driver
   onto the cursor path cut the C/A floor from ~10 m to 1.5 m for
   every future experiment.

3. **Constants are artifacts; slopes are physics.** Before touching
   code, run the gate at two operating points and fit the residual's
   dependence on the parameter. A residual identical across operating
   points (−6.13 m at 0 m/s vs −6.14 m at 300 m/s) is driver
   construction; an error slope that equals a physical rate by
   construction (exactly −range-rate) names the mechanism outright
   (rate-blind labels). See also the three-way pattern above — this
   is its cheap two-point cousin.

4. **Designate a semantics authority and match it exactly — including
   at its limits.** When extending a path that mirrors an existing
   one (L1C mirroring C/A, RTL mirroring a golden), read the
   authority first, replicate its op chain verbatim, and where the
   authority itself is imperfect (e.g. ±1 LSB streaming parity on
   integrated-Doppler ramps), match-and-document rather than silently
   exceed — an "improvement" on one side is a divergence someone else
   debugs later. Close-reading the authority is also how its own
   latent bugs get found.

5. **Byte-parity has a mathematical boundary — classify quantities
   before writing parity gates.** Pointwise-interpolated quantities
   (pure functions of absolute time: tx offsets, C/N0 envelopes, code
   offsets) survive chunked re-entry bit-exactly. Integrated
   quantities (phase from a Doppler profile) cannot, because chunk
   re-entry re-derives the origin with a different summation order.
   Demand bit-exact only where the math supports it; document a
   quantified tolerance where it doesn't.

6. **Never trust the first green — re-verify independently.** Treat a
   handback's (or your own) passing gates as claims to re-derive from
   the artifacts, not results to relay. Every gate that later failed
   under an adversarial parameter had already "passed". Operator
   priors about likely failure modes ("each hop hid one real defect")
   are calibration data — write them into kickoff prompts as standing
   instructions.

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
