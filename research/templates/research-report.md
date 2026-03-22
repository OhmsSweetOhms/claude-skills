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

## Suggested Next Steps

{NEXT_STEPS}

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
