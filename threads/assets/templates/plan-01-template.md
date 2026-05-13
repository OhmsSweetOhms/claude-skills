# Plan: {{PLAN_TITLE}}

**Status:** active
**Thread:** {{THREAD_ID}}
**Parent plan:** {{PARENT_PLAN_REF}}

<!-- Discipline: this plan IS the Codex launch prompt when the hop hands
     to Codex. "Fleshed out" = every base section above the divider has
     non-placeholder content; if Codex hop, every Codex section below the
     divider also has non-placeholder content; no TODO/TBD markers in
     load-bearing positions. Reference workflow files
     (`~/.claude/skills/threads/references/codex-handoff.md`,
     `~/.claude/skills/threads/references/codex-handback.md`) instead of
     restating their conventions inline. -->

## Why this plan exists

{{1-2 paragraph motivation grounded in prior-hop evidence}}

## Hypothesis

{{1-3 sentences, testable}}

## Stop criteria

This plan should close or hand off to a successor hop when:

- {{First stop criterion}}
- {{Second stop criterion}}

## Steps

### Step N — {{title}}

**Goal:** {{1-2 sentence goal}}
**Deliverables:** {{bullet list}}
**Acceptance:** {{bullet list — observable, measurable}}

{{Repeat per step; typical plan has 3-7 steps.}}

## Notes

{{Free-form context; omit section if empty}}

----- Codex hop sections (fill only if this hop launches Codex) -----

## Active substrate

{{Profile / hardware constraint locking the hop to a specific configuration. Omit if no substrate binding.}}

## Worktree

**Path:** {{WORKTREE_PATH}}
**Branch:** {{BRANCH}}
**Base SHA at launch:** {{SHA}}
**Handoff inbox:** {{INBOX_PATH}}

## Hard constraints

1. {{Numbered run-specific invariants. Typical 4-10. Include don't-push, no-bypass, additive-only, baseline-deletion discipline, hardware preflight as appropriate.}}

## Counter tolerance / acceptance metrics

{{Table or bullets for numerical gates. Omit if not applicable.}}

## Codex hop shape

Worktree branch stays unpushed; merge-back to main is one terminal event
at thread close on user request, not at plan close. Handback artifacts
(handback.json + handback.md + scripts/ + temp/ + artifacts/) per
`~/.claude/skills/threads/references/codex-handback.md`.

## Cross-references

- Predecessor: {{path or "none"}}
- Successor: {{path or "TBD"}}
- Sibling threads: {{paths or "none"}}
