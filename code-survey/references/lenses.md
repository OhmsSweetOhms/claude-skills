# Lenses — detailed reference

Each lens is one orthogonal way of looking at the codebase. They
are **additive**: each finds things the others can't. The synthesis
step (see `synthesis.md`) cross-checks findings between lenses;
findings that show up in 2+ lenses are high-confidence.

The agent prompt for each lens lives in
`assets/templates/agent-prompt-<lens>.md`. The substance below
explains *why* each lens is shaped the way it is, so a future
maintainer can adapt the prompts without losing the intent.

---

## 1. file-level monolith (default kit)

**Purpose.** Find files that do multiple unrelated jobs. Distinguish
genuinely monolithic files (multiple concerns crammed in) from
long-but-cohesive files (one concern, expressed at length).

**Bias.** Default-KEEP. The most common false positive in this lens
is "this file is long, therefore split it." Length is not monolith.
A 900-line orchestrator that composes 11 blocks is doing one
coherent job; splitting it fragments the composition.

**Search strategies.**
- `find` for `.py` / `.ts` / etc. by line count (descending).
- For each candidate over the size threshold, ask: does it have
  multiple top-level classes? Multiple unrelated import clusters?
  Multiple "section" comments?
- Group files into thematic batches (orchestrators, blocks,
  utilities, tests) before fanning out — agents are more accurate
  on a coherent batch than on a random sample.

**Anti-patterns to encode in the prompt.**
- "Length is not monolith. KEEP unless multi-concern is demonstrated."
- "Constructors are usually KEEP."
- "State machines are usually KEEP."
- "Project boundaries are sacred — do not propose splitting files
  the config marks as one-unit-per-file."

**Verdict vocabulary.**
- `SPLIT` — multi-concern; concrete extraction targets named.
- `KEEP` — cohesive; length is not by itself a problem.
- `MINOR` — small extract worth doing (utility module, helper file)
  but the bulk of the file stays.
- `DEFERRED` — known to be in flux (e.g., a major redesign is
  pending); revisit later.

**Default model.** Haiku. The judgment is mostly pattern-matching
("does this file have one concern or many?"), which Haiku does well.

---

## 2. function-level long-method (default kit)

**Purpose.** Within files (whether KEEP or SPLIT), find individual
methods or functions that are themselves long or deeply nested. Not
about file boundaries — about whether a single method has internal
structure that wants to be private helpers.

**Bias.** REFACTOR-IN-PLACE (extract private methods on the same
class) over file-level extraction. The granularity is *within* a
file. KEEP if the method is one coherent algorithm.

**Search strategies.**
- AST walk OR grep for `def ` / `function ` / `fn ` and count lines.
- Threshold: methods >60 lines OR with nesting depth >3.
- For each candidate, look for "section header" comments
  (e.g. `# --- step 1 ---`) — strong signal that the author
  already mentally factored.
- Look for local-variable lifetimes: if lines 40-80 only touch
  `tmp_a/b/c`, that region is an extraction candidate.

**Anti-patterns to encode in the prompt.**
- "ICD-traceable algorithms must NOT fragment. A method that
  walks IS-GPS-200 / RFC-X / spec-Y steps in order is one thing,
  not several."
- "State machines are KEEP. Splitting per-state methods obscures
  transitions."
- "Constructors are KEEP. Pure parameter wiring is legitimate even
  at 100+ lines."
- "DSP / hot inner loops are KEEP. Don't fragment a tight loop."

**Verdict vocabulary.**
- `REFACTOR-IN-PLACE` — extract private methods within the same
  class/module. Name the extractions.
- `GUARD-CLAUSE` — flatten nesting with early returns; no extraction.
- `KEEP` — single coherent algorithm; length is justified.

**Default model.** Sonnet. Distinguishing "5 phases mashed together"
from "1 algorithm in 5 steps" is reasoning-heavy.

---

## 3. duplicate-helper (default kit)

**Purpose.** Find duplicated code across files. Categorize by
similarity. Recommend either hoisting to a canonical owner (2-file
duplicates) or extracting a shared module (3+ files).

