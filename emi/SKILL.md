---
name: emi
description: "EMI/EMC bench automation and MIL-STD-461 measurement workflows for the EMI project. Use this skill whenever the user mentions EMI, EMC, MIL-STD-461, RE102, CE102, radiated emissions, conducted emissions, GNSS RF environment surveys, analyzer substitution, Rigol RSA5000/RSA5065, R&S FPH/Spectrum Rider, SMA100A, SDG2000/SDG2042X, spectrum analyzer Ethernet/SCPI/FTP export, TBMA1B antenna factors, LISN measurements, dBuV/m, dBuV, noise-floor vs UUT-on plots, scan manifests, calibration/system-check runs, or organizing EMI scan data."
---

# EMI Bench Workflows

Use this skill for the local EMI bench project and related RF measurement work.
The default project path in Doogie's environment is:

`/media/doogie/Work1/Claude/work/EMI`

If the task is not in that directory, infer the active repo from `cwd` and
confirm the expected files exist before acting.

## Repo Boundary

Treat the EMI repo as the source of truth for executable code, tests,
calibration JSON, manuals, distilled reference docs, and handoffs. Treat this
skill as the concise operating manual that points future sessions at the right
repo files and safe workflows.

- Do not copy project drivers, plotting code, tests, PDFs, extracted manuals,
  or generated measurement data into the skill.
- Prefer repo commands and docs over recreated snippets:
  `tools/emi_control.py`, `emi/`, `tests/`, `docs/`, `antennas/`, and `LISN/`.
- Keep session-specific run IDs, exact bench results, and local data paths in
  `handoff.md` or project docs. Keep reusable procedure in this skill.
- Use `docs/emi_tracker.json` for skill/workflow gaps discovered during real
  bench work. Classify each lesson as repo code, repo reference, skill
  guidance, eval material, handoff-only, or local data.

## Start Here

1. Read the newest handoff first: `handoff.md` if present, then `HANDOFF.md`.
2. Read `README.md` for current command examples and known working paths.
3. Check `git status --short` before editing so user changes are not
   overwritten.
4. Identify the measurement class:
   - RE102 radiated emissions: read `references/re102.md`.
   - CE102 conducted emissions: read `references/ce102.md`.
   - GNSS RF environment survey: read `references/gnss.md`.
   - Analyzer or signal-generator details: read `references/instruments.md`.
   - Data organization or scan archival: read `references/data-layout.md`.
5. For exact instrument command behavior, prefer the project driver and local
   research notes over guessing SCPI commands:
   - `emi/instruments/rsa5000.py`
   - `emi/instruments/fph.py`
   - `emi/instruments/sdg2000x.py`
   - `emi/instruments/sma100a.py`
   - `docs/Rigol_RSA5000_Programming_Guide.md`
   - `docs/FPH_User_Manual_en_21.md`
   - `docs/Siglent_SDG2042X_User_Manual.md`
   - `docs/SMA100A_OperatingManual_en_14.md`
   - `docs/MIL-STD-461F.md`

## Live Hardware Rules

Treat spectrum analyzers, LISNs, antennas, bias tees, and signal generators as
live hardware.

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
- Keep direct RSA/FPH analyzer smoke checks separate from formal antenna-port
  or LISN-path measurement-system checks.
- Preserve raw RSA CSV exports. Reprocessing should create new JSON/SVG
  artifacts without modifying raw files.
- Do not use direct `:TRACe:DATA? TRACE1` on the RSA5065 bench path; use the
  project CSV store plus FTP path. The FPH is different: it uses
  `TRACe:DATA?` and writes a local CSV.
- GNSS surveys with an active antenna require current limiting, bias-tee DC
  checks, no DC at the analyzer input, initial attenuation, preamp off, and
  terminated baselines before powered antenna data.
- If front-panel control is locked out, note that ESC restored local control in
  the 2026-05 bench session. Do not claim a SCPI local-unlock command unless it
  has been verified against the RSA5000 programming guide and this unit.

## Workflow Patterns

For CE102 iterative tuning, use the two-step bench loop:

- `get ready`: stage the next numbered run folder and `run.json`, verify prior
  artifacts and analyzer reachability, report settings, and do not acquire.
