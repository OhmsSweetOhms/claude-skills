---
name: research
description: "Structured technical research agent. Takes a research question and executes a multi-stage process: decompose into sub-questions, search IEEE/web/GitHub/citations in parallel, rank results by quality and relevance, and produce a vault-ready report with tiered recommendations. Use for literature surveys, finding implementations, mapping a technical field, or identifying key papers and authors. Triggers on: research, literature review, survey, find papers, what exists for, state of the art."
---

# /research — Structured Technical Research

## Entry Point

When invoked as `/research {query}`:

1. Check for domain tracker flags (`--domain-status`, `--domain-review`, `--domain-apply`). If present, jump to the "Domain Knowledge Tracker" section below.
2. Read the user's research query from the arguments
3. If no query provided, ask the user: "What would you like to research?"

## Stage 1: Decompose

**Goal:** Break the query into sub-questions and build a research plan.

Read `references/decomposition.md` for methodology.

1. Identify the **domain** (e.g., GPS/GNSS signal processing, RF design, digital communications, control systems, computer vision)
2. Check `references/domains/` for a file matching the identified domain. If found, load it — it provides conferences, journals, vendors, synonyms, known repos, and platform matching criteria for this domain.
3. Identify the **implementation target** (e.g., Zynq SoC, FPGA, software, ASIC) — may be "none" for pure literature surveys
4. Identify the **specific problem** and break it into constituent parts
5. Cross axes to generate sub-questions (algorithm-level, implementation-level, integration-level)
6. Load known conferences, journals, authors, repos from the domain reference if available; otherwise identify from query context
7. **Implementation detection:** If the query references specific block names, parameters, profiles, or a gap/robustness document, assess as `implementation` effort level. This mode emphasizes extracting concrete parameter values and design decisions rather than surveying the field.
8. Assess effort level:

| Query Type | Example | Effort Level | Roles |
|-----------|---------|-------------|-------|
| Targeted lookup | "Find the Borre 2007 GPS textbook" | targeted | web only |
| Focused technical | "VHDL Costas loop for GPS L1" | focused | ieee + code |
| Broad survey | "GPS carrier acquisition on Zynq SoC" | broad | all roles |
| Field mapping | "State of the art in FPGA-based GNSS receivers" | field_mapping | all roles + extended budget |
| Implementation research | "What anti-windup policy should my 3rd-order PLL use?" | implementation | all roles, code-as-literature emphasis |

9. Generate the research plan as JSON per `schemas/research-plan.json`. Include the domain reference file path in the plan if one was loaded.
10. Create the session directory and write the plan:

```bash
mkdir -p .research/session-$(date +%Y%m%d-%H%M%S)/{results,pdfs,blogs,app-notes,html,repos}
```

Write `plan.json` to the session directory.

11. Show the user a brief summary of the plan:
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

   PDF text extraction tries Mathpix first (high-fidelity LaTeX, tables, headings); falls back to pymupdf flat-text on any error. Provenance is written to `<stem>.extraction.json` next to `<stem>.md`. Read `references/mathpix-conversion.md` for env-var setup (`MATHPIX_APP_ID`, `MATHPIX_APP_KEY`), costs, and known limitations.

   **d) gh API results** — pipe JSON output:
   ```bash
   gh api search/repositories ... | python3 scripts/fetch_and_save.py gh-json .research/session-{id} --name "{query-summary}"
   ```

   **e) Git repos** — shallow clone repos marked `clone_repo`:
   ```bash
   python3 scripts/fetch_and_save.py clone "<repo_url>" .research/session-{id} [--name "{name}"]
   ```

   Do NOT skip this step. Do NOT use Write tool as an alternative — always use the script. The script creates directories, sanitizes names, and produces consistent output. If it came from a tool call, it goes through the script to disk.
7. Produce output JSON per `schemas/subagent-result.json`. Use `url` for URLs and `doi` for DOIs as **separate fields** — papers often have both. Set `local_file` to a **relative path only** — either session-relative (`pdfs/foo.pdf` for a fresh download) or repo-relative (`.research/session-XXX/repos/Y/file.cc` for a file in another session, `gps_iq_gen/foo.py` for a file in the project tree). **Never write absolute paths** like `/home/<user>/...`, `/Users/<user>/...`, `/media/<user>/...` or anything starting with the local project root — those leak the local user's directory layout into committed JSON. The same rule applies to any other free-text path field (e.g. `domain_reference` in plan.json, `source` in extraction.json, `local_paths[]`, `verbatim_quote` blobs that paste shell output). Set `type` explicitly (paper/thesis/repo/blog_post/app_note/tutorial/trade_article/webpage).
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