**Bias.** Don't propose new modules for 2-occurrence duplications.
Hoist the duplicate to the existing canonical owner (whichever file
is more authoritative — usually the runtime path over a legacy
test-only path).

**Search strategies — and this is where the lens earns its keep.**
- **By symbol name:** grep for the function name across the repo.
- **By math signature:** grep for the constants/operations used
  (e.g. `OMEGA_E *`, `np.linalg.norm`, parity-check XOR masks).
- **By parameter shape:** functions with the same `(arg1, arg2, ...)`
  pattern doing the same job.
- **By docstring summary:** sometimes the docstrings agree even
  when the names don't.

**The naming-drift trap.** Two functions can do the same math under
different names (e.g. `_correct_<X>` in one file and
`_rotate_<X>_helper` in another, both applying the same rotation by
the same angle to the same input). A symbol-name grep will report
"single canonical owner" and miss the duplicate. Always cross-check
with a math-signature grep when the user's project has any history
of naming convention drift.

**Anti-patterns to encode in the prompt.**
- "2-file duplicate → hoist to canonical owner; don't create a
  new shared module."
- "Same code, different role ≠ duplicate. If implementation A is
  firmware-bound and can't import implementation B, the
  duplication may be intentional. Flag for documentation, not
  deduplication."
- "Naming drift hides duplicates from name-grep. Always
  cross-check by math/parameter/docstring signature."

**Verdict vocabulary.**
- `EXACT` — byte-identical or near-identical body in 2+ files.
- `NEAR` — same intent, minor variation (different constant,
  different parameter name).
- `INLINE-EQUIVALENT` — one file has a named helper, another
  inlines the same logic.
- `INTENTIONAL` — duplication is deliberate (firmware boundary,
  cyclic-dep avoidance); recommend documentation.

**Default model.** Haiku. Pattern-matching on code shape.

---

## 4. naming-drift (thorough kit)

**Purpose.** Find symbols that grep finds under multiple names.
Either the codebase has redundant implementations (graduate to
duplicate-helper finding) OR it has consistent semantics under
inconsistent names (rename to one canonical form).

**Bias.** Cluster first; recommend rename only when the cluster is
dense.

**Search strategies.**
- For each function/class in scope, generate a "math signature":
  the set of constants used, the set of operations, the parameter
  count, the return shape.
- Cluster by signature. Clusters of size 2+ are candidates.
- Rank by clarity gain — renaming `compute_X` and `do_X` and
  `process_X` to one consistent verb has bigger impact than
  renaming two helpers used in one place each.

**Anti-pattern.** Don't rename across module boundaries that
intentionally insulate (e.g. firmware vs simulation). Confirm the
cluster spans a single layer before recommending consolidation.

**Verdict vocabulary.**
- `CONSOLIDATE` — same math under multiple names; pick a canonical
  name and rename others.
- `RENAME` — single occurrence under a misleading name; rename in
  place.
- `KEEP` — the apparent overlap is structural (different layer,
  different role).

**Default model.** Sonnet. Math-signature reasoning is hard for Haiku.

---

## 5. constants-drift (thorough kit)

**Purpose.** Physical or numerical constants defined in multiple
places. Detect both numerical drift (different values for the
"same" constant — a real bug) and naming drift (same value, multiple
aliases — a coordination tax).

**Bias.** Numerical drift is always P1. Naming drift is P2.
Inline-literal violations of a project's "no hardcoded constants"
rule are P3.

**Search strategies.**
- Grep for the well-known values of project-relevant constants
  (e.g. `299792458`, `7.2921151467e-5`, `6378137`).
- Grep for the well-known *names* (`C_LIGHT`, `OMEGA_E`,
  `WGS84_A`).
- Grep for inline literals that match physical magnitudes (numbers
  with engineering-notation patterns near domain keywords like
  `frequency`, `velocity`, `radius`).

