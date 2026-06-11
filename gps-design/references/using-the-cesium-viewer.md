# Using the Cesium viewer — render contract & canonical-scenario authoring

The Cesium globe viewer (`docs/cesium-viewer/index.html`, single-file app)
renders a run's truth track, PVT fixes, SV constellation, antenna lobes, and
interference picture on a 3D globe, scrub-synced with the SVG results
dashboard. **Serve/install mechanics, lockstep sync, and WebGL/network
requirements live in `docs/cesium-viewer/README.md` — read that for
operations.** This chapter is the knowledge the README doesn't carry: what
payload fields light up which render features, how platform identity
resolves, and how to author a scenario so it renders correctly in BOTH the
dashboard and the viewer.

Function names below are stable search anchors in
`docs/cesium-viewer/index.html`; quoted line numbers are as of 2026-06-11
and will drift — search the name, not the number.

## 1. Taxonomy mapping (read `docs/scenario-taxonomy.md` first)

The project's scenario classification (settled 2026-06-11) has three axes;
each maps to a distinct viewer surface. When extending the viewer, hang new
controls off the right axis — don't invent a new classification signal.

| Taxonomy axis | Scenario field | Viewer surface |
|---|---|---|
| Platform → role/mobility (`base`/`rover`) | results `platforms{n}.role` (today *derived*: base iff `trajectory.type=="static"`) | Platform selector options read "name (role)"; non-active platforms render as static labeled dots at their first truth point; a `base` platform's `truth_track` is collapsed to one point by the results fold |
| Entity → function (`kind`) | `entities.<e>.kind` | `gps_receiver` → the flying receiver entity + path + antenna lobe + PVT fixes; `jammer` → the interference layer (`eks-*` entities: emitter dots, orbit tracks, transmit lobes, TDOA) |
| Constellation → nav-vs-interference + system | `interference.constellations` in the payload (today carries the legacy flat `type`; target shape is `category` + `system`, see taxonomy doc §6) | Constellation panel rows: one GNSS row (sats / sv-ant toggles) vs per-jammer-constellation rows (sats / track / ant toggles; enabled member = orange, disabled = gray, `is_source` = bold "§6.5" label) |

## 2. Store and run addressing

- The viewer consumes `window.RESULTS.runs[]` from
  `../results-dashboard/results-data.js` — the same store the dashboard
  reads, built by `tools/build_results.py` from `results/*.results.json`
  (each run's payload is the `gps_scenario._build_results_payload`
  `schema_version: 2` shape, plus `run_id` = file stem and `source_file`
  added by the store builder).
- **Address runs by `?run=<run_id>`** (stable file stem, e.g.
  `?run=jt23_4_1_5_real_iq_60s`). Numeric `?run=N` is positional and goes
  stale: the store sorts by `source_file`, so every newly landed run can
  shift indices. The run selector writes the `run_id` form back into the
  URL (`pickDefaultRun()` ~line 969; selector handler ~line 360). Default
  with no param: first run that has PVT fixes.

## 3. Payload-field → render contract

Every block is optional and **feature-detected**; absence is a silent no-op
(no error, no placeholder). The per-run scene is rebuilt by `buildScene()`
with panel setup in its tail (`renderConstellationPanel(run)`,
`updatePlatformSection(run)`, `setupTdoaPanel(run)`, `buildInset(run)`).

| Payload block | What it lights up (anchor) |
|---|---|
| `truth_track[]` | Blue truth polyline + the flying receiver entity/label/path (`buildScene` ~1958) |
| `pvt_track[]` | Time-gated fix dots colored green/amber/red by `err_3d_m` (~2084) |
| `sv_series{prn}` | Per-SV satellite entities + trails + LOS rays + SV transmit lobes (~2105); an SV needs ≥2 samples carrying `sv_ecef_m` or it is skipped (older frozen runs) |
| `interference` | The whole jammer layer: emitter dots/orbits (ECEF + ECI frames), transmit lobes, TDOA stations/sightlines/LOP — and the camera: `if (run.interference) flyToInterference(run)` else overview (~2164) |
| `interference.constellations` | Per-constellation rows in the panel (`renderConstellationPanel` ~1342); without it, a legacy single-EKS master toggle |
| `interference.tdoa` (+ `.series`, `.lop_surface`) | TDOA stations + LOP; `series` animates the hyperbola + moving source (vs static t0 snapshot); `lop_surface: "ground"` switches to a draped dashed LOP + 3D hyperboloid sheet |
| `platforms{name}` | Multi-platform accordion: selector shown when ≥2 platforms ("name (role)", ~2189); `runView(run, name)` merges the active platform's series into the run-level view; siblings render as static dots |
| `interference_exposure` | Platform-scoped "JAMMED" badge + which constellations a platform "sees" (~2316) |
| `attitude_series[]` + `run.antenna_boresight_frd` | Antenna lobe banks with body attitude (quaternion → `boresightWorld`); absent → position-only zenith/nadir lobe |
| `run.cov_gnss_ned` / `cov_series[]` | Red constant GNSS covariance ellipsoid / green animated fused ellipsoid (50× exaggerated) |
| `raim_series` / `signal_series{prn}` | RAIM PASS/FAIL and per-SV lock/C/N0 state in the SV panel |
| `run.mode` | Platform-name resolution input — see §4. **The single most common parity bug.** |

