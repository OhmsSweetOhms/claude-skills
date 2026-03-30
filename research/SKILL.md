---
name: research
description: "Structured technical research agent. Takes a research question and executes a multi-stage process: decompose into sub-questions, search IEEE/web/GitHub/citations in parallel, rank results by quality and relevance, and produce a vault-ready report with tiered recommendations. Use for literature surveys, finding implementations, mapping a technical field, or identifying key papers and authors. Triggers on: research, literature review, survey, find papers, what exists for, state of the art."
---

# /research — Structured Technical Research

## Entry Point

When invoked as `/research {query}`:

1. Read the user's research query from the arguments
2. If no query provided, ask the user: "What would you like to research?"

## Stage 1: Decompose

**Goal:** Break the query into sub-questions and build a research plan.

Read `references/decomposition.md` for methodology.

1. Identify the **domain** (e.g., GPS/GNSS signal processing, RF design, digital communications)
2. Identify the **implementation target** (e.g., Zynq SoC, FPGA, software, ASIC) — may be "none" for pure literature surveys
3. Identify the **specific problem** and break it into constituent parts
4. Cross axes to generate sub-questions (algorithm-level, implementation-level, integration-level)
5. Identify known conferences, journals, authors, repos for this domain
6. Assess effort level:

| Query Type | Example | Effort Level | Roles |
|-----------|---------|-------------|-------|
| Targeted lookup | "Find the Borre 2007 GPS textbook" | targeted | web only |
| Focused technical | "VHDL Costas loop for GPS L1" | focused | ieee + code |
| Broad survey | "GPS carrier acquisition on Zynq SoC" | broad | all roles |
| Field mapping | "State of the art in FPGA-based GNSS receivers" | field_mapping | all roles + extended budget |

7. Generate the research plan as JSON per `schemas/research-plan.json`
8. Create the session directory and write the plan:

```bash
mkdir -p .research/session-$(date +%Y%m%d-%H%M%S)/{results,pdfs,blogs,app-notes,html,repos}
```

Write `plan.json` to the session directory.

9. Show the user a brief summary of the plan:
   - Number of sub-questions
   - Roles to execute
   - Effort level
   - Estimated tool calls

Ask: "Ready to proceed, or would you like to adjust the plan?"

## Stage 2: Collect

**Goal:** Execute each role sequentially, collecting results.

Read the role document for each role before executing it. The role docs are in `roles/`:
- `roles/ieee-searcher.md` — IEEE Xplore academic papers
- `roles/web-searcher.md` — tutorials, app notes, blogs, theses
- `roles/code-searcher.md` — GitHub/GitLab repositories
- `roles/citation-tracer.md` — citation network tracing (depends on ieee-searcher results)

### Execution Order

1. **ieee-searcher** (or skip if not in `roles_to_execute`)
2. **web-searcher** (can run independently)
3. **code-searcher** (can run independently, but check for `handoff_items` from ieee/web)
4. **citation-tracer** (runs LAST — needs seed papers from ieee-searcher)

### For Each Role

1. Read the role document from `roles/`
2. Take the sub-questions assigned to this role from the plan
3. Also take any `handoff_items` targeting this role from previously-completed roles
4. Execute the search strategy per `references/search-strategy.md`
5. For each result found, assess relevance (high/medium/low) with rationale
6. **MANDATORY: Save ALL content to the session directory** using `scripts/fetch_and_save.py`. Nothing should exist only in conversation context. The script handles all content types — use it for everything.

   **a) WebSearch results** — after each WebSearch call, pipe the results list:
   ```bash
   echo "1. [Title](url)
   2. [Title](url)" | python3 scripts/fetch_and_save.py search-log .research/session-{id} --role "{role}" --query "{query}"
   ```

   **b) WebFetch extractions** — after each WebFetch call, pipe the content with content type:
   ```bash
   echo "{extracted content}" | python3 scripts/fetch_and_save.py webfetch .research/session-{id} --name "{name}" --url "{source_url}" --type "{content_type}"
   ```
   Content types: `blog_post`, `tutorial`, `forum_thread` → `blogs/`; `app_note`, `trade_article` → `app-notes/`; anything else → `html/`

   **c) PDF downloads** — auto-detects PDF, downloads, extracts text:
   ```bash
   python3 scripts/fetch_and_save.py fetch "<url>" .research/session-{id} --name "{name}"
   ```

   **d) gh API results** — pipe JSON output:
   ```bash
   gh api search/repositories ... | python3 scripts/fetch_and_save.py gh-json .research/session-{id} --name "{query-summary}"
   ```

   **e) Git repos** — shallow clone repos marked `clone_repo`:
   ```bash
   python3 scripts/fetch_and_save.py clone "<repo_url>" .research/session-{id} [--name "{name}"]
   ```

   Do NOT skip this step. Do NOT use Write tool as an alternative — always use the script. The script creates directories, sanitizes names, and produces consistent output. If it came from a tool call, it goes through the script to disk.
7. Produce output JSON per `schemas/subagent-result.json`. Use `url` for URLs and `doi` for DOIs as **separate fields** — papers often have both. Set `local_file` to the relative session path after saving content. Set `type` explicitly (paper/thesis/repo/blog_post/app_note/tutorial/trade_article/webpage).
8. Write to `.research/session-{id}/results/{role}.json`
9. Collect `handoff_items` for subsequent roles

### Tool Availability Check

