# Domain Knowledge Tracker

How to identify, log, and apply domain knowledge discovered during research sessions.

## Purpose

Research sessions surface domain knowledge that isn't in (or doesn't yet have) a domain reference file. This tracker captures those discoveries and provides a pathway to feed them back into domain files, improving future research quality.

## When Tracking Runs

Tracking hooks execute at two points in the pipeline:

1. **Between Roles (Stage 2)** — after each role completes, compare its results against the loaded domain file
2. **Analysis Phase (Stage 3)** — after gap analysis, review all results for higher-level domain patterns

Tracking is **non-intrusive**. Claude suggests items; the user decides what to log. Never auto-log without user awareness.

## What IS Domain Knowledge

Domain knowledge is reusable across research sessions in the same domain. It tells future sessions *where to look* and *how to evaluate*, not *what was found*.

### Track These (with thresholds)

| Category | Threshold | Example |
|----------|-----------|---------|
| `conference` | Yielded 2+ relevant results not already in domain file | "IEEE Aerospace Conference had 3 FPGA/GNSS papers" |
| `journal` | Yielded 2+ relevant results not already in domain file | "GPS Solutions (Springer) had receiver tuning papers" |
| `trade_publication` | Found useful content from a publication not in domain file | "Navipedia has detailed PLL design articles" |
| `vendor_source` | Found vendor app notes/docs with a document prefix pattern | "TI has SWRA-prefixed RF app notes" |
| `synonym` | A search variant produced results the original term missed | "'carrier recovery' found papers that 'carrier tracking' missed" |
| `known_repository` | Repo has 10+ stars OR yielded significant content, not in domain file | "PocketSDR — compact Python/C SDR receiver with tracking loops" |
| `foundational_reference` | Cited by 3+ results in this session, or identified as canonical by multiple sources | "Van Dierendonck 1996 — discriminator taxonomy, cited by 5 papers found" |
| `ranking_note` | Observed pattern that should inform future result evaluation | "Papers validated on pilot channels don't transfer to data-only signals" |
| `code_search_limitation` | Empirically discovered search gap for a language/platform | "SystemVerilog testbenches have zero GitHub code search coverage" |
| `platform_matching` | Discovered architectural equivalence or non-equivalence | "Zynq UltraScale+ results transfer well to Versal ACAP" |
| `comparison_template_row` | Design choice differing across implementations that wasn't in the comparison template | "AGC policy differs across receivers — not tracked in template" |

### Do NOT Track

- **Individual papers** — a paper is a session result, not domain knowledge
- **Blog posts or tutorials** — session-specific content
- **Specific parameter values** — those go in the report's Parameters Extracted section
- **Session-specific gaps** — those go in the report's Gap Analysis section
- **One-off authors** — need recurrence across results to qualify as domain knowledge
- **URLs that happened to have useful content** — unless they represent a recurring source

## New Domain Detection

When Stage 1 identifies a domain and finds **no matching file** in `references/domains/`:

1. Note `domain_file_exists: false` in the session discoveries
2. **Lower thresholds** — in a known domain, a conference needs 2+ results to be worth logging. In an unknown domain, every conference that yields relevant results is worth logging because we're bootstrapping from zero
3. Track more aggressively: any conference, journal, repo, or synonym that proves useful
4. At promotion time, if 5+ items spanning 3+ categories have accumulated (across sessions), suggest creating a new domain file

## Between-Roles Tracking Procedure

After each role completes and before briefing the user:

1. Load the domain reference file from Stage 1 (or note its absence)
2. For each result from this role, check against the domain file:
   - Is the conference/journal new? Did it yield 2+ results? → `conference` or `journal`
   - Is the repository new? Does it have 10+ stars or useful content? → `known_repository`
   - Did a synonym variant produce results the domain's table missed? → `synonym`
   - Is there a vendor source with a document prefix pattern not in the domain file? → `vendor_source`
   - Is there a foundational reference cited by multiple results? → `foundational_reference`
3. For each qualifying discovery:
   - Generate the `proposed_entry` formatted to match the domain file's existing table/paragraph style for that section
   - Log to `.research/session-{id}/domain-discoveries.json`
4. In the between-roles brief, add: "Domain tracker: logged {N} potential additions for {domain}."

## Analysis-Phase Tracking Procedure

After Stage 3 gap analysis, before moving to Stage 4:

1. Review all results collectively (across roles) for domain-level patterns:
   - **Ranking insights:** "Papers from {venue} were consistently higher/lower quality than expected" → `ranking_note`
   - **Code search limitations:** discovered empirically during code-searcher → `code_search_limitation`
   - **Platform equivalences:** results for platform X transferred to platform Y → `platform_matching`
   - **Comparison template gaps:** design choices that differed across implementations but weren't in the template → `comparison_template_row`
2. Log qualifying discoveries to session `domain-discoveries.json`

## Promotion: Session → Pending

At the end of Stage 4, after writing the report:

1. Read `.research/session-{id}/domain-discoveries.json`
2. If it has items, append to `references/domains/_pending.json`:
   - Create the file if it doesn't exist
   - Deduplicate against existing items (exact match on category + summary)
   - Assign globally unique IDs via `next_id` counter
   - Group under the domain key
3. If `domain_file_exists` is false and 5+ items across 3+ categories have accumulated in pending for this domain, suggest: "Enough domain knowledge to create `references/domains/{domain}.md`. Run `/research --domain-apply {domain}`."
4. Brief: "Promoted {N} domain discoveries to pending. Run `/research --domain-review` when ready."

## Review Workflow (`/research --domain-review`)

1. Load `references/domains/_pending.json`
2. If `--session {id}` specified, also load that session's `domain-discoveries.json` and promote any `discovered` items first
3. Group items by domain, then by category (in domain file section order: Conferences → Journals → Trade Publications → Vendor Sources → Synonym Expansion → Known Repositories → Foundational References → Ranking Notes → Code Search Limitations → Platform Matching → Comparison Template)
4. For each item show: summary, detail, proposed_entry, evidence, priority
5. User can: **accept**, **reject**, or **skip** each item
6. Update status in `_pending.json`
7. Summary: "Reviewed {N} items: {A} accepted, {R} rejected, {S} skipped."

## Apply Workflow (`/research --domain-apply {domain}`)

### Existing domain file

1. Filter `_pending.json` to `accepted` items for the domain
2. Load the domain file (`references/domains/{domain}.md`)
3. For each category group:
   - Show the current section content
   - Show the proposed additions (from `proposed_entry`)
   - Generate the merged section
4. Present the complete updated domain file to the user
5. On approval, write the file
6. Mark all applied items as `status: "applied"` in `_pending.json`

### New domain file

1. Filter `_pending.json` to `accepted` items for the domain
2. Generate a complete domain file using existing domain files as structural template
3. Populate each section from accepted items
4. Sections with no items get a placeholder: "No entries yet — will be populated as research sessions discover relevant sources."
5. Present to user for approval, then write
6. Mark items as `applied`

## Status Check (`/research --domain-status`)

Quick non-interactive summary:

```
Domain knowledge tracker:
  gnss-signal-processing-soc: 3 pending (1 conference, 2 synonyms)
  gnss-signal-processing-control-loops: 1 pending (1 known_repository)
  radar-signal-processing: 5 pending (new domain — no file exists yet)
  Total: 9 pending across 3 domains
```
