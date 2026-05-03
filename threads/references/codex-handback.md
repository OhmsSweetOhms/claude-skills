# Codex handback contract

A Codex handback is the structured artifact pair a Codex worktree
session writes at the end of a plan hop:

- `codex-handoff/<plan-id>/handback.json`
- `codex-handoff/<plan-id>/handback.md`

The JSON is the machine-readable closure record. The Markdown is the
human-readable companion. Together they are the only durable record
of the Codex session; chat text is not a handback and should not be
treated as evidence after the session ends.

## Where it fits

The handoff prompt tells Codex what to do. The handback tells the
main session what happened.

Template relationship:

```text
assets/templates/codex-handoff-prompt.md
  includes assets/templates/recording-discipline.md
  points Codex at:
    assets/schemas/codex-handback.schema.json
    assets/templates/codex-handback-template.md

Codex emits:
  <worktree>/codex-handoff/<plan-id>/handback.json
  <worktree>/codex-handoff/<plan-id>/handback.md
  <worktree>/codex-handoff/<plan-id>/scripts/
  <worktree>/codex-handoff/<plan-id>/temp/
  <worktree>/codex-handoff/<plan-id>/artifacts/
```

The main session reads those artifacts before closing the plan hop,
starting the next hop, or merging the worktree back.

Legacy handbacks may still exist at
`<worktree>/.threads/<thread-id>/codex-handback-<plan-id>.{json,md}`.
Consumers should read both layouts. New Codex worktree sessions use
the root `codex-handoff/<plan-id>/` inbox so `.threads/` remains
main-session owned.

## JSON schema summary

Canonical schema file:
`assets/schemas/codex-handback.schema.json`.

Top-level required fields:

- `schema_version`: currently `"2"`.
- `plan_id`: plan identifier such as `plan-03`.
- `thread_id`: thread id such as
  `scenario_engine/20260426-synth-tropo-model-for-raim-plausibility`.
- `session_date`: ISO date for the handback session.
- `status`: Codex progress/outcome status:
  `complete | gate-incomplete | blocked | scope-cut`.
- `worktree`: branch, base/head commits, optional terminal commit
  range, and `diff_stat`.
- `commits`: commit list with `sha`, `subject`, `role`, and optional
  verification metadata.
- `gates`: acceptance-gate verdicts copied from the plan.
- `discoveries`: spontaneous observations Codex found while doing
  the work.
- `investigations`: answers to user-prompted mid-session questions.
- `follow_ons`: proposed future work that Codex did not implement.
- `plan_hindsight`: one-paragraph retrospective.

Important optional fields:

- `closure_status`: separate lifecycle state copied from the plan
  hop or thread when the main session has made that decision:
  `active | blocked | superseded | closed`. Codex usually omits this
  for forward handbacks; retroactive or already-closed plans may
  include it.
- `tests`: aggregate test count and log metadata.
- `regression_baseline`: baseline command/result metadata.
- `blockers`: concrete blockers with command evidence and recommended
  owner.
- `engineering_deliverables`: path-level summary of landed, partial,
  deferred, or removed deliverables.
- `handoff_artifact_summary`: counts for helper material written
  under `codex-handoff/<plan-id>/`.
- `handoff_artifacts`: inventory of scripts, debug tests, generated
  temp files, and curated artifacts that the main session should
  promote, keep, or discard.

Gate entries must carry:

- `name`
- `verdict`: `pass | fail | unmeasured | retired |
  deferred-to-firmware`

Gate entries may also carry:

- `target_ref` or `target_lines`
- `observed` / `measured_value`
- `evidence_path`
- `summary`
- `caveats[]`: portability, reproducibility, validity, coverage,
  scope, or other caveats affecting interpretation or future reruns.
  New handbacks should emit `caveats: []` when none exist.

Schema compatibility rule: v2 remains tolerant of unknown fields so
old handbacks continue to validate. New prompts and templates define
the stricter emission discipline for current handbacks.

## Recording discipline

Every handback must answer four questions before Codex exits.

**1. What happened to the plan?** Record acceptance gates, commits,
diff stat, deliverables, regression baseline, and `status`. This is
the closure record that lets the main session update
`thread.json::plan_hops[].outcome`.

