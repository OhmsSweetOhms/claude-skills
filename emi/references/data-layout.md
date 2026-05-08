# EMI Data Layout Reference

Use subject/test-group directories for new scan data. Avoid adding more flat
files directly under method-first paths such as `data/re102` or
`data/ce102/measurements`.

There are two first-class subject classes:

- Actual UUT/EUT campaigns: `data/uuts/<uut_id>/<campaign_id>/...`
- Non-UUT characterization data: `data/characterization/<dataset_id>/<campaign_id>/...`

## Preferred UUT/Test-Group Layout

For actual UUT work, start here:

```text
data/
  uuts/
    <uut_id>/
      <campaign_id>/
        campaign.json
        ce102/
          measurement.json
          calibration/
          runs/
          plots/
        re102/
          measurement.json
          calibration/
          runs/
          plots/
        gnss/
          measurement.json
          calibration/
          runs/
          plots/
```

Use a real product, serial, or bench identifier when the UUT is known. If the
identity is not known yet, allocate provisional IDs in order: `uut_001`,
`uut_002`, ... `uut_nnn`. Do not create `unknown_uut`; the sequence ID gives
the data a durable handle while the true identity is resolved later in JSON.

`<campaign_id>` is the durable test group, not a site condition. Prefer ISO
dates for date-scoped bench sessions, for example `2026-05-08`. If one date has
multiple distinct campaigns, append a short qualifier such as
`2026-06-01_qualification_dry_run` or `2026-06-15_screen_room`.

Do not create directory layers named only for setup state:

```text
indoor/
outdoor/
terminated/
sky_view/
```

Put those labels in JSON instead:

- `campaign.json.site_conditions`
- `<method>/measurement.json.site_conditions`
- `<method>/runs/<run_id>/run.json.site_condition`
- run names, when useful for scanning: `001_ambient_indoor`,
  `002_hot_config_1_indoor`

This keeps one UUT campaign together while still preserving the exact bench
setup for each run.

## Characterization Layout

Bench validation, analyzer smoke checks, direct generator checks, calibration
experiments, and ambient/reference scans are valuable data, but they are not
UUT campaigns. Put them here:

```text
data/
  characterization/
    <dataset_id>/
      <campaign_id>/
        campaign.json
        rsa/
        ce102/
        re102/
        gnss/
```

Use this for examples like:

- `data/characterization/bench_validation/<campaign_id>/`
- `data/characterization/rm255_ambient_lab/<campaign_id>/`

Characterization `campaign.json` should use `subject` metadata instead of
`uut` metadata:

```json
{
  "schema": "emi.characterization.campaign.v1",
  "dataset_id": "bench_validation",
  "subject": {
    "id": "bench_validation",
    "kind": "bench_validation",
    "is_uut": false
  }
}
```

If a characterization scan uses CE102/RE102 tooling, keep the method folder
shape the same, but make the non-UUT role explicit in `campaign.json`,
`measurement.json`, or `run.json`. Do not place it under `data/uuts`.

## Initializer

Use the skill helper to scaffold a new test group without touching hardware:

```bash
python /home/doogie/.claude/skills/emi/scripts/init_emi_test_group.py next 2026-05-08 \
  --root /media/doogie/Work1/Claude/work/EMI/data \
  --kind uut \
  --method ce102 \
  --method re102 \
  --site-condition indoor \
  --site-condition outdoor \
  --attenuator-loss-db 20 \
  --cable-loss-db 0
```

`next` allocates the next unused provisional UUT directory under `data/uuts`
(`uut_001`, then `uut_002`, and so on). For a known UUT, pass that known ID
instead of `next`.

The helper creates:

- `campaign.json`
- one method folder per `--method`
- method-level `measurement.json`
- empty `calibration/`, `runs/`, and `plots/`

For non-UUT data:

```bash
python /home/doogie/.claude/skills/emi/scripts/init_emi_test_group.py bench_validation 2026-05-08 \
  --root /media/doogie/Work1/Claude/work/EMI/data \
  --kind characterization \
  --method rsa \
  --method ce102 \
  --method re102 \
  --site-condition direct_rsa_smoke
```

