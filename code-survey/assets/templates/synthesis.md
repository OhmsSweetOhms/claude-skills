# Code-survey synthesis — {{PROJECT_NAME}}

**Run:** `{{RUN_TIMESTAMP}}`
**Scope:** {{SCOPE_DESCRIPTION}}
**Lenses:** {{LENSES_RUN}}
**Model per lens:** {{LENS_MODELS_USED}}

## Executive summary

{{EXECUTIVE_SUMMARY_PARAGRAPH}}

**Verdict:** `{{CLEAN | CLEANUP-WORTHY | SPRINT-WORTHY}}`

## Cross-pass-reinforced findings (highest confidence)

These showed up in two or more lenses. Treat as high-confidence
findings worth acting on.

| ID | Lenses | File / Symbol | Description | Recommendation |
|---|---|---|---|---|
| {{ID}} | {{LENSES}} | {{FILE}} | {{DESCRIPTION}} | {{RECOMMENDATION}} |

## Per-lens highlights

### file-level monolith
{{TOP_FINDINGS_LENS_1}}

### function-level long-method
{{TOP_FINDINGS_LENS_2}}

### duplicate-helper
{{TOP_FINDINGS_LENS_3}}

{{OPTIONAL_LENS_4_SECTION}}
{{OPTIONAL_LENS_5_SECTION}}
{{OPTIONAL_LENS_6_SECTION}}
{{OPTIONAL_LENS_7_SECTION}}
{{OPTIONAL_LENS_8_SECTION}}

## Recommendation table

Sorted by priority, then risk. Effort is rough — within ~2× of reality.

| # | Priority | Risk | File | Item | Recommendation | Verification | Effort |
|---|---|---|---|---|---|---|---|
| {{N}} | {{P1|P2|P3|P4}} | {{low|medium|high}} | {{FILE}} | {{ITEM}} | {{REC}} | {{V}} | {{EFFORT}} |

## Proposed verification policy

{{VERIFICATION_POLICY_PARAGRAPH}}

**Low/medium-risk batch (suggested):**
- {{LIST_OF_LOW_RISK_ITEMS}}
- Single E2E gate at batch end: `{{E2E_COMMAND}}`. Pass criteria: `{{E2E_PASS_CRITERIA}}`.

**High-risk per-item:**
- {{LIST_OF_HIGH_RISK_ITEMS}}
- Each gets its own E2E gate after landing.

## Thread-worthy verdict

{{THREAD_WORTHY_VERDICT_PARAGRAPH}}

**Next step:** `{{/code-survey propose-thread | direct commits}}`.

## Did NOT surface (filtered out)

These findings were considered but filtered. Listed for legibility
so a future user knows what was ruled out, and why.

- {{FILE}}: {{FINDING}} — filtered: physics-floor (residual {{X}} below floor {{Y}}).
- {{FILE}}: {{FINDING}} — filtered: keep_rules match ({{RULE}}).
- {{FILE}}: {{FINDING}} — filtered: boundaries match ({{RULE}}).
- {{FILE}}: {{FINDING}} — filtered: contradictory verdicts across lenses; awaiting user review.

## Run metadata

- Files surveyed: {{N_FILES}}
- Per-lens agent count: {{AGENT_COUNTS}}
- Total agent reports: {{N_REPORTS}}
- Synthesis duration: {{SYNTHESIS_DURATION}}
- Config snapshot: `config.snapshot.json`
- Scope record: `scope.json`
