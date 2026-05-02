# Retroactive Codex handback prompt

> Paste this whole block into Codex running in the existing worktree.
> Replace every `{{...}}` placeholder first. This prompt reconstructs
> a missing handback from committed evidence; it must not ask Codex to
> redo the source work.

---

You are a Codex helper running inside an existing worktree for the
`{{REPO_NAME}}` project. Your task is **retroactive handback
reconstruction only** for a plan hop that already ran.

## Inputs

- **Thread:** `{{THREAD_ID}}`
- **Plan:** `{{PLAN_ID}}`
- **Plan file:** `{{PLAN_FILE_PATH}}`
- **Worktree:** `{{WORKTREE_PATH}}`
- **Branch:** `{{BRANCH}}`
- **Evidence commit range:** `{{COMMIT_RANGE}}`
- **Base ref:** `{{BASE_REF}}`
- **Head ref:** `{{HEAD_REF}}`
- **Closure status from main session:** `{{CLOSURE_STATUS_OR_OMITTED}}`
- **Schema path:** `{{SCHEMA_PATH}}`
- **Handback JSON path:** `{{HANDBACK_JSON_PATH}}`

Read these before writing:

- `{{PLAN_FILE_PATH}}`
- `{{THREAD_HANDOFF_PATH}}`
- `{{THREAD_JSON_PATH}}`
- `~/.claude/skills/threads/references/codex-handback.md`
- `~/.claude/skills/threads/assets/schemas/codex-handback.schema.json`
- `~/.claude/skills/threads/assets/templates/codex-handback-template.md`

## What to produce

Write exactly these two files:

- `{{WORKTREE_PATH}}/.threads/{{THREAD_ID}}/codex-handback-{{PLAN_ID}}.json`
- `{{WORKTREE_PATH}}/.threads/{{THREAD_ID}}/codex-handback-{{PLAN_ID}}.md`

Commit only those two files with subject:

```text
{{PLAN_ID}} retroactive handback: reconstruct closure from committed evidence
```

## Reconstruction rules

1. Use only committed evidence: the plan file, `thread.json`,
   `handoff.md`, commit messages/diffs in `{{COMMIT_RANGE}}`, and
   any committed test logs or findings files. Do not infer facts from
   absent chat history.

2. Mark the handback as reconstruction-grade in both artifacts. In
   JSON, put that statement in `plan_hindsight` unless a richer local
   field already exists. In Markdown, state it in the Summary.

3. `status` is the Codex progress/outcome axis:
   `complete | gate-incomplete | blocked | scope-cut`. If the main
   session already closed or superseded the plan, record that
   lifecycle state in `closure_status`.

4. Session-only arrays are not recoverable unless committed evidence
   proves them. Emit empty arrays for unrecoverable
   `discoveries[]`, `investigations[]`, `blockers[]`, and
   `follow_ons[]`. Do not invent entries to make the handback look
   complete.

5. For every plan acceptance gate, record the strongest verdict the
   committed evidence supports. If a gate's result depended on a
   local fixture, branch-only file, environment quirk, or other
   contingency, record it in that gate's `caveats[]`.

6. Do not edit source code. Do not edit thread bookkeeping files
   other than the two handback artifacts above. The main session owns
   `thread.json`, `handoff.md`, plan-hop closure prose, and registry
   regeneration.

## Validation

Before committing, validate JSON against:

```bash
python3 - <<'PY'
import json
from pathlib import Path

schema = json.loads(Path("{{SCHEMA_PATH}}").read_text())
handback = json.loads(Path("{{HANDBACK_JSON_PATH}}").read_text())
required = schema["required"]
missing = [k for k in required if k not in handback]
if missing:
    raise SystemExit(f"missing required keys: {missing}")
print("retroactive-handback-basic-schema-ok")
PY
```

If `jsonschema` is installed, run a full schema validation too.

## Final response

Reply with only:

- the two artifact paths
- JSON `status`
- JSON `closure_status` if present
- counts for `gates`, `discoveries`, `investigations`, `blockers`,
  and `follow_ons`
- validation command result
