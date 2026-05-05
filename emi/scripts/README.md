# EMI Skill Scripts

Scripts in this directory are deterministic helpers for file and JSON workflow.
Keep live instrument control in the EMI project unless a script is intentionally
made portable.

## Scripts

- `init_re102_measurement.py`: create a measurement-set directory and starter
  `measurement.json` for the preferred `data/re102/measurements/<test_id>/`
  layout.

Example:

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
