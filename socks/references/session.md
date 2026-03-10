# Session Manifest & Dashboard

## Overview

The session manifest (`build/logs/session.json`) tracks every pipeline stage
run — both scripted and guidance — in a single unified log.

## Session lifecycle

```bash
# Create a fresh session on first pipeline invocation
python3 scripts/socks.py --project-dir . --stages 0 --new-session

# Scripted stages auto-log to session.json
python3 scripts/socks.py --project-dir . --stages 1,4,7,8,10

# Log guidance stages manually after completing them
python3 scripts/log_stage.py --project-dir . --stage 2 --status pass \
    --note "Wrote RTL" --files src/module.vhd

# Terminal summary
python3 scripts/socks.py --project-dir . --summary
```

## log_stage.py

Logs guidance stages (2, 5, 6, 11, 12, 13) or any manual stage work:

```
--project-dir   Project root (required)
--stage         Stage number 0-13 (required)
--status        pass | fail | skip (required)
--note          Description of what was done
--files         Files created or modified
```

Creates `session.json` if it doesn't exist.

## dashboard.py

Live HTML dashboard with SSE auto-refresh:

```bash
# Live server (opens browser, auto-refreshes on session.json changes)
python3 scripts/dashboard.py --project-dir . --port 8077

# Static HTML snapshot
python3 scripts/dashboard.py --project-dir . --no-serve --output build/logs/dashboard.html
```

The dashboard shows:
- 14-card stage grid with pass/fail/skip colours and iteration badges
- Chronological timeline of all stage runs
- Stats bar: stages completed, pass/fail/skip counts, iteration depth, duration

## session.json format

```json
{
    "session_id": "20260310_143022",
    "project": "/path/to/project",
    "stages": [
        {
            "stage": 0,
            "time": "14:30:22",
            "status": "pass",
            "source": "script",
            "note": "Environment OK",
            "files": [],
            "iteration": 1,
            "log_file": "build/logs/stage_00.log"
        }
    ]
}
```

- **source**: `"script"` (socks.py) or `"guidance"` (log_stage.py / Claude)
- **iteration**: auto-increments per stage number across design-loop re-entries

## Design-loop iterations

When the pipeline re-enters at Stage 2, each subsequent stage run increments
its iteration counter. The dashboard shows iteration badges on cards and in the
timeline, making it easy to see how many passes each stage required.
