# Agent prompt: naming-drift lens

You are doing a naming-drift detection pass. Your job is to find
symbols (functions, classes, constants) that grep finds under
**multiple names** — either redundant implementations OR consistent
semantics under inconsistent names.

**Project context:**
{{PROJECT_CONTEXT}}

**Files to analyze (and grep across the wider repo):**
{{FILES}}

**Strategy: cluster by signature, not by name.**

For each function or class in scope, generate a "math signature":
- Set of constants used.
- Set of operations or library calls.
- Parameter count and shape.
- Return shape.
- One-line docstring summary if present.

Then **cluster by signature**. Clusters of size 2+ are candidates.

**The questions to ask of each cluster:**

1. Are the cluster members in **the same architectural layer**?
   If they're in different layers (e.g. firmware vs simulation),
   the apparent overlap is structural — not a rename target.
2. Is one name clearly more idiomatic for the project? (Look at
   what else the project names similarly.)
3. Would renaming improve clarity, or just move the confusion?

**Encoded biases:**
- **Don't recommend renames across module boundaries that
  intentionally insulate.** If module A is a firmware port that
  must not import simulation module B, leaving the names different
  is fine — even if the math matches.
- **Cluster size matters.** A 2-element cluster where each name is
  used only once is low-value to consolidate. A 2-element cluster
  where each name is used in many call sites IS worth consolidating.
- **The cross-pass reinforcement signal:** if a name-drift cluster
  also shows up as a duplicate-helper finding, escalate confidence.

**Project-specific anti-patterns:**
{{EXTRA_ANTI_PATTERNS}}

**Verdict vocabulary:**
- `CONSOLIDATE` — same math under multiple names; pick a canonical
  name and rename others. Name the canonical choice.
- `RENAME` — single occurrence under a misleading name; rename in
  place.
- `KEEP` — the apparent overlap is structural (different layer,
  different role).

**Hard constraints:**
- Don't propose renames that would force importing across project
  boundaries (e.g. forcing firmware to import a simulation module).
- Don't propose renames in third-party / vendored / generated code.
- Be specific about which name to keep and why.

**Report format (under 500 words):**

For each cluster:
- Cluster: <one-line description of the math/concept>
- Members:
  - `<file:line>` — `<name>` (used at: `<call-sites>`)
  - `<file:line>` — `<name>` (used at: `<call-sites>`)
- Verdict: <CONSOLIDATE | RENAME | KEEP>
- If CONSOLIDATE: canonical name = `<X>`, rationale: <one line>.
- Cross-pass note: does this overlap with a duplicate-helper
  finding? (Yes → high-confidence merge candidate.)
