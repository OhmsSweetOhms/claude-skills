# Project config — schema and bootstrap

Every project gets one config at `<project-root>/.code_survey/config.json`.
The config is the runtime contract: it tells every lens which
boundaries are sacred, what the physics floor is, how to verify
proposed changes, and how to classify risk.

The config lives **inside** the artifact tree at
`.code_survey/config.json` (not in `.claude/`). This makes the
whole code-survey footprint self-contained, portable across
machines, and reviewable in PRs alongside the artifacts it
governs. Older versions of this skill kept config at
`.claude/code-survey-config.json`; bootstrap auto-migrates that
file when found.

If the config doesn't exist, the **Bootstrap** workflow creates it
(seeded from CLAUDE.md). Bootstrap is **auto-invoked** on a Scan
that finds no config — the user does not have to run it manually.
Without a config, the skill runs in generic mode with reduced
accuracy — but generic mode should be an explicit user opt-out,
not the default path.

## Schema (v1)

```json
{
  "version": 1,
  "project_name": "<short identifier from CLAUDE.md>",
  "boundaries": [
    {
      "glob": "<files matching this pattern>",
      "rule": "<what's invariant about them; why splitting would break things>"
    }
  ],
  "keep_rules": [
    "<file glob> always KEEP regardless of length"
  ],
  "physics_floor": {
    "description": "<plain English: smallest residual that matters>",
    "value": "<concrete number or rule>"
  },
  "verification_command": {
    "unit": "<command to run unit tests>",
    "integ": "<command to run integration tests>",
    "e2e": "<command to run end-to-end tests>",
    "e2e_baseline": {
      "test": "<test name>",
      "metric": "<what to measure>",
      "value_m": "<baseline value, with unit>",
      "tolerance": "<tolerance band>",
      "fix_count": "<expected pass count if applicable>"
    }
  },
  "risk_classifier": {
    "low": ["<list of change types that are low risk>"],
    "medium": ["<medium-risk change types>"],
    "high": ["<high-risk change types>"]
  },
  "lens_overrides": {
    "<lens-name>": {
      "additional_search_patterns": ["<extra grep terms>"],
      "extra_anti_patterns": ["<project-specific cautions>"]
    }
  },
  "thread_worthy_threshold": {
    "min_items": 6,
    "or_any_high_risk_with_verification": true,
    "or_multi_day_estimate": true
  },
  "default_model": "haiku",
  "lens_models": {
    "<lens-name>": "haiku|sonnet"
  }
}
```

## Field-by-field guide

### `boundaries[]`

The "do not fragment" rules. These are the most important field —
without them, every lens is at risk of recommending splits that
break project-specific architecture.

Examples drawn from real projects:
- `gps_receiver/blocks/*.py` → "one block per file; do not split".
  Each file mirrors a hardware block; splitting fragments the
  Python ↔ VHDL correspondence.
- `scenario_engine/orbit_propagator.py` → "IS-GPS-200 Table 20-IV
  algorithm; do not fragment `propagate_sv`."
- `firmware/*.c` → "bare-metal; cannot import simulation modules."

Each entry has:
- `glob`: which files this rule applies to.
- `rule`: prose explaining what's invariant and *why*. The "why"
  matters — it gives downstream agents the reasoning to extend the
  rule to edge cases the rule didn't anticipate.

### `keep_rules[]`

Stronger than `boundaries`: these files always get a KEEP verdict,
no matter what. Use sparingly — for files that are by definition
out of scope for refactoring (generated code, vendored
third-party, etc.).

### `physics_floor`

Project-specific definition of "the smallest residual that matters."
The synthesis step uses this to filter false-positive precision
claims.

Examples:
- GPS receiver: "1 ns ≈ 30 cm of pseudorange for *physical*
  contributions (clock terms, Sagnac, ionosphere). Numerical-
  convergence detail of well-conditioned fixed points does not
  qualify."
- Financial system: "1 cent at 99th-percentile portfolio size."
- ML training: "0.1 percentage point accuracy at the validation
  baseline."

Both fields are prose — they're consumed by the model, not by
deterministic code. Be specific enough that "is this above the
floor?" has a clear answer.

### `verification_command`

How the agent (or the user) confirms a proposed change is
behavior-neutral.

The `e2e_baseline` sub-object is what gets propagated into thread
proposals — the canonical metric, baseline value, and tolerance band
that every gated change must hold within.

### `risk_classifier`

Three buckets, free-form lists. The synthesis step matches a
finding's `recommendation` against these lists to assign a risk.

Defaults that work for most projects:
- **low:** comment-only, unused-symbol removal, test reorganization,
  in-place private-helper extraction.
- **medium:** sibling-module extraction, method >100 lines refactored.
- **high:** anything touching a `boundaries[]` glob, anything
  modifying a canonical test or its baseline, anything where the
  e2e metric could shift.