After initialization, add calibration/system-check artifacts under
`<method>/calibration/<run_id>/` and EUT/ambient/hot acquisitions under
`<method>/runs/<run_id>/`.

## Legacy Method-First Layout

The older layout grouped first by method:

```text
data/ce102/measurements/<test_id>/
data/re102/
```

Treat this as legacy. When migrating, keep old source paths in
`legacy_source_path`, `legacy_source_prefix`, or a migration manifest. Choose
the live destination based on subject class: actual UUT work goes under
`data/uuts`; non-UUT smoke/reference/calibration datasets go under
`data/characterization`.

## RE102 Layout

For new UUT RE102 work, use:

```text
data/uuts/<uut_id>/<campaign_id>/re102/
  measurement.json
  calibration/
  plots/
  runs/
    001_noise_floor_horizontal/
      run.json
      raw/
      derived/
      plots/
    002_uut_on_horizontal/
      run.json
      raw/
      derived/
      plots/
  reports/  # only for formal report packages, if produced
```

Optional run roles:

- `noise_floor`
- `uut_on`
- `calibration`
- `debug`
- `narrow_check`

Use explicit polarization in the run directory when relevant:

- `horizontal`
- `vertical`
- `unspecified`

Example run directory names:

```text
001_noise_floor_horizontal
002_uut_on_horizontal
003_noise_floor_vertical
004_uut_on_vertical
010_siggen_35mhz_calibration
020_narrow_check_2951120k
```

## File Roles

- `raw/`: immutable RSA CSV exports.
- `derived/`: processed JSON such as field strength, receiver power, or
  conducted voltage.
- `plots/`: per-run and method-level graph outputs, overlays, failure panels,
  and tuning summary SVGs.
- `reports/`: formal report packages or signed-off writeups only. Do not put
  routine plots in `reports/`.
- `measurement.json`: setup-level truth.
- `run.json`: acquisition-level truth.

## measurement.json Starter

```json
{
  "schema": "emi.re102.measurement.v1",
  "test_id": "UUT123_re102",
  "measurement_label": "UUT123 RE102",
  "standard": "MIL-STD-461F",
  "method": "RE102",
  "limit": "aircraft_fixed_wing_internal_lt25m",
  "uut": {
    "id": "UUT123",
    "name": "UUT123"
  },
  "site": {
    "screen_room": false,
    "notes": "Engineering scan; compliance status depends on the approved setup."
  },
  "antenna": {
    "model": "TBMA1B",
    "distance_m": 1.0
  },
  "corrections": {
    "cable_loss_db": 1.5
  },
  "notes": []
}
```

## run.json Starter

```json
{
  "schema": "emi.re102.run.v1",
  "run_id": "001_noise_floor_horizontal",
  "role": "noise_floor",
  "polarization": "horizontal",
  "trace_mode": "maxhold",
  "detector": "positive",
  "raw_csv_dir": "raw",
  "field_strength_json": "derived/field_strength.json",
  "plots": []
}
```

## Naming Rules

- Put the durable identifier first: `<test_id>`.
- Use filenames for convenience, not truth.
- Keep correction values and setup details in JSON.
- When a filename and reality disagree, preserve the raw filename and fix the
  metadata. Do not rename raw files just to hide history.
- For generated formal reports, include method and limit key:

```text
reports/<test_id>_re102_fixed_wing_internal_lt25m.svg
```

## Existing Flat Data

Legacy flat `data/re102` directories can contain early bring-up artifacts:

- Direct signal-generator calibration.
- Full TBMA1B scans.
- Narrow debug checks.
- RM255 aliases/manifests/reports.

Do not treat those flat names as the schema. When migrating, copy or move them
into measurement-set directories with a manifest that records the source paths.

RM255 note:

- Actual polarization: horizontal.
- Some source filenames contain `vertical`; trust corrected metadata instead.
- No screen room was used, so it is not compliance data.

## CE102 Layout

Use the same shape under the UUT campaign's CE102 method folder:

