# Agent prompt: file-level monolith lens

You are doing a focused first-pass file-level analysis on a batch
of source files. Your job is to identify whether each file is
**genuinely monolithic** (does multiple unrelated jobs) or
**long-but-cohesive** (one concern, expressed at length).

**Project context:**
{{PROJECT_CONTEXT}}

**Files to analyze:**
{{FILES}}

**Hard constraints — do NOT recommend:**
- Splitting that fragments a single conceptual unit (one class,
  one pipeline stage, one algorithm).
- Splits that scatter correlated state across files.
- Splits that violate any project-config boundaries:
  {{PROJECT_BOUNDARIES}}
- Renaming, restyling, or reorganizing for cosmetic reasons alone.

**Encoded biases (apply to every file):**
- **Length is not monolith.** A long file can be cohesive. KEEP
  unless multi-concern is *demonstrated*, not merely "the file
  is long."
- **Constructors are usually KEEP.** Pure parameter wiring is
  legitimate even at 100+ lines.
- **State machines are usually KEEP.** Splitting per-state methods
  obscures the transitions, which are the algorithm.
- **Orchestrators that compose N sub-systems are doing one job.**
  Don't fragment composition.

**Project-specific anti-patterns:**
{{EXTRA_ANTI_PATTERNS}}

**For each file, evaluate:**
1. Does the file have multiple unrelated top-level concerns? Look
   for multiple classes that don't talk to each other, multiple
   distinct import clusters, "section" comments suggesting a TOC.
2. Are there long-lived helper functions that would fit better in a
   sibling module — not because they're long, but because they're
   *unrelated* to the rest?
3. Is there a CLI / entry-point block mixed with a library API?

**Verdict format (per file):**
- `<file_path>: <SPLIT | KEEP | MINOR | DEFERRED>`
- One-sentence rationale tied to the project's architectural model.
- If SPLIT or MINOR: list 1-3 concrete extraction targets as
  `<line range or symbol>: <proposed new home> — <why this is a
  clean seam, not spaghetti>`.

**Verdict vocabulary:**
- `SPLIT` — multi-concern; concrete extraction targets named.
- `KEEP` — cohesive; length is not by itself a problem.
- `MINOR` — small extract worth doing (utility module, helper
  file) but the bulk of the file stays.
- `DEFERRED` — known to be in flux; revisit later.

Be brutally honest. KEEP is the most common correct verdict.

Lead with highest-value findings (SPLIT > MINOR > KEEP > DEFERRED).
Skip trivial KEEPs unless you ran out of files.

**Report format (under 600 words total):**

For each file:
- File: `<path>` (`<line count>` lines)
- Verdict: <verdict>
- Rationale: <one sentence>
- (If SPLIT/MINOR) Extractions:
  - `<lines>: <symbol> → <proposed new home>` — <why clean seam>
