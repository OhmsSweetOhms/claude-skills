# Agent prompt: duplicate-helper lens

You are doing a duplicated-helper detection pass on a batch of
source files. Your job is to find code that's duplicated across
files and recommend either hoisting to a canonical owner (2-file
duplicates) or extracting a shared module (3+ files).

**Project context:**
{{PROJECT_CONTEXT}}

**Files to analyze (and grep across the wider repo):**
{{FILES}}

**Use grep aggressively. Three search strategies — use ALL of them:**

1. **By symbol name.** For each helper function in scope, grep for
   its name across the repo. Catalog occurrences.
2. **By math signature.** Grep for the constants and operations
   that define what the function does. Example patterns:
   - Bit manipulation: `& 0xFFFFFFFF`, `>> 30`, `(x >> n) & 1`,
     parity-check XOR masks.
   - Geometry: `OMEGA_E *`, `np.linalg.norm`, rotation matrices.
   - Domain-specific: {{ADDITIONAL_SEARCH_PATTERNS}}.
3. **By parameter shape and docstring.** Functions with the same
   `(arg1, arg2, ...)` pattern doing the same job, even under
   different names.

**The naming-drift trap.** Two functions can do the same math under
different names. A symbol-name grep returns "single canonical
owner" and the duplicate stays hidden. Always cross-check by math
signature before declaring "no duplicate."

**Categorize each finding:**
- `EXACT` — byte-identical or near-identical body in 2+ files.
- `NEAR` — same intent, minor variation (different constant,
  different parameter name).
- `INLINE-EQUIVALENT` — one file has a named helper, another
  inlines the same logic.
- `INTENTIONAL` — duplication is deliberate (firmware boundary,
  cyclic-dep avoidance); recommend documentation, not merge.

**Encoded biases:**
- **Don't propose a new shared module for 2-occurrence duplications.**
  Hoist to whichever file is the canonical owner — usually the
  runtime path over a legacy/test path.
- **Same code, different role ≠ duplicate.** If two implementations
  exist because they serve different roles (one is firmware-bound,
  one is simulation), that's intentional duplication. Flag for
  documentation, not consolidation.

**Project-specific anti-patterns:**
{{EXTRA_ANTI_PATTERNS}}

**Hard constraints:**
- Be precise: "lines 117-130 in file A are byte-identical to lines
  51-67 in file B" beats "they're similar."
- For each finding, name a canonical owner with rationale.
- Note any risk of behavior change if the duplicate is removed.

**Report format (under 600 words):**

Group by category (EXACT / NEAR / INLINE-EQUIVALENT / INTENTIONAL).
Lead with EXACT.

For each finding:
- Helper: `<function or concept name>`
- Locations: `<file:line-range>` × N
- Category: <EXACT | NEAR | INLINE-EQUIVALENT | INTENTIONAL>
- Canonical owner: `<file>` — <one-line rationale>
- Recommendation: <hoist to <file> | new shared module | document>
- Risk note: <one line on whether removal could change behavior>
