# Adding a scenario to the project

A scenario is the **single root of truth** for an end-to-end run:
`scenario_engine/scenarios/<name>.v2.json`, validated against
`scenario_engine/schemas/scenario.schema.json` (Draft-07). This is the
recipe for adding one, **running it, and viewing its results** without tripping
the drift this project guards against. Authoritative shape: that schema +
`scenario_engine/CLAUDE.md` §3a. See the live set in the HTML atlas
(`docs/json-structure/index.html`, Scenarios tab). The flow is: author (§2) →
validate (§3) → make runnable (§4) → refresh the config atlas (§5) → **run it
and capture results to the dashboard (§6)**.

## 1. Pick `signal_source` — it drives the required shape

`signal_source` (top-level, required) selects a conditional `allOf` branch:

| `signal_source` | required blocks | forbidden |
|---|---|---|
| `synthetic` | (none beyond top-level) | `constellation`, `live` |
| `test_24sv` | `platforms`, `entities`, `environment` | `constellation`, `live` |
| `rinex` | `platforms`, `entities`, `environment`, `constellation` | `live` |
| `live` | `live` | `constellation` |

Always-required top-level keys: `schema_version`, `name`, `signal_source`,
`iq_gen`.

- **synthetic** — constant per-PRN Doppler; satellites are supplied at runtime
  via `--prn PRN:DOPPLER:CODE_PHASE[:CN0]` (the schema intentionally does NOT
  require `iq_gen.satellites`). Set `iq_gen.cn0_dbhz_default`. This is the
  open_sky / urban / high_dynamic / indoor / cislunar / extreme_dynamic family.
- **test_24sv** — physics-driven from the 24-SV constellation + a platform
  trajectory; C/N0 comes from the link budget (no `cn0_dbhz_default`). This is
  the huntsville_static / huntsville_circle family.

## 2. Author the file

`test_24sv` skeleton (static ground receiver):

```json
{
  "schema_version": "1.0",
  "name": "My Scenario",
  "signal_source": "test_24sv",
  "platforms": {
    "rx_site": {
      "name": "...",
      "trajectory": { "type": "static", "lat_deg": 34.66, "lon_deg": -86.55, "alt_m": 194.2 },
      "antennas": { "gps_l1": { "direction": "rx", "model": "patch", "peak_gain_dbi": 5.0, "sv_block": "iif" } }
    }
  },
  "entities": {
    "primary_rx": { "kind": "gps_receiver", "platform": "rx_site", "antenna": "gps_l1",
                    "receiver_profile": "open_sky", "freq_band": "L1" }
  },
  "environment": { "elevation_mask_deg": 5.0, "t_sys_k": 300.0, "cn0_floor_dbhz": 20.0 },
  "iq_gen": { "doppler_type": "scenario", "nav_data": "all_ones", "duration_s": 1.0,
              "noise_model": { "type": "awgn", "rms_dbfs": -12.0 } },
  "outputs": { "pvt": { "enabled": true, "file": null },
               "results": { "enabled": true, "file": null } }
}
```

Block notes:

- **`platforms.<p>.trajectory.type`** ∈ `static | lunar_static | waypoint | oem`.
  `waypoint` **is supported** (the huntsville_circle scenarios use it) — never
  write a "waypoint deferred / stand-in until waypoint lands" rationale. Plus an
  optional `antennas` map.
- **`entities.<e>`** — `kind` (`gps_receiver` | `jammer`). `gps_receiver`
  **requires `receiver_profile`**, a string key into
  `receiver-block-profiles.json::profiles` (open_sky / urban / high_dynamic /
  indoor / cislunar). **Bind the receiver here, not in tracking-mode.** For pure
  IQ generation with no matched receiver (e.g. `extreme_dynamic`), use
  `signal_source: synthetic` and **omit the entity entirely** (there is no
  `receiver_profile: null`).
- **`environment`** — `elevation_mask_deg`, `t_sys_k`, `cn0_floor_dbhz`,
  optional `atmosphere` (tropo) and `multipath`.
- **`iq_gen`** — `doppler_type`; `nav_data` (`all_ones` = no decodable message →
  no PVT; `subframe` = real LNAV → end-to-end PVT, `wn` defaults 2345; `random`);
  `duration_s`; `noise_model`; optional `propagation_model` (iono/tropo).
- **`outputs`** — per-sink toggles (`enabled` + optional `file`): `pvt` / `iq` /
  `rinex` / `interference_manifest` / **`results`**. The **`results`** sink
  (default enabled) is the run-outcome JSON the dashboard reads (§6) — set its
  `file` to `results/<id>.results.json` to land directly in the committed store.
  Optional **`targets`** — the per-scenario PVT goal (the sub-5 m breakdown);
  add it when the scenario has a defined accuracy target.

## 3. Validate

```bash
python3 -m unittest tests.test_scenario_root_schema   # auto-discovers every *.v2.json
```

Positive tests validate every shipped scenario; negative tests cover missing /
mistyped / out-of-range / conditional-branch violations.

## 4. Make it runnable

