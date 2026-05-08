# EMI Skill Scripts

Scripts in this directory are deterministic helpers for file and JSON workflow.
Keep live instrument control in the EMI project unless a script is intentionally
made portable.

## Scripts

- `init_emi_test_group.py`: create the preferred subject/test-group layout
  with `campaign.json`, method-level `measurement.json`, `calibration/`,
  `runs/`, and `plots/`. Use `--kind uut` for actual UUT/EUT campaigns and
  `--kind characterization` for bench validation, smoke checks, and
  ambient/reference data points. For provisional UUTs, pass `next` or `auto`
  as the subject ID to allocate the next `uut_NNN` directory.
- `init_re102_measurement.py`: create a measurement-set directory and starter
  `measurement.json` for the legacy `data/re102/measurements/<test_id>/`
  layout. Prefer `init_emi_test_group.py` for new work.

Example:

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

Known UUT example:

```bash
python /home/doogie/.claude/skills/emi/scripts/init_emi_test_group.py UUT123 2026-05-08 \
  --root /media/doogie/Work1/Claude/work/EMI/data \
  --kind uut \
  --method ce102 \
  --method re102
```

Non-UUT characterization example:

```bash
python /home/doogie/.claude/skills/emi/scripts/init_emi_test_group.py bench_validation 2026-05-08 \
  --root /media/doogie/Work1/Claude/work/EMI/data \
  --kind characterization \
  --method rsa \
  --method ce102 \
  --method re102 \
  --site-condition direct_rsa_smoke
```

Legacy RE102-only example:

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