```text
data/uuts/<uut_id>/<campaign_id>/ce102/
  measurement.json
  calibration/
    001_lisn_path_system_check/
      run.json
      tone_plan.json
      raw/
      derived/
      plots/
  runs/
    001_ambient_indoor/
      run.json
      raw/
      derived/
      plots/
    002_hot_config_1_indoor/
  plots/
  reports/  # only for formal report packages, if produced
```

The difference is in derived data and units:

- RE102 derived result: `field_strength.json`, `dBuV/m`.
- CE102 derived result: `conducted_voltage.json`, `dBuV`.

Suggested CE102 run roles:

- `system_check`
- `noise_floor`
- `ambient`
- `uut_on`
- `hot`
- `hot_config`
- `debug`

Suggested line identifiers:

- `positive`
- `negative`
- `line`
- `neutral`
- `return`
- `unspecified`

CE102 `measurement.json` should record the LISN model/configuration, limit key,
attenuator loss, source voltage class, and whether the system check passed.
For this project, the active LISN configuration is `50uh`; do not encode
`50uh_5ohm` as the default.

For iterative engineering scans, keep the ambient baseline as the first run and
then add hot/configuration runs in order:

```text
001_ambient/
002_hot_config_1/
003_hot_config_2/
004_hot_config_3/
```

Use the same ambient run for overlays unless the site, cable path, LISN setup,
attenuator, RBW plan, or ambient conditions have changed enough to require a
new baseline. Generated overlay and summary SVG plots belong in method-level
`plots/`, not inside one run directory, because they compare multiple runs.

Suggested CE102 `run.json` fields:

- `schema`: `emi.ce102.run.v1`
- `run_id`, `role`, `configuration_id`, `line`, `label`
- `ambient_baseline`, `comparison_runs`
- `frequency_range_hz`, `rbw_hz`, `vbw_hz`, `points`, `sweep_count`
- `trace_mode`, `detector`, `analyzer`, `host`
- `attenuator_loss_db`, `cable_loss_db`, `external_gain_db`
- `lisn_model`, `lisn_mode`, `correction_model`, `limit`
- `raw_csv_dir`, `conducted_voltage_json`, `plots`
- `summary.peak`, `summary.worst_margin_db`, `summary.pass_fail`,
  `summary.current_best`

## Calibration Layout

Store calibration and measurement-system-check runs outside EUT measurement
folders:

```text
data/uuts/<uut_id>/<campaign_id>/<method>/
  calibration/
    <run_id>/
      run.json
      tone_plan.json
      raw/
      derived/
      plots/
      reports/  # only for formal report packages, if produced
```

Use this for CE102 SDG2000/RSA system checks and RE102 SMA100A/analyzer system
checks. A calibration `run.json` should identify the run mode so it cannot be
confused with EUT emissions data:

- `direct_rsa_smoke`
- `lisn_path_ce102_system_check`
- `antenna_port_re102_system_check`

Record at least:

- instruments, hosts, channels, output load, and analyzer backend
- injection plane and signal path
- source-to-injection losses/gains
- measurement-path cable loss and external gain
- source output limits and configured power
- selected frequency set and tolerance
- raw CSV paths, derived JSON paths, plots, optional formal reports, and
  pass/fail

## GNSS Noise Survey Layout

GNSS RF environment surveys are not RE102 or CE102 measurement sets. In a UUT
campaign, use:

```text
data/uuts/<uut_id>/<campaign_id>/gnss/
  measurement.json
  runs/
    <run_id>/
      manifest.json
      raw/
      derived/
      plots/
```

Expected files:

- `manifest.json`: dry-run/live plan, analyzer backend, profiles, expected raw
  CSV names, active-antenna metadata, attenuation, bias, and notes.
- `raw/`: immutable analyzer CSV files.
- `derived/noise_survey.json`: processed receiver-power and density views.
- `plots/noise_survey.svg`: plot with GPS L1/L2 markers and TW3972XF passband
  context.

GNSS metadata should set:

```json
{
  "purpose": "rf_environment_noise_survey",
  "standard": "none"
}
```

Do not add MIL limit keys, antenna-factor correction, `dBuV/m` pass/fail
fields, or compliance verdicts to GNSS manifests unless the user explicitly
defines a separate engineering criterion.
