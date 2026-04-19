# Plan: {{PLAN_TITLE}}

**Status:** active
**Thread:** {{THREAD_ID}}
**Parent plan:** {{PARENT_PLAN_REF}}

## Hypothesis

{{HYPOTHESIS_PARAGRAPH}}

## What we know so far

{{WHAT_WE_KNOW}}

## Stop criteria

This plan should close or hand off to a successor hop when:

- {{STOP_CRITERION_1}}
- {{STOP_CRITERION_2}}

## Steps

1. {{FIRST_STEP}}
2. {{SECOND_STEP}}
3. *(add more as the investigation unfolds)*

## Expected artifacts

- Diagnostic scripts in `diagnostics/` —
  e.g., `diagnose_{{KEBAB_SLUG}}.py`
- Regeneratable outputs in `temp/` —
  e.g., per-event CSV captures
- Findings snapshot `findings-YYYY-MM-DD.md` when the hop closes

## Notes

*(free-form notes as the plan evolves; when a new hypothesis
branches off, close this hop and open plan-02-*)*
