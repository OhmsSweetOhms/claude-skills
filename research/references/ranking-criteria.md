# Ranking Criteria

What makes a result worth reading vs. skimming vs. skipping.

## Tier 1 — Read in Full

Directly applicable, high quality. The user should invest time here.

A result is Tier 1 if it meets **2 or more** of:
- Describes an implementation on the target platform or close equivalent (e.g., Zynq, Artix-7, any 7-series+ for an FPGA query)
- Provides synthesizable code or detailed block diagrams with timing
- Published in a top venue (IEEE Transactions on Aerospace and Electronic Systems, ION GNSS+ proceedings, IEEE Access with high citations)
- Cited 10+ times (for papers older than 2 years)
- Open-source repo with recent activity (commits within 12 months), documentation, and clear license
- Author is a recognized expert in the domain (high h-index in this specific field)

## Tier 2 — Skim for Architecture Ideas

Relevant but indirect. Worth 15 minutes, not 2 hours.

- Algorithm description without implementation details
- Implementation on a different platform (ASIC, different FPGA family, software-only)
- Older foundational work that newer papers build on
- Tutorial or app note that covers the concept but not the specific application
- PhD thesis (often more detailed than papers but may lack peer review rigor)
- Blog post from a credible author with code snippets or block diagrams

## Tier 3 — Aware but Skip

Low immediate value. Note existence, don't invest time.

- Tangential to the query (e.g., navigation solution algorithms when the question is about acquisition)
- Superseded by newer work from the same group
- Behind paywall with no informative abstract
- Repo with no documentation, no recent commits, unclear licensing
- Content farm / SEO-optimized articles with no technical depth
- Duplicate of another result (same content on ResearchGate and IEEE)

## Special Flags

Apply these flags to any tier — they modify priority within the tier:

| Flag | Meaning | Action |
|------|---------|--------|
| `foundational` | Widely cited seminal work | Read regardless of age — understanding the field requires it |
| `novel_approach` | Unusual or unconventional method | Flag for awareness even if not directly applicable |
| `contradicts` | Conflicts with another result | **Prioritize** — discrepancies are more valuable than confirmations |
| `has_code` | Source includes downloadable implementation | Elevate within tier — code is always more useful than prose |
| `platform_specific` | Directly targets the user's platform | Elevate within tier |
| `open_access` | Full text available without paywall | Note for the user's convenience |

## Confidence Scoring

Each result gets a confidence score (0.0–1.0) for its relevance assessment:

- **0.9–1.0:** Title and abstract directly address the query; platform match; high-quality venue
- **0.7–0.8:** Clearly relevant but missing one axis (e.g., right algorithm but different platform)
- **0.5–0.6:** Probably relevant but abstract is ambiguous or incomplete
- **0.3–0.4:** Tangentially related; might contain a useful section
- **0.0–0.2:** Relevance uncertain; included only because query returned it

## Ranking Within Tiers

Within each tier, sort by:
1. Confidence score (descending)
2. Number of special flags (descending)
3. Recency (descending, except `foundational` which ignores age)
4. Citation count (descending, normalized for age)