### `lens_overrides`

Per-lens project-specific tweaks. The most useful fields:
- `additional_search_patterns`: extra grep terms specific to the
  project's domain. Example for a GPS project:
  `["sagnac", "kepler", "klobuchar", "tgd"]`.
- `extra_anti_patterns`: project-specific cautions ("don't dedupe
  the firmware-bound copy of `_sagnac`; it intentionally cannot
  import simulation modules").

### `thread_worthy_threshold`

When the synthesis step suggests "this is sprint-worthy, propose a
thread." Three triggers, OR-combined:
- `min_items` ≥ N: more than a casual cleanup.
- `or_any_high_risk_with_verification`: any single change that
  needs a per-item E2E gate justifies a thread for the structure.
- `or_multi_day_estimate`: total effort exceeds one session.

### `default_model` / `lens_models`

Sub-agent model choice. Default is Haiku; specific lenses can
override to Sonnet via `lens_models`. **Never** put `opus` here —
synthesis is the only Opus-grade work, and that's main-session.

## Bootstrap workflow

When the user types something like "set up code-survey" or
"bootstrap the code survey config", or when **Scan** runs and finds
no config:

1. **Read CLAUDE.md** — both project root and any nested ones
   (e.g., `gps_receiver/CLAUDE.md`).
2. **Extract:**
   - "Hard requirements" / "Constraints" → boundaries, physics_floor.
   - "Conventions" / "Architecture" → keep_rules, additional anti-patterns.
   - "Running" / "Tests" → verification_command (test runner
     invocations).
   - "Block IDs" / canonical lists → boundaries entries.
   - Phrases like "1 ns matters" / "sub-X precision goal" /
     "ε tolerance" → physics_floor.
3. **Propose a config** in conversation, with comments explaining
   each section's source ("boundaries[0] derived from
   `gps_receiver/CLAUDE.md` § Module Layout"). Don't bury the
   sourcing — the user is reviewing and will ask "where did this
   come from."
4. **User reviews** — they may edit, add fields, remove fields,
   correct misreadings. Common corrections:
   - "That boundary glob is too narrow — it's all files under
     `blocks/`, not just `pl_*.py`."
   - "Physics floor is wrong; the real floor is X."
   - "Drop the `keep_rules` entry; we do refactor that file."
5. **Write** `.code_survey/config.json`.
6. **Tell the user to commit it.** The config is project state; it
   belongs in version control.

## Generic mode (no config)

If the user runs Scan without bootstrapping, the skill still
functions but with reduced accuracy. Specifically:

- All `boundaries` and `keep_rules` defaults are empty (so any file
  is fair game for SPLIT recommendations — likely too aggressive).
- `physics_floor` defaults to "no floor" (precision claims pass
  unfiltered — likely too many false P1s).
- `verification_command` defaults are missing (synthesis can't
  recommend a verification policy).
- Risk classifier uses generic defaults (file-size-based).

Warn the user explicitly: "Running in generic mode. Findings will
be conservative on KEEP rules and noisy on physics-floor. Bootstrap
a config when you have time."

## Examples

A minimal config (small Python library):

```json
{
  "version": 1,
  "project_name": "my-lib",
  "boundaries": [],
  "keep_rules": [],
  "physics_floor": {
    "description": "no domain physics; numerical drift only",
    "value": "report any drift; let user decide"
  },
  "verification_command": {
    "unit": "pytest tests/",
    "integ": "pytest tests/integration/",
    "e2e": null
  },
  "risk_classifier": {
    "low": ["comment-only", "test reorg", "rename"],
    "medium": ["module extraction"],
    "high": ["public API change"]
  },
  "lens_overrides": {},
  "thread_worthy_threshold": {"min_items": 6},
  "default_model": "haiku",
  "lens_models": {}
}
```

A complex config (the gps_design example, abbreviated):

```json
{
  "version": 1,
  "project_name": "gps_design",
  "boundaries": [
    {
      "glob": "gps_receiver/blocks/*.py",
      "rule": "Each file mirrors one FPGA/PS block. Splitting fragments the Python<->VHDL correspondence and breaks the canonical block ID lookup in shared-interfaces.v1.json."
    }
  ],
  "physics_floor": {
    "description": "1 ns ≈ 30 cm pseudorange for *physical* contributions only",
    "value": "Numerical-convergence detail of well-conditioned fixed points (e.g. τ light-time) does NOT qualify."
  },
  "lens_overrides": {
    "duplicate-helper": {
      "additional_search_patterns": ["sagnac", "kepler", "klobuchar", "tgd"]
    }
  }
}
```

The full schema lives at `assets/templates/code-survey-config.json`
(seed template with placeholder substitutions).