- `go`: run the scan, generate per-run and overlay/summary plot SVGs, run
  `limit-summary`, compare against the previous configuration and ambient
  baseline, update `run.json` paths, and report pass/fail plus current best.

For calibration or measurement-system checks, stage artifacts under
`data/<test>/calibration/<run_id>/` and record injection plane, losses, source
limits, tone plan, raw traces, derived JSON, plots, optional formal reports,
and pass/fail.
Use explicit `--apply` only after the dry-run plan has been reviewed.

For GNSS RF environment surveys, keep the work non-MIL: no RE102/CE102 limit
scoring, no antenna-factor `dBuV/m` conversion, and no pass/fail language
unless the user has defined a separate engineering criterion.

## Data Principles

- JSON is the preferred format for manifests, corrections, calibration data,
  and generated summaries.
- Raw files, derived JSON, plots, and any formal reports should be separated by
  role.
- Put authoritative metadata in JSON manifests, not only in filenames. The
  RM255 scan had a filename/polarization mismatch; that kind of correction must
  live in metadata.
- For new UUT/EUT work, initialize data under
  `data/uuts/<uut_id>/<campaign_id>/<method>/`.
- Prefer ISO date campaign IDs such as `2026-05-08` when the test group is a
  date-scoped bench session. Add a short suffix only when one date has multiple
  distinct campaigns.
- When the UUT identity is not known yet, allocate a provisional sequence ID
  (`uut_001`, `uut_002`, ...). Do not create `unknown_uut` directories.
- For non-UUT data points, bench validation, analyzer smoke checks, ambient
  reference scans, and calibration experiments, use
  `data/characterization/<dataset_id>/<campaign_id>/<method>/`.
- Put setup labels such as `indoor`, `outdoor`, `screen_room`, or
  `terminated` in JSON metadata and run names, not as directory layers.
- Generated scan artifacts under `data/` are local bench products and normally
  stay out of git. Commit code, schemas, reference docs, and reusable scripts.
- Skill evals verify workflow behavior. Repo unit tests verify parsers,
  correction math, plotting/report behavior, and instrument drivers.

## Reference Index

| Task | Reference | Load When |
| --- | --- | --- |
| RE102 radiated emissions | `references/re102.md` | Antenna-factor correction, TBMA1B scans, dBuV/m plots, RE102 reports |
| CE102 conducted emissions | `references/ce102.md` | LISN/attenuator measurements, SDG2042X system checks, dBuV correction, CE102 scans/plots |
| GNSS RF environment survey | `references/gnss.md` | GPS L1/L2 survey planning, active antenna safety, non-MIL reporting |
| Instruments and analyzer substitution | `references/instruments.md` | RSA vs FPH behavior, SDG/SMA generator checks, safe trace export paths |
| Data layout | `references/data-layout.md` | Organizing `data/re102`, `data/ce102`, `data/gnss`, calibration runs, manifests |

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

Preferred new-work initializer:

```bash
python /home/doogie/.claude/skills/emi/scripts/init_emi_test_group.py UUT123 2026-05-08 \
  --root /media/doogie/Work1/Claude/work/EMI/data \
  --kind uut \
  --method ce102 \
  --method re102 \
  --site-condition indoor \
  --site-condition outdoor \
  --attenuator-loss-db 20 \
  --cable-loss-db 0 \
  --note "Created before bench acquisition; site labels live in JSON."
```

This creates `campaign.json` plus method-level `measurement.json`,
`calibration/`, `runs/`, and `plots/` folders. It does not touch hardware.
For a provisional UUT, pass `next` as the subject ID; the helper allocates the
next unused `uut_NNN` directory under `data/uuts/`.

For non-UUT characterization data, use `--kind characterization`; the helper
will scaffold under `data/characterization/` and write `subject` metadata
instead of `uut` metadata.

## Expected Working Style

- Prefer root-cause fixes and repo-native patterns.
- For new measurement workflows, first define the data contract and correction
  terms, then implement CLI support.
- For standards or instrument details not already captured locally, say what is
  missing and ask before relying on external sources.
- When updating this skill, keep it compact and add references only when they
  reduce repeated context loading. Leave manuals and implementation details in
  the EMI repo.
- Before committing, run relevant syntax checks and a fingerprint scan if this
  skill or public-facing project files are being published.