### Domain Knowledge Tracking (Between Roles)

After briefing the user on role completion, check for domain knowledge discoveries. Read `references/domain-tracker.md` for full criteria and thresholds.

1. Load the domain reference file from Stage 1 (or note its absence)
2. Compare this role's results against the domain file:
   - New conferences/journals that yielded 2+ relevant results → `conference` / `journal`
   - New repositories with 10+ stars or significant content → `known_repository`
   - Synonym variants that produced results the domain's table missed → `synonym`
   - Vendor sources with document prefix patterns not in domain file → `vendor_source`
   - Foundational references cited by 3+ results → `foundational_reference`
3. For each qualifying discovery, generate a `proposed_entry` formatted to match the domain file's existing style for that section
4. Log to `.research/session-{id}/domain-discoveries.json` per `schemas/domain-discovery.json`
5. Brief: "Domain tracker: logged {N} potential additions for {domain}."

If no domain file exists, lower thresholds — we're bootstrapping a new domain. See `references/domain-tracker.md` "New Domain Detection" for details.

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

### Domain Knowledge Tracking (Analysis Phase)

After completing gap analysis and before moving to Stage 4, review all results collectively for domain-level patterns. Read `references/domain-tracker.md` for criteria.

1. **Ranking insights:** "Papers from {venue} were consistently higher/lower quality than expected" → `ranking_note`
2. **Code search limitations:** discovered empirically during code-searcher execution → `code_search_limitation`
3. **Platform equivalences:** results for one platform transferred well to another → `platform_matching`
4. **Comparison template gaps:** design choices that differed across implementations but weren't in the template → `comparison_template_row`

Log qualifying discoveries to `.research/session-{id}/domain-discoveries.json`.

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
   - **Suggested Next Steps:** Specific actionable follow-ups. **For multi-step implementation work, recommend spawning a thread (under `<project>/.threads/<subsystem>/<slug>/`) — NOT a `docs/implementation-plan-*.md` file.** Implementation-plan docs are deprecated in favor of threads, which are designed to accrete plan hops, findings, and diagnostics across sessions. See "Recommending follow-up work" below for the decision rule and the pre-filled thread-spawn template.
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

### Refresh Cross-Project Indexes

If `<project>/.threads/` exists alongside `<project>/.research/`, regenerate the threads-side registry and the research-side index so any new session is reflected and any `spawning_thread`/`linked_research` cross-references are validated:

```bash
python3 ~/.claude/skills/threads/scripts/index_threads_research.py
```

Run from the project root (the directory containing `.research/`). The script writes `<project>/.threads/threads.json` (the threads-skill registry) and `<project>/.research/INDEX.json` (the research-side mirror with reverse `linked_by_threads[]` for each session). Add `--check` to validate without writing; the command exits 1 if the new session's `spawning_thread` references a non-existent thread, if it has the `.threads/` prefix instead of the bare `subsystem/slug` form, or if any thread's `linked_research[]` points at a missing session. If the project has no `.threads/` directory, skip this step.

### Recommending Follow-Up Work — Threads vs Docs

When writing the Suggested Next Steps section, classify each follow-up by its shape, then pick the right destination:

