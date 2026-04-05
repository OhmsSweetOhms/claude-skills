# Search Strategy

Query construction and search execution heuristics.

## Core Rules

1. **Start broad, then narrow.** Begin with 2-3 word queries. Evaluate what comes back. Add specificity only when needed.

2. **Run at least 3 variant queries per role** before concluding a gap exists. If the first query returns nothing, the query is probably wrong, not the literature.

3. **Zero results → broaden.** Remove the most specific term. "Costas loop VHDL GPS L1" → "Costas loop GPS FPGA" → "GPS carrier tracking FPGA".

4. **50+ results → narrow.** Add platform or technique qualifier. "GNSS receiver" → "GNSS receiver FPGA Zynq".

5. **Track every query.** Record what was searched, which tool, and how many results came back. This audit trail goes in the raw results JSON for reproducibility.

## Query Construction by Role

### IEEE Searcher
- Use academic terminology, not colloquial terms
- Try both keyword search and metadata search (author, conference name)
- Include Boolean operators: `"GPS acquisition" AND "FPGA"` is better than `GPS acquisition FPGA`
- Search specific conferences: use `ieee_search_in_publication` with venue name + keywords
- Year filtering: default to last 10 years; extend to 20 if looking for foundational work
- If IEEE MCP is unavailable, fall back to web search with `site:ieeexplore.ieee.org` queries

### Web Searcher
- Include vendor-specific terms alongside generic terms: "Xilinx GPS receiver" not just "GPS receiver"
- Use tutorial/guide language: "how to implement", "design guide", "application note"
- Search for specific document types: "XAPP" (Xilinx app note), "UG" (user guide), "AN" (app note)
- Include trade publication names: "Inside GNSS", "GPS World"
- Try author names found by other roles
- For theses: add "thesis" or "dissertation" to the query

### Code Searcher
- **Repo search works, code search doesn't** (especially for HDL languages)
- Use `gh api search/repositories` with broad terms
- Language filter (`language:VHDL`) is useful but dramatically narrows results — run with AND without
- Search by known project names first (gnss-sdr, GNSS-VHDL, etc.)
- Topic tags are sparse in FPGA/GNSS domain — don't rely on them
- After finding repos, inspect with `gh api repos/{owner}/{name}` and `gh api repos/{owner}/{name}/contents` for structure
- Check README content: `gh api repos/{owner}/{name}/readme --jq .content` (base64 encoded)

### Citation Tracer
- Needs seed papers — runs AFTER ieee-searcher
- Use Semantic Scholar paper search with titles or DOIs from ieee-searcher results
- Trace forward citations (who cited this?) for recent work
- Trace backward citations (what does this cite?) for foundational work
- Look for papers that appear in multiple seed papers' references — likely foundational

## Synonym Expansion

Technical terms often have multiple names. Load the domain-specific synonym table from `references/domains/{domain}.md` if a domain file was loaded in Stage 1. If no domain file exists, generate synonyms from the query's technical terms.

Example (from GNSS domain):

| User's Term | Synonyms to Try |
|-------------|-----------------|
| GPS acquisition | signal acquisition, code phase search, coarse acquisition |
| Carrier tracking | carrier loop, PLL, FLL, Costas loop |

Always generate at least 2-3 synonyms per key technical term, even without a domain file.

## When to Stop

A role is done when:
- At least 3 variant queries have been executed
- Tier 1 results are saturating (new queries return papers already seen)
- OR the tool call budget is exhausted
- OR 3 consecutive queries return zero new relevant results

Do NOT stop just because the first query returned good results — there may be better work under different terminology.
