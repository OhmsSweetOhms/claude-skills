# Codex handback — {{PLAN_ID}} — {{SESSION_DATE}}

Thread: `{{THREAD_ID}}`
Plan file: `{{PLAN_FILE_PATH}}`
Status: `{{STATUS}}`
Commit range: `{{BASE_AT_HOP_START}}..{{HEAD_AT_HANDBACK}}`

## Summary

3-5 sentences. What was done, what landed, the overall verdict in one
sentence, and the single most important thing the next plan-hop needs
to know.

## Commits

Plan commits (anchor commits get a one-line rationale; intermediates
just listed):

- `{{SHA}}` — {{subject}} *(anchor)* — {{rationale}}
- `{{SHA}}` — {{subject}} (intermediate)

Later commits already on the branch but NOT part of this hop's work
(if any) — list with note.

## Gate verdicts

### Gate {{N}} — {{name}}

Target: see `{{target_ref}}` (or quote inline if short)

Verdict: `{{pass|fail|unmeasured|retired|deferred-to-firmware}}`

Evidence: `{{evidence_path}}`

Measured: {{observed_or_measured_value}}

Notes (required if verdict is `retired`, `deferred-to-firmware`, or
the prose adds context the JSON row can't carry): one paragraph max.

## Engineering deliverables

- `{{path}}` — `{{status}}` — `{{commit_sha}}` — {{one-line summary}}

## Discoveries

Unprompted observations made while doing the plan work.

### {{discovery-1}} — {{claim restated as title}}

- **Kind:** {{free-form classifier}}
- **Evidence:** {{file:line | commit:sha | path}}
- **Why this matters:** one sentence

## Investigations

Human-directed mid-session inquiries that produced findings. Each entry
should match a `triggered_by` prompt verbatim or paraphrased.

### {{investigation-1}} — {{question restated as title}}

- **Triggered by:** "{{user prompt, verbatim or <=1 line paraphrase}}"
- **Question:** {{restate the inquiry as a question}}
- **Answer:** {{<= 5 sentences for prose; numbers go in JSON
  evidence.key_numbers}}
- **Evidence:** {{commit:sha | path}}, key numbers in JSON
- **Code anchored in:** `{{file:symbol}}`, `{{file:symbol}}`

## Blockers

(omit entire section if no blockers)

### {{blocker-1}} — {{summary}}

One paragraph: what I was trying to do, what failed, what I tried,
what's left untried. Concrete: paste the failing command + last 5
lines of stderr. Recommended owner: {{plan-NN | firmware-port | etc.}}

## Follow-ons

- {{summary}} — routing: `{{next-hop|new-thread|backlog|out-of-scope}}` — {{one-line rationale}}

## Plan hindsight

One paragraph. What would I do differently if I ran this plan again?
Was the scope right? Were the steps in the right order? "Nothing
notable" is a valid answer.

## Regression baseline

Command:

```bash
{{regression_command}}
```

Result: {{passed/failed/expected_failures}}
Evidence: `{{log_path}}`