| If the follow-up is… | Recommend… |
|---|---|
| Multi-step implementation work spanning multiple sessions (new feature build-out, phased delivery, anything that will accrete plan hops + findings) | **A new thread** at `<project>/.threads/<subsystem>/<slug>/` |
| Hypothesis-driven debugging that will accrete findings across sessions | **A new thread** |
| A static spec, design intent, ICD, or interface contract (one-time write, doesn't evolve) | **A `docs/spec-*.md` file** |
| A one-off code change (single edit, single PR, no follow-up needed) | **Direct implementation** — no doc/thread needed |
| A research follow-up requiring more literature/code investigation | **Another `/research` session** (optionally spawned from a thread, with bidirectional `linked_research[]` / `spawning_thread` linkage) |

**Implementation-plan docs (`docs/implementation-plan-*.md`) are DEPRECATED in favor of threads.** Do not generate `docs/implementation-plan-X.md` recommendations. If the work is multi-step and will evolve, it's a thread.

**Project-conditional:** Only recommend thread spawning if `<project>/.threads/` exists. If the project has no `.threads/` directory, fall back to the appropriate `docs/spec-*.md` or `docs/<plan>.md` recommendation and note that adopting the threads skill would be useful for this kind of work.

**Pre-filled thread-spawn template.** When the report recommends a new thread, include a ready-to-run snippet so the user can adopt it without re-extracting context:

```markdown
### Recommended thread spawn

If you'd like to follow this report's <Phase / Initiative name> recommendations as a thread:

  Subsystem:        <subsystem>      (e.g. gps-receiver, fpga, scenario_engine)
  Slug:             <slug>           (e.g. l1c-phase-a, lifted from report content)
  Linked research:  session-{SESSION_ID}
  Parent doc(s):    <docs/spec-*.md path(s) if applicable>

  Suggested plan-01 scope:
    <one-paragraph scope, lifted from this report's recommendations>

  Hard constraints (lift from this session's plan.json scope_constraints):
    - <constraint>
    - <constraint>

  Related threads:
    - <existing thread slug>  (relationship: <coordinator | sibling | substrate | ...>)
```

The user invokes the threads skill (e.g. via `/threads new` or by asking "spawn a thread for X"); the threads skill writes `thread.json.linked_research[].session_id = "session-{SESSION_ID}"` back-pointing to this session, completing the bidirectional handshake. The cross-project index refresh (next sub-section) validates the linkage.

**Granularity rule.** One thread per cohesive multi-step initiative — typically grouped by phase. A "Phase A + Phase B" recommendation is two threads (or one thread that ramps from A to B via plan hops, depending on cohesion). Don't generate one thread per Suggested Next Step bullet; that's noise. Don't generate threads for Gap Analysis items either — gaps are open questions, not committed work; if/when committed, *that's* when the thread spawns.

### Final Output

Tell the user:
- Where the report is: `.research/session-{id}/report.md`
- Key numbers: total results evaluated, Tier 1 count, Tier 2 count, gaps identified
- Top 3 Tier 1 recommendations (title + one-line rationale)
- Most significant gap identified
- **If the report recommends a thread spawn:** name the proposed thread (`<subsystem>/<slug>`) and offer to invoke the threads skill to create it now. One concrete offer per cohesive initiative — don't pile up multiple thread offers in the same closing message.

### Domain Knowledge Promotion

After presenting the report, promote session discoveries to the skill-level pending file:

1. Read `.research/session-{id}/domain-discoveries.json`
2. If it has items:
   a. Append to `references/domains/_pending.json` (create if it doesn't exist)
   b. Deduplicate against existing pending items (exact match on category + summary)
   c. Assign globally unique IDs via the `next_id` counter
   d. Group under the domain key
3. If `domain_file_exists` is false and 5+ items across 3+ categories have accumulated in pending for this domain, suggest: "Enough domain knowledge to create `references/domains/{domain}.md`. Run `/research --domain-apply {domain}`."
4. Brief: "Promoted {N} domain discoveries to pending. Run `/research --domain-review` when ready."

## Effort Scaling

The skill adjusts its depth based on effort level. This table governs total behavior:

| Effort Level | Roles | Tool Calls/Role | WebFetch Depth | Report Detail |
|-------------|-------|----------------|---------------|---------------|
| targeted | 1-2 | 3-5 | 1-2 pages | Brief, focused |
| focused | 2-3 | 8-12 | 3-5 pages | Standard sections |
| broad | all 4 | 10-15 | 5-8 pages | Full report |
| field_mapping | all 4 | 15-20 | 8-12 pages | Extended landscape analysis |
| implementation | all 4 | 10-15 | 5-8 pages | Standard + Parameters Extracted + Cross-Implementation Comparison |

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

## Domain Knowledge Tracker

The research skill tracks domain knowledge discovered during sessions and provides a pathway to feed it back into domain reference files. See `references/domain-tracker.md` for full criteria and workflows.

### Commands

```
/research --domain-status                  Show pending discovery counts by domain
/research --domain-review                  Review all pending discoveries (grouped by domain/category)
/research --domain-review --session {id}   Review discoveries from a specific session
/research --domain-apply {domain}          Apply accepted items to a domain file (or create new one)
```

### `--domain-status`

Quick non-interactive summary of pending items per domain and whether a domain file exists.

### `--domain-review`

1. Load `references/domains/_pending.json`
2. If `--session {id}` specified, first promote that session's `domain-discoveries.json` items
3. Group by domain, then by category (in domain file section order)
4. For each item: show summary, detail, proposed_entry, evidence, priority
5. User can **accept**, **reject**, or **skip** each item
6. Update status in `_pending.json`

### `--domain-apply {domain}`

**If domain file exists:**
1. Filter `_pending.json` to `accepted` items for the domain
2. Load the domain file, show proposed additions grouped by section
3. Present the updated file to the user for approval
4. On approval, write the file and mark items as `applied`

**If no domain file exists (new domain):**
1. Generate a complete domain file from accepted items using existing domain files as structural template
2. Sections with no items get a placeholder note
3. Present to user for approval, then write and mark items as `applied`