**Anti-pattern.** Don't flag `0`, `1`, `2 * pi` etc. — only domain
constants. The project config provides the list of relevant
constants and their canonical values.

**Verdict vocabulary.**
- `NUMERICAL-DRIFT` — different values for the same constant. P1.
- `NAMING-DRIFT` — same value, different names. P2.
- `INLINE-LITERAL` — value hardcoded inline against project rule. P3.
- `CONSISTENT` — defined once, used everywhere.

**Default model.** Haiku. Pure pattern matching.

---

## 6. import-graph (full kit)

**Purpose.** Find circular dependencies, ambiguous ownership, and
accidentally-tight coupling.

**Search strategies.**
- Build the import graph (which file imports which) for the scope.
- Detect cycles (`networkx.simple_cycles` if available, or
  iterative DFS).
- For each module, count fan-in (how many import it) and fan-out
  (how many it imports). High fan-out + high fan-in = god module.
- Look for files that import siblings that import them back.

**Anti-pattern.** Some cycles are acceptable (typing-only via
`if TYPE_CHECKING:`). Don't flag those.

**Verdict vocabulary.**
- `CYCLE` — actual import cycle, not type-only.
- `GOD-MODULE` — high fan-in AND high fan-out.
- `ORPHAN` — defined but never imported by anyone in scope.
- `OK`.

**Default model.** Haiku.

---

## 7. comment-debt (full kit)

**Purpose.** Stale TODOs, FIXMEs, "removed once X" comments
referring to closed issues or removed code paths.

**Search strategies.**
- Grep for `TODO`, `FIXME`, `XXX`, `HACK`, `removed once`,
  `see issue #`, `see PR #`.
- For each match, check if the referenced issue / file / function
  still exists.

**Anti-pattern.** Don't flag TODOs that include a clear "until <X>"
condition that hasn't yet been met. Those are deferred-but-tracked,
not stale.

**Verdict vocabulary.**
- `STALE` — the referenced issue is closed / the referenced code
  is gone / the condition is met.
- `OPEN` — still relevant.
- `UNCLEAR` — needs human review.

**Default model.** Haiku.

---

## 8. api-surface (full kit)

**Purpose.** Find symbols whose visibility is inconsistent with
their use. Underscore-prefixed (private) symbols imported externally;
public symbols never imported by anyone.

**Search strategies.**
- For each `def foo` / `class Foo`: grep for `import foo`,
  `from X import foo`, `X.foo` across scope.
- Underscore-prefix + external imports → accidentally private.
- No prefix + zero external imports → accidentally public.

**Anti-pattern.** Test files that import private helpers from the
module under test are *fine* — that's an established convention.
Don't flag.

**Verdict vocabulary.**
- `ACCIDENTALLY-PRIVATE` — underscore-prefix but imported externally.
- `ACCIDENTALLY-PUBLIC` — no prefix, no external use.
- `OK`.

**Default model.** Sonnet. Distinguishing "test imports private"
(OK) from "production imports private" (problem) requires reasoning
about call-site context.

---

## How the lenses compose

The synthesis step (`synthesis.md`) takes findings from multiple
lenses and looks for **cross-pass reinforcement**. A finding that
shows up in two lenses is high-confidence; one that shows up in
only one is medium-confidence.

Examples:
- Lens 3 (duplicate-helper) flags two parity functions in different
  files. Lens 4 (naming-drift) flags the *same* two functions under
  different names. → High confidence: real duplicate, naming hides
  it. Hoist to canonical home.
- Lens 1 (file-level monolith) flags a 700-line file as SPLIT. Lens
  2 (long-method) flags one method in that file as REFACTOR-IN-PLACE.
  → The split-and-refactor work bundles into one coherent change.
- Lens 5 (constants-drift) flags `C_LIGHT` defined in 3 places. Lens
  6 (import-graph) shows those 3 modules don't import each other.
  → They're per-package definitions for import independence.
  Verdict: keep, but document.

The synthesis step is where the value of running multiple lenses
shows up. Read `synthesis.md` for the cross-pass procedure.