**2. What did the user ask mid-session?** User-prompted inquiries
that produced durable findings go in `investigations[]`, with the
prompt, question, answer, evidence, and code anchors.

**3. What did Codex notice on its own?** Unprompted observations go
in `discoveries[]`. If the observation requires work, set
`follow_up_needed` and explain the follow-up.

If a discovery affects a gate's future validity, portability, or
reproducibility, record it twice: once as the discovery and once on
the affected `gates[].caveats[]` entry. Do not hide a gate caveat in
free prose while emitting an otherwise clean `complete` handback.

**4. What helper material did Codex create?** Scripts, debug tests,
generated working files, and curated evidence live under the
root-level `codex-handoff/<plan-id>/` inbox:

- `scripts/` for throwaway probes, debug tests, and helpers.
- `temp/` for bulky or disposable generated working files.
- `artifacts/` for curated evidence cited by the handback.

Record useful material in `handoff_artifacts[]` with a promotion
recommendation. The main session decides what becomes `.threads/`
bookkeeping, tracked `data/`, or permanent package tests.

## Lifecycle and visibility

Handback artifacts live on the Codex worktree branch until the
terminal worktree merge-back. During a long-lived thread, a main
checkout reader may not see the files in the main working tree yet.

Required pointer convention while the handback is worktree-only:

```text
(handback: codex-handoff/<plan-id>/handback.{json,md} on worktree
branch <branch> at <worktree-head-sha>; path <absolute-worktree-path>)
```

Put that pointer in the closed plan hop's `outcome` prose on `main`
when the plan closes. The worktree path should match
`thread.json::codex_worktrees[].path`; the branch should match
`thread.json::codex_worktrees[].branch`.

After merge-back, leave the historical pointer in place unless it is
misleading. It records where the artifact lived during the thread.
If you update it, append the merge commit rather than deleting the
worktree pointer:

```text
merged to main at <merge-sha>
```

## Forward handback

Use this for the normal case: Codex executed the plan hop and emits
the handback before handing control back to the main session.

Rules:

- Codex writes both `.json` and `.md` artifacts before its final
  commit for the hop, under `codex-handoff/<plan-id>/`.
- Codex does not edit `.threads/`. The root handoff inbox is the
  only place for session handback files, helper scripts, generated
  temp files, and curated artifacts.
- Codex validates JSON against
  `assets/schemas/codex-handback.schema.json`.
- Codex may omit `closure_status` unless the main session has
  already supplied a closure decision.
- Main session reads and triages the handback before merge-back or
  next-hop activation.

Example: synth-tropo `plan-03` produced a forward handback at
worktree commit `23bb3e1`. Its discovery about the M4 fixture
portability caveat required a pre-merge follow-up, later resolved on
the worktree before merge-back.

## Retroactive handback

Use this when a plan hop was already executed or closed without a
handback artifact.

The retroactive prompt reconstructs only what the committed evidence
can support. It may use commit ranges, test logs, plan text, and any
saved handoff notes, but it must mark session-only fields as
reconstruction-grade. If the original chat is gone, do not invent
`investigations[]`, `discoveries[]`, `blockers[]`, or `follow_ons[]`;
emit empty arrays unless the evidence appears in committed files.

Example: synth-tropo `plan-02` needed a retroactive handback from the
committed worktree range ending at `4dbc564`; the reconstruction
landed at worktree commit `fcfbbdd`.

## Consumer triage

The handback is not complete until the main session consumes it.
Before merge-back or next-hop activation, classify every
`discoveries[]`, `follow_ons[]`, `investigations[]`, `blockers[]`,
and unresolved `gates[].caveats[]` item as one of:

- `pre-merge blocker`: resolve on the worktree before merge-back.
- `post-merge follow-up`: track in a new plan hop, new thread, or
  backlog after merge.
- `accepted as-is`: record as context with no code or thread action.

Use `scripts/triage_codex_handback.py` for the first pass when an
artifact is available:

```bash
python3 ~/.claude/skills/threads/scripts/triage_codex_handback.py \
  <worktree>/codex-handoff/<plan-id>/handback.json \
  --out <main-checkout>/.threads/<thread-id>/codex-handback-<plan-id>-triage.md
```

The script's classifications are recommendations. The main session
may edit them, but it must preserve one row per handback item and a
clear disposition before merge-back or next-hop activation.