Before executing each role, verify its tools are available:

- **ieee-searcher:** Try calling `ieee_search` with a simple query. If MCP tools are not available, switch to fallback mode (see `roles/ieee-searcher.md` Fallback Mode section).
- **code-searcher:** Verify `gh auth status` succeeds. If not authenticated, skip and note in output.
- **citation-tracer:** Check if Semantic Scholar MCP tools are available. If not, use web search fallback mode.
- **web-searcher:** Always available (uses built-in WebSearch).

### Between Roles

After each role completes:
- Brief the user: "{role} complete. Found {N} results ({tier1} high relevance, {tier2} medium). Moving to {next_role}."
- Pass `handoff_items` to the next role's input

## Stage 3: Analyze

**Goal:** Evaluate all collected results, identify patterns, rank by quality.

Read `references/ranking-criteria.md` for the ranking methodology.

1. Load all results from `.research/session-{id}/results/`
2. Deduplicate — same paper/repo found by multiple roles
3. Apply ranking criteria to assign tiers:
   - **Tier 1:** Read in full — directly applicable, high quality (2+ qualifying criteria)
   - **Tier 2:** Skim for ideas — relevant but indirect
   - **Tier 3:** Aware but skip — low immediate value (don't include in report)
4. Apply special flags: `foundational`, `novel_approach`, `contradicts`, `has_code`, `platform_specific`, `open_access`
5. Sort within tiers per ranking criteria (confidence → flags → recency → citations)
6. Identify gaps — what sub-questions got no good answers?
7. Look for contradictions between sources — flag these prominently
8. Compile cross-references — which results cite or relate to each other?

## Stage 4: Report

**Goal:** Produce the final vault-ready report.

Read `references/output-format.md` for structure and style guide.
Read `templates/research-report.md` for the skeleton.

1. Generate YAML frontmatter per `schemas/research-report-frontmatter.json`
2. Write each section:
   - **Research Plan:** Summarize Stage 1 decomposition
   - **Landscape Summary:** 2-3 paragraphs on the state of the field
   - **Tier 1 Recommendations:** Full entries with rationale, DOI/URL, flags, recommended action
   - **Tier 2 Recommendations:** Same format, briefer rationale
   - **Gap Analysis:** What wasn't found, where to look next
   - **Suggested Next Steps:** Specific actionable follow-ups
   - **Raw Results Index:** Pointers to session directory files
3. Write `report.md` to the session directory
4. Write `CLAUDE.md` to the session directory (see below)
5. Generate `session-manifest.json` (see below)
6. Present the report to the user

### Session CLAUDE.md

Write a short `CLAUDE.md` at the session root for fast context loading in future conversations. This file should be **under 30 lines** and contain:

```markdown
# Session {YYYYMMDD-HHMMSS}

**Query:** {one-line summary of the query}
**Date:** {YYYY-MM-DD}
**Status:** {complete | partial}
**Effort:** {effort_level} ({roles executed summary})

## Key Findings
- {3-5 bullet points: the most important takeaways}

## Top Resources
- `pdfs/{filename}` — {one-line description}
- `pdfs/{filename}` — {one-line description}
- `pdfs/{filename}` — {one-line description}

## Caveats
- {any limitations, fallback modes used, missing data}
```

Keep it factual and terse. This is an index, not a summary — point to files, don't repeat content. The report has the detail; this file exists so future conversations can orient in seconds.

### Session Manifest

Generate `session-manifest.json` by running the manifest generator script. This file is the single source of truth for vault generators and other tools that consume session output programmatically.

```bash
python3 scripts/gen_manifest.py .research/session-{id} \
  --title "{report title from frontmatter}" \
  --query "{original user query verbatim}"
```

The script scans `pdfs/`, `blogs/`, `app-notes/`, `html/`, and `repos/` directories, extracts metadata from file headers and GitHub API JSON, and writes the structured manifest. Schema: `schemas/session-manifest.json`.

### Final Output

Tell the user:
- Where the report is: `.research/session-{id}/report.md`
- Key numbers: total results evaluated, Tier 1 count, Tier 2 count, gaps identified
- Top 3 Tier 1 recommendations (title + one-line rationale)
- Most significant gap identified

## Effort Scaling

The skill adjusts its depth based on effort level. This table governs total behavior:

| Effort Level | Roles | Tool Calls/Role | WebFetch Depth | Report Detail |
|-------------|-------|----------------|---------------|---------------|
| targeted | 1-2 | 3-5 | 1-2 pages | Brief, focused |
| focused | 2-3 | 8-12 | 3-5 pages | Standard sections |
| broad | all 4 | 10-15 | 5-8 pages | Full report |
| field_mapping | all 4 | 15-20 | 8-12 pages | Extended landscape analysis |

## Error Handling

- **MCP tool unavailable:** Switch to fallback mode for that role (documented in each role doc). Note in report which roles used fallback.
- **API rate limit:** Stop that role, write partial results, note the limit in gaps.
- **Zero results from a role:** This IS a finding. Document it in gap analysis. Try 3 variant queries before concluding.
- **gh not authenticated:** Skip code-searcher, note in report.

## Session Management

All session data persists in `.research/session-{YYYYMMDD-HHMMSS}/`. The user can:
- Re-read any previous report
- Compare results across sessions
- Use raw JSON results for further analysis

Never overwrite a previous session. Each `/research` invocation creates a new session directory.