Multi-platform scoping: series blocks (`truth_track`, `pvt_track`,
`sv_series`, `interference_exposure`, …) are per-platform via the
`runView()` merge; `interference` and `run` metadata are run-level, shared
across platforms.

## 4. How platform display names resolve (`resolvePlatform()`, ~line 926)

```
run.run.mode ──► fetch ../../tracking-mode-profiles.json
                    └─ entry.scenario_root ──► fetch ../../scenario_engine/scenarios/<name>.v2.json
                                                  └─ platforms.<platformName>.name  (+ antennas: model,
                                                     peak_gain_dbi, sv_block → SV-lobe gain table)
```

Fallbacks, in order: mode key missing from the registry → try the mode
string as a scenario basename (strip `.v2`); platform name not found →
first platform in the file; any fetch failure → `null`, and the receiver
tag renders the generic **"rx"** label. So a run whose `mode` resolves to
no scenario file silently loses its human platform name, antenna pattern,
and `sv_block` lobe table — exactly the real-IQ failure mode of §6. This
chain is also why the viewer must be served from the **repo root** (the
`../../` fetches).

## 5. Authoring a scenario that renders in BOTH dashboard and viewer

The CLAUDE.md keystone applies: **one results contract, two producers** —
anything that emits a results JSON must produce the same
`_build_results_payload` shape, or sim-vs-FPGA/real diffs stop being one
diff at one sink.

- Author per `references/adding-a-scenario.md` (§1–5 config, §6 run +
  capture). The dashboard needs `signal_source: test_24sv` + `nav_data:
  subframe` for rich panels; the viewer additionally rewards `sv_ecef_m`
  in `sv_series` (SV entities), a resolvable `run.mode` (names + lobes),
  and the `interference` block (jammer picture).
- **Anti-pattern (load-bearing):** producing a results payload
  off-pipeline. A hand-rolled serializer drifts from the canonical
  producer and the run renders *differently* with no error anywhere —
  feature detection just quietly skips what's missing. If a run can't go
  through `gps_scenario.py` end-to-end (e.g. real-IQ replay), the replay
  script must still **call the canonical producers**:
  `gps_scenario._build_results_payload` for the body and
  `gps_scenario._build_interference_geometry(scenario_root, ...)` for the
  jammer block, and set `run.mode` to a registry mode that resolves to the
  scenario file (§4).
- Jammer picture: `_build_interference_geometry` scans `entities` for
  `kind: jammer` — spaceborne (`waveform.type: satellite` on a `tle`
  platform) gets the full orbit/TDOA treatment; otherwise it falls through
  to the terrestrial branch (ground jammer dots). Jammer-free runs return
  `None` → no block → viewer interference mode stays inert. Off-by-default
  is the contract.

### Worked example — synthetic/real twin parity (runs 20/21)

`rfi/20260611-jt23-real-iq-pvt`: the real-IQ run
(`results/jt23_4_1_5_real_iq_60s.results.json`) was hand-authored
off-pipeline and rendered without the jammer dot and with the generic
"rx · 5/5" label, while its synthetic twin
(`jammertest_jt23_4_1_5_staged_onset_36s`) rendered fully. The fix (in the
replay diagnostic's `_emit_results`) was exactly the recipe above: set
`run.mode = "jammertest_jt23_4_1_5"` and attach `interference` via
`_build_interference_geometry`. The two runs now render identically except
synthetic-vs-real PVT. Validate any such fix with
`python3 tools/build_results.py --check` plus the browser suites
(`tests.test_results_dashboard`, `tests.test_cesium_viewer`) — run those
**serially under system python3**, never in parallel (playwright leak
lesson).

## 6. Known staleness / migration notes

- `docs/cesium-viewer/README.md` still describes the EKS layer as a
  static-snapshot picture; the producer now emits an animated TDOA series
  (`tdoa.series`) which the viewer renders when present.
- The payload's `interference.constellations` carries the legacy flat
  `type` value; when the taxonomy migration lands
  (`docs/scenario-taxonomy.md` §6: `category` + `system`), the producer,
  this contract table, and `renderConstellationPanel()` move together.
