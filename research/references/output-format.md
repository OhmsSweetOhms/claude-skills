# Output Format

Final report structure, YAML frontmatter schema, and file organization.

## Session Directory

All outputs for a single research run go to:
```
.research/session-{YYYYMMDD-HHMMSS}/
├── CLAUDE.md                    # Quick-reference index for future conversations
├── plan.json                    # Stage 1: research plan
├── search-log.md                # Append-only log of all WebSearch queries + results
├── report.md                    # Stage 4: final report
├── results/
│   ├── ieee.json                # Stage 2: ieee-searcher output
│   ├── web.json                 # Stage 2: web-searcher output
│   ├── code.json                # Stage 2: code-searcher output
│   └── citations.json           # Stage 2: citation-tracer output
├── pdfs/                        # Downloaded PDFs and their text extractions
│   ├── {sanitized-filename}.pdf
│   └── {sanitized-filename}.md  # Auto-extracted text (lives next to its PDF)
├── fetched/                     # WebFetch HTML content extractions
│   └── {sanitized-filename}.md  # Extracted HTML content from WebFetch
├── repos/                       # Cloned git repos and GitHub API metadata
│   ├── {repo-name}/             # Shallow clones (--depth 1)
│   └── gh-{query}.json          # GitHub API raw results
└──
```

`report.md` is the deliverable. `results/` is the structured audit trail. `pdfs/` and `fetched/` are raw source material for reference.

### Content Persistence — Nothing Lives Only in Context

Every piece of information gathered during a research session must be saved to the session directory. The conversation context is ephemeral — it compacts, it ends. The session directory is permanent.

**What gets saved and where:**

| Source | Save To | Format |
|--------|---------|--------|
| WebSearch results | `search-log.md` (session root) | Append: role, query, numbered list of `[title](url)` |
| WebFetch HTML extractions | `fetched/{sanitized-name}.md` | Markdown with source URL header |
| PDF downloads | `pdfs/{sanitized-name}.pdf` | Binary via `scripts/fetch_and_save.py` |
| PDF text extractions | `pdfs/{sanitized-name}.md` | Auto-extracted alongside the PDF by `scripts/fetch_and_save.py` |
| gh API JSON results | `repos/gh-{query-summary}.json` | Raw JSON |
| Git repos (clone_repo) | `repos/{repo-name}/` | Shallow clone (`--depth 1`) |
| Per-role structured results | `results/{role}.json` | Per `schemas/subagent-result.json` |

**Filename sanitization:** lowercase, hyphens for spaces, no special chars, max 80 chars. Example: `leclere-comparison-framework-taes-2013.pdf`

**PDF workflow:** Use `scripts/fetch_and_save.py` — one command handles download, PDF detection, text extraction, and saving both files to `pdfs/` (the `.pdf` and its `.md` text extraction live side-by-side). Supports SSL fallback for ESA/government sites with expired certs.

## Report Frontmatter

YAML frontmatter per `schemas/research-report-frontmatter.json`:

```yaml
---
title: "Research: {descriptive title}"
type: research_report
date: {YYYY-MM-DD}
query: "{original user query verbatim}"
status: initial_sweep | deep_dive | complete
mode: single_instance
sources_searched:
  - ieee_xplore
  - github
  - web
total_results_evaluated: {int}
tier1_recommendations: {int}
tier2_recommendations: {int}
gaps_identified: {int}
tags:
  - {domain tags}
  - {platform tags}
  - {technique tags}
---
```

## Report Body Sections

### 1. Research Plan
The decomposition from Stage 1. What sub-questions were identified, what was searched, and why. Include the effort level and which roles were executed.

### 2. Landscape Summary
State of the field in 2-3 paragraphs. Who are the key authors/groups? Where is active work happening? What's the maturity level — well-established with textbook solutions, or active research frontier?

### 3. Tier 1 Recommendations
Each entry includes:
- **Title** — exact title of paper/repo/resource
- **Source** — venue, publisher, or platform
- **Year** — publication year
- **Why Tier 1** — specific rationale for ranking
- **Value to query** — what the user will get from reading this
- **DOI/URL** — direct link
- **Flags** — any special flags (foundational, has_code, platform_specific, etc.)
- **Recommended action** — read_full_paper, clone_repo, trace_citations, etc.

### 4. Tier 2 Recommendations
Same format as Tier 1, with briefer rationale.

### 5. Gap Analysis
What the search did NOT find. What's missing from the literature. Where the user might need to look beyond these sources. This section is often the most valuable — it tells you where the frontier is.

### 6. Suggested Next Steps
Specific actionable items:
- Follow-up queries to run
- Papers to trace citations from
- Authors to follow or contact
- Repos to watch or clone
- Conferences to check for upcoming presentations

### 7. Raw Results Index
Pointer to `.research/session-{id}/results/` for full per-role JSON outputs. Note which roles were executed and any that were skipped (with reason).

## Writing Style

- Be direct and specific. "This paper presents a Zynq-based GPS L1 acquisition engine using parallel code phase search with 12 correlators" is useful. "This paper discusses GPS signal processing" is not.
- Use the user's terminology where possible, but note when the literature uses different terms.
- Quantify where possible: citation counts, star counts, resource utilization numbers.
- Flag contradictions explicitly. If two papers recommend opposite approaches, say so.
- Don't pad. If there are only 3 Tier 1 results, report 3. Don't inflate Tier 2 results to fill space.
