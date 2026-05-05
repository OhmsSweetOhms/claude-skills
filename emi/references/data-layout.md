# EMI Data Layout Reference

Use measurement-set directories for new scan data. Avoid adding more flat files
directly under `data/re102`.

## RE102 Layout

```text
data/re102/
  measurements/
    <test_id>/
      measurement.json
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
      reports/
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
- `plots/`: per-run plots.
- `reports/`: combined measurement reports, overlays, failure panels, and
  final summary artifacts.
- `measurement.json`: setup-level truth.
- `run.json`: acquisition-level truth.

## measurement.json Starter

```json
{
  "schema": "emi.re102.measurement.v1",
  "test_id": "RM255",
  "measurement_label": "RM255",
  "standard": "MIL-STD-461F",
  "method": "RE102",
  "limit": "aircraft_fixed_wing_internal_lt25m",
  "uut": {
    "name": "RM255"
  },
  "site": {
    "screen_room": false,
    "notes": "Ambient lab scan; not compliance data."
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
- For generated reports, include method and limit key:

```text
reports/<test_id>_re102_fixed_wing_internal_lt25m.svg
```

## Existing Flat Data

The current flat `data/re102` directory contains early bring-up artifacts:

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

Use the same shape under `data/ce102`:

```text
data/ce102/
  measurements/
    <test_id>/
      measurement.json
      runs/
        000_system_check_10k/
          run.json
          raw/
          derived/
          plots/
        001_system_check_100k/
        002_system_check_2m/
        003_system_check_10m/
        010_noise_floor_positive/
        011_uut_on_positive/
      reports/
```

The difference is in derived data and units:

- RE102 derived result: `field_strength.json`, `dBuV/m`.
- CE102 derived result: `conducted_voltage.json`, `dBuV`.

Suggested CE102 run roles:

- `system_check`
- `noise_floor`
- `uut_on`
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
