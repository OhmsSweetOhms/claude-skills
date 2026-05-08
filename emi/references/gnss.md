# GNSS RF Environment Survey Reference

GNSS work in the EMI repo is an RF environment/noise survey for GPS L1/L2. It
is not MIL-STD-461 RE102 or CE102.

Use repo docs for the detailed plan and checklist:

- `docs/gnss-rf-environment-noise-survey-plan.md`
- `docs/gnss-bench-checklist.md`
- `emi/gnss.py`
- `tests/test_gnss.py`

## Measurement Meaning

Report GNSS survey data as receiver power and approximate power density:

```text
dBm
dBm/Hz ~= measured_dBm - 10*log10(RBW_Hz)
```

Do not apply:

- RE102/CE102 limit lines
- antenna-factor correction
- `dBuV/m` field-strength conversion
- MIL pass/fail scoring

If the user wants a threshold, record it as a separate engineering criterion
with its own assumptions.

## Profiles

The default profiles stay inside the Calian TW3972XF LNA bands:

```text
l2_passband   1164-1254 MHz
l2_deep       1215-1240 MHz
l1_passband   1559-1606 MHz
l1_deep       1563-1588 MHz
```

Preview without hardware:

```bash
.venv/bin/python tools/emi_control.py gnss plan --show 2
```

Stage a dry-run manifest:

```bash
.venv/bin/python tools/emi_control.py gnss scan gnss_l1_l2_lab_YYYYMMDD \
  --profile l2_passband \
  --profile l2_deep \
  --profile l1_passband \
  --profile l1_deep \
  --condition antenna_powered_indoor \
  --bias-voltage-v 5 \
  --bias-current-ma 0 \
  --external-attenuation-db 20 \
  --note 'Dry run only; update measured bias current at bench.'
```

The scan command is dry-run by default. It writes `manifest.json` and expected
raw CSV names, and it does not connect to hardware unless `--apply` is present.

## Active Antenna Safety

Before connecting the analyzer RF input:

- Set a current limit for the TW3972XF supply.
- Verify bias tee DC and RF paths with a DMM.
- Verify no DC appears at the analyzer-side RF connector.
- Start with `20 dB` external attenuation.
- Leave analyzer preamp off for the first run.
- Run terminated baselines before powered antenna data.

Baseline order:

```text
1. rsa_terminated
2. chain_terminated
3. antenna_powered_indoor
4. antenna_powered_sky_view
```

First live RSA command:

```bash
.venv/bin/python tools/emi_control.py gnss scan gnss_l1_l2_rsa_term_YYYYMMDD \
  --profile l2_passband \
  --profile l1_passband \
  --condition rsa_terminated \
  --external-attenuation-db 20 \
  --rsa-attenuation-db 20 \
  --no-preamp \
  --apply
```

Only move to powered-antenna runs after the terminated baseline has been
processed and plotted.

## FPH As GNSS Analyzer

When using the FPH:

- Set `--analyzer fph`.
- Set `--points 711`.
- Use `--detector SAMP` if average detector conflicts with FPH settings.
- Keep the same non-MIL reporting rules.

Example dry run:

```bash
.venv/bin/python tools/emi_control.py gnss scan gnss_fph_term_YYYYMMDD \
  --analyzer fph \
  --host <fph_ip> \
  --profile l2_passband \
  --profile l1_passband \
  --points 711 \
  --detector SAMP \
  --condition rsa_terminated \
  --external-attenuation-db 20 \
  --rsa-attenuation-db 20 \
  --no-preamp
```

## Process And Plot

After raw CSVs exist:

```bash
.venv/bin/python tools/emi_control.py gnss process-csv \
  'data/gnss/noise_survey/<measurement>/raw/*.csv' \
  --manifest data/gnss/noise_survey/<measurement>/manifest.json \
  --external-attenuation-db 20 \
  --condition rsa_terminated \
  --out data/gnss/noise_survey/<measurement>/derived/noise_survey.json

.venv/bin/python tools/emi_control.py gnss plot-svg \
  data/gnss/noise_survey/<measurement>/derived/noise_survey.json \
  --out data/gnss/noise_survey/<measurement>/plots/noise_survey.svg
```

For review, broad-vs-focused L1/L2 overlays are useful when multiple survey
runs exist. Keep exact local run IDs in `handoff.md`, not in this reusable
reference.