- **Direct:** `python3 gps_scenario.py --scenario-root scenario_engine/scenarios/<name>.v2.json`
- **Named mode:** add a thin pointer to `tracking-mode-profiles.json`, then `--mode`:
  ```json
  "<mode>": { "scenario_root": "scenario_engine/scenarios/<name>.v2.json", "notes": "..." }
  ```
  Modes are **thin pointers only** — no inline config. A scenario with no mode is
  legal (reach it via `--scenario-root`), but then the mode registry isn't a
  complete index of what exists — decide deliberately.

## 5. Refresh the HTML atlas

```bash
python3 tools/build_json_atlas.py     # then reload docs/json-structure/index.html
```

Your scenario shows up in the Scenarios tab with its mode / targets / orphan flags.

## 6. Run it and see the results in the dashboard

§1–5 are the *config* side. To run the scenario and view its *outcome* — PVT
accuracy vs truth, satellites tracked + health, C/N0 over time, an az/el
skyplot, and a drag/playback 3D track — use the results dashboard, the
run-outcome companion to the atlas (`docs/results-dashboard/`, thread
`cross-cutting/20260524-results-dashboard`).

You do **not** write a serializer. Every run emits a results JSON through the
**`outputs.results`** sink (default on), built by
`gps_scenario._build_results_payload` and written by the one sidecar writer
`_emit_sidecar` (same mechanism as `outputs.interference_manifest`). Pick the
capture path by run size:

- **Light / short run (in-RAM)** — receiver runs in memory; the sidecar lands
  next to `--output`:
  ```bash
  python3 gps_scenario.py --scenario-root scenario_engine/scenarios/<name>.v2.json --output /tmp/run.iq16
  # -> /tmp/run.iq16.results.json   (or set outputs.results.file to write into results/)
  ```
- **Heavy / long run (would OOM in-RAM** — the full complex IQ is 16 B/sample,
  ~6 GB for 90 s, ~16 GB for the 252 s spiral**)** — the memory-bounded
  streaming capture (IQ → temp file → chunked receiver replay):
  ```bash
  python3 gps_scenario.py --mode <mode> --duration <N> --stream-replay
  # -> results/<scenario-slug>_<N>s.results.json   (override with --results-out)
  ```
  `--stream-replay` is explicit, warm-start only, and omits the residual DC
  offset (a streaming limitation, like `--iq-only`). The chunk-replay lives in
  the reusable primitive `gps_scenario.run_receiver_on_iq_file`; the default
  store filename is `<scenario-slug>_<duration>s.results.json`.

**For the dashboard to be rich**, author the scenario as `signal_source:
test_24sv` (engine truth → 3D track / skyplot / az-el / PVT-error-vs-truth) with
`nav_data: subframe` (real LNAV → ephemeris decode → PVT). First fix needs
~30 s of subframe decode, so bound the duration above that. `nav_data:
all_ones` runs track but produce no PVT (the PVT panels gray out cleanly);
synthetic `--prn` runs have no engine truth (3D track / skyplot stay empty).

**Aggregate + view:**
```bash
python3 tools/build_results.py            # results/*.results.json -> docs/results-dashboard/results-data.js
python3 tools/build_results.py --check     # CI staleness gate (mirrors build_json_atlas.py --check)
```
Then open `docs/results-dashboard/index.html` (zero-dependency, `file://`-safe;
run selector + drag/playback 3D track). `results/` is the canonical **committed**
store — small durable JSONs, unlike the gitignored `/temp/` `.iq16` captures.
The emitted `results-data.js` is itself a **gitignored derived artifact**
(untracked 2026-06-11): run `tools/build_results.py` once after a fresh clone
(~0.3 s) and after runs land; `--check` exits 1 when it's missing or stale.
Tests: `tests/test_results_dashboard.py` (Playwright render, system python3,
auto-covers every run in the store) and `tests/test_stream_replay.py`
(SCENARIO_SLOW, file-replay ≡ in-RAM). Mirrors the atlas pipeline:
`results/*.results.json` → `tools/build_results.py` → `results-data.js` →
`index.html`; see `docs/results-dashboard/` and `results/README.md`.

For the 3D Cesium globe view of the same store — and the payload-field →
render contract that decides what a run lights up there — see
`references/using-the-cesium-viewer.md`.

## Gotchas (surfaced by the spec-stack hygiene thread)

- **No stale rationales** — don't justify a choice by a capability that already
  exists (the `urban_kinematic` "waypoint deferred" note is the cautionary tale).
- **Name must match motion** — a "kinematic" scenario with `trajectory.type =
  static` is a smell; use a `waypoint` trajectory if it actually moves.
- **Scenario selects the profile, not the reverse** — same-named scenario↔profile
  pairs (`open_sky.v2.json` ↔ the `open_sky` profile) are conventional, but the
  binding direction is scenario → `receiver_profile`.
- **`schema_version: "1.0"` is the schema-doc revision**, unrelated to the `.v2`
  filename (the file is "v2" generation, schema doc is "1.0"). Don't reconcile
  them — it's a known label split.
