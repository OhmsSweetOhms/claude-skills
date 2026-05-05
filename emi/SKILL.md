---
name: emi
description: "EMI/EMC bench automation and MIL-STD-461 measurement workflows for the EMI project. Use this skill whenever the user mentions EMI, EMC, MIL-STD-461, RE102, CE102, radiated emissions, conducted emissions, Rigol RSA5000/RSA5065, spectrum analyzer Ethernet/SCPI/FTP export, TBMA1B antenna factors, LISN measurements, dBuV/m, dBuV, noise-floor vs UUT-on plots, scan manifests, or organizing EMI scan data."
---

# EMI Bench Workflows

Use this skill for the local EMI bench project and related MIL-STD-461
measurement work. The default project path in Doogie's environment is:

`/media/doogie/Work1/Claude/work/EMI`

If the task is not in that directory, infer the active repo from `cwd` and
confirm the expected files exist before acting.

## Start Here

1. Read the newest handoff first: `handoff.md` if present, then `HANDOFF.md`.
2. Read `README.md` for current command examples and known working paths.
3. Check `git status --short` before editing so user changes are not
   overwritten.
4. Identify the measurement class:
   - RE102 radiated emissions: read `references/re102.md`.
   - CE102 conducted emissions: read `references/ce102.md`.
   - Data organization or scan archival: read `references/data-layout.md`.
5. For exact instrument command behavior, prefer the project driver and local
   research notes over guessing SCPI commands:
   - `emi/instruments/rsa5000.py`
   - `emi/instruments/sdg2000x.py`
   - `.research/Rigol_RSA5000_Programming_Guide.md`
   - `.research/Siglent_SDG2042X_User_Manual.md`
   - `.research/MIL-STD-461F.md`

## Live Hardware Rules

Treat the spectrum analyzer and signal generator as live hardware.

- Start with passive checks (`rsa ports`, IDN/mode queries) before scan commands.
- Use the existing project CLI and driver paths when possible; do not invent
  new SCPI sequences during a bench run.
- Explain scan settings before starting a long acquisition: start/stop
  frequency, RBW/VBW, detector, trace mode, points, sweep count, correction
  terms, and output directory.
- Keep direct signal-generator calibration separate from antenna-based RE102
  data. A cabled generator run is receiver input power in dBm/dBuV, not
  dBuV/m.
- For CE102 signal-generator checks, use the project CLI instead of ad hoc SCPI:
  `ce102 system-check-plan` and `ce102 system-check-tone`. The SDG2042X was
  observed at `192.168.0.2`; turn output off before moving equipment or ending
  a bench session.
- Direct SDG-to-RSA runs are smoke tests for control/export/correction
  plumbing only. They do not replace the MIL-STD-461F LISN measurement-system
  check.
- Preserve raw RSA CSV exports. Reprocessing should create new JSON/SVG
  artifacts without modifying raw files.
- If front-panel control is locked out, note that ESC restored local control in
  the 2026-05 bench session. Do not claim a SCPI local-unlock command unless it
  has been verified against the RSA5000 programming guide and this unit.

## Data Principles

- JSON is the preferred format for manifests, corrections, calibration data,
  and generated summaries.
- Raw files, derived JSON, plots, and reports should be separated by role.
- Put authoritative metadata in JSON manifests, not only in filenames. The
  RM255 scan had a filename/polarization mismatch; that kind of correction must
  live in metadata.
- Generated scan artifacts under `data/` are local bench products and normally
  stay out of git. Commit code, schemas, reference docs, and reusable scripts.

## Reference Index

| Task | Reference | Load When |
| --- | --- | --- |
| RE102 radiated emissions | `references/re102.md` | Antenna-factor correction, TBMA1B scans, dBuV/m plots, RE102 reports |
| CE102 conducted emissions | `references/ce102.md` | LISN/attenuator measurements, SDG2042X system checks, dBuV correction, CE102 scans/reports |
| Data layout | `references/data-layout.md` | Organizing `data/re102`, manifests, report folders, migration from flat files |

## Bundled Scripts

Scripts live in `scripts/`. They should be deterministic helpers that work with
plain files and JSON. Keep live instrument control in the EMI project unless a
script is intentionally made portable.

Current script:

```bash
python /home/doogie/.claude/skills/emi/scripts/init_re102_measurement.py RM255 \
  --root /media/doogie/Work1/Claude/work/EMI/data/re102/measurements \
  --uut RM255 \
  --antenna-model TBMA1B \
  --distance-m 1 \
  --cable-loss-db 1.5 \
  --screen-room no \
  --note "Ambient lab scan; not compliance data."
```

## Expected Working Style

- Prefer root-cause fixes and repo-native patterns.
- For new measurement workflows, first define the data contract and correction
  terms, then implement CLI support.
- For standards or instrument details not already captured locally, say what is
  missing and ask before relying on external sources.
- Before committing, run relevant syntax checks and a fingerprint scan if this
  skill or public-facing project files are being published.
