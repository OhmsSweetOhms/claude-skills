# State File & Dashboard

## Overview

The project state file (`build/state/project.json`) is the single source of
truth for all pipeline data: project metadata, stage results, input hashes,
and next-action suggestions.

## State file lifecycle

```bash
# Workflow commands create/update project.json automatically
python3 scripts/socks.py --project-dir . --design --scope module
python3 scripts/socks.py --project-dir . --test
python3 scripts/socks.py --project-dir . --bughunt

# Migrate from old log-based format
python3 scripts/socks.py --project-dir . --migrate

# Legacy: explicit stages (also writes to project.json if it exists)
python3 scripts/socks.py --project-dir . --stages 0 --new-session
python3 scripts/socks.py --project-dir . --stages 4,5,7,8,9

# Log guidance stages manually
python3 scripts/log_stage.py --project-dir . --stage 2 --status pass \
    --note "Wrote RTL" --files src/module.vhd
```

## log_stage.py

Logs guidance stages (2, 6, 12) or any manual stage work:

```
--project-dir   Project root (required)
--stage         Stage number 0-13 (required)
--status        pass | fail (required)
--note          Description of what was done
--files         Files created or modified
```

Writes to both `build/logs/session.json` (legacy) and
`build/state/project.json` (if it exists).

## dashboard.py

Live HTML dashboard with SSE auto-refresh:

```bash
# Live server (opens browser, auto-refreshes on project.json changes)
python3 scripts/dashboard.py --project-dir . --port 8077

# Static HTML snapshot
python3 scripts/dashboard.py --project-dir . --no-serve --output build/dashboard.html
```

The dashboard shows:
- 14-card stage grid with pass/fail colours, duration, and source badges
- Next-action banner with retry point and blocked stages
- Activity log sorted by timestamp (newest first)
- Stats bar: stages completed, pass/fail counts, total duration
- Input hash indicators (docs/src/tb/sw tracking status)
- Scope and workflow badges in header

Data source: `build/state/project.json` via `/api/state` endpoint.

## project.json format

```json
{
    "version": 2,
    "project": {
        "name": "my_module",
        "scope": "module",
        "last_workflow": "design",
        "timestamp_last_modified": "2026-03-12T14:30:00.000000"
    },
    "design_intent": {
        "intent_file": "docs/DESIGN-INTENT.md",
        "scope": "module",
        "status": "APPROVED"
    },
    "stages": {
        "0": {
            "name": "Environment Setup",
            "status": "PASS",
            "timestamp": "2026-03-12T14:30:01.000000",
            "source": "script",
            "duration_seconds": 0.42,
            "note": "All checks passed"
        },
        "4": {
            "name": "Synthesis Audit",
            "status": "PASS",
            "timestamp": "2026-03-12T14:31:15.000000",
            "source": "script",
            "duration_seconds": 0.05,
            "note": "Run 13 static synthesis checks on 1 file(s)"
        }
    },
    "inputs_hash": {
        "docs": "abc123...",
        "src": "def456...",
        "tb": null,
        "sw": null
    },
    "next_action": {
        "suggested": "Stage 7 (SV/Xsim Testbench) FAILED. Fix and re-run.",
        "blocked_stages": [8, 9, 10, 11, 13],
        "can_retry_from": 7
    }
}
```

- **status**: `"PASS"`, `"FAIL"`, `"VIOLATED"`, `"UNKNOWN"` (uppercase; `SKIP` is not a valid status — stages must pass or fail)
- **source**: `"script"` (socks.py) or `"guidance"` (log_stage.py / Claude)
- **inputs_hash**: SHA-256 of each tracked directory; `null` if directory absent
- **next_action**: `null` when all stages pass; populated on failure

## Hash-based incremental detection

Workflow commands (`--design`, `--test`, etc.) automatically compare directory
hashes against stored values. When nothing changed, stages are skipped:

| Directory | Re-entry stage | Reason |
|-----------|---------------|--------|
| `docs/`   | Stage 1       | Architecture / design intent changed |
| `src/`    | Stage 4       | RTL changed |
| `tb/`     | Stage 4       | Testbench changed |
| `sw/`     | Stage 7       | C driver changed (DPI-C) |

When multiple directories change, the earliest re-entry stage wins.

## Migration from legacy format

Old projects using `build/logs/session.json` can migrate:

```bash
python3 scripts/socks.py --project-dir . --migrate
```

This creates a minimal `build/state/project.json` stub. The `--design` workflow
also auto-detects old projects and warns. After migration, the next pipeline
run populates all stage results and hashes.
