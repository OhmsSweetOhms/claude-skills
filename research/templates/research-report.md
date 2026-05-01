---
title: "Research: {TITLE}"
type: research_report
date: {DATE}
query: "{QUERY}"
status: initial_sweep
mode: single_instance
sources_searched:
  - {SOURCES}
total_results_evaluated: {TOTAL}
tier1_recommendations: {TIER1}
tier2_recommendations: {TIER2}
gaps_identified: {GAPS}
tags:
  - {TAGS}
---

# {TITLE}

**Query:** {QUERY}
**Date:** {DATE}
**Session:** `.research/session-{SESSION_ID}/`

## Research Plan

{PLAN_SUMMARY}

### Sub-Questions Investigated
{SUB_QUESTIONS}

### Roles Executed
{ROLES_EXECUTED}

---

## Landscape Summary

{LANDSCAPE}

---

## Tier 1 Recommendations

{TIER1_ENTRIES}

---

## Tier 2 Recommendations

{TIER2_ENTRIES}

---

## Gap Analysis

{GAPS_ANALYSIS}

---

## Parameters Extracted

{PARAMETERS_TABLE}
<!-- Include this section only when effort level is "implementation". Delete if not applicable. -->

---

## Cross-Implementation Comparison

{COMPARISON_TABLE}
<!-- Include this section only when effort level is "implementation" and multiple implementations exist. Delete if not applicable. -->

---

## Suggested Next Steps

{NEXT_STEPS}
<!--
Classify each follow-up item before writing it:

  - Multi-step implementation work (new feature, phased delivery, anything
    that will accrete plan hops + findings across sessions) → recommend
    spawning a thread under <project>/.threads/<subsystem>/<slug>/.
    DO NOT recommend creating docs/implementation-plan-*.md — those are
    deprecated in favor of threads.
  - Hypothesis-driven debugging spanning multiple sessions → thread.
  - Static spec / design intent / interface contract → docs/spec-*.md.
  - One-off code change → direct implementation, no doc/thread needed.
  - Research follow-up needing more literature/code → another /research
    session, optionally spawned from a thread.

If the project has no .threads/ directory, fall back to docs/spec-*.md
or docs/<plan>.md and note that adopting the threads skill would help.

For a multi-step initiative, include a pre-filled thread-spawn block:

  ### Recommended thread spawn

  Subsystem:        <subsystem>      (e.g. gps-receiver, fpga, scenario_engine)
  Slug:             <slug>           (lift from this report's Phase / Initiative name)
  Linked research:  session-{SESSION_ID}
  Parent doc(s):    <docs/spec-*.md path(s) if applicable>

  Suggested plan-01 scope:
    <one-paragraph scope, lifted from this report's recommendations>

  Hard constraints (lift from this session's plan.json scope_constraints):
    - <constraint>
    - <constraint>

  Related threads:
    - <existing thread slug>  (relationship: <coordinator | sibling | substrate | ...>)

The threads skill will write thread.json.linked_research[] back-pointing
here, completing the bidirectional handshake. Granularity: one thread
per cohesive multi-step initiative, typically grouped by phase.
Don't generate one thread per Suggested Next Step bullet.
-->

---

## Domain Knowledge Discovered

{DOMAIN_DISCOVERIES_TABLE}
<!-- Include when domain-discoveries.json has items. Delete section if none. -->

---

## Raw Results Index

Session directory: `.research/session-{SESSION_ID}/`

| File | Role | Status |
|------|------|--------|
| `results/ieee.json` | IEEE Searcher | {IEEE_STATUS} |
| `results/web.json` | Web Searcher | {WEB_STATUS} |
| `results/code.json` | Code Searcher | {CODE_STATUS} |
| `results/citations.json` | Citation Tracer | {CITATIONS_STATUS} |
| `plan.json` | Research Plan | completed |
