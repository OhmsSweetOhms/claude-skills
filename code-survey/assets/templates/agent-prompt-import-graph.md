# Agent prompt: import-graph lens

You are doing an import-graph analysis on a batch of source files.
Your job is to find circular dependencies, ambiguous ownership,
and accidentally-tight coupling.

**Project context:**
{{PROJECT_CONTEXT}}

**Files to analyze:**
{{FILES}}

**Strategy:**

1. **Build the import graph.** For each file, extract its imports.
   Build a directed edge `A → B` whenever file A imports from file B.
2. **Detect cycles.** A cycle is a path A → B → ... → A. Both
   typing-only cycles (via `if TYPE_CHECKING:`) and runtime cycles.
3. **Compute fan-in / fan-out per module.** Fan-in = how many files
   import this one. Fan-out = how many this one imports.
4. **Look for orphans.** Files that define symbols but aren't
   imported by anyone in the project's runtime path. (Tests
   importing them is fine; only-tests is a yellow flag.)

**Categorize findings:**

- `CYCLE` — actual import cycle, runtime (not typing-only).
- `TYPING-CYCLE` — cycle exists but only via `if TYPE_CHECKING:`.
  Usually OK; flag if it's complicated.
- `GOD-MODULE` — high fan-in AND high fan-out (e.g. fan-in ≥ 5 AND
  fan-out ≥ 5). Likely doing too many jobs.
- `ORPHAN` — defined but never imported by anyone in scope.
- `ACCIDENTAL-COUPLING` — two modules tightly coupled by many
  shared symbols, where one of them probably shouldn't reach into
  the other (e.g., `tests/` reaching into private internals of
  another module's sibling).

**Encoded biases:**
- **Some cycles are acceptable.** Typing-only cycles for
  forward-references are fine. Don't flag those.
- **God-modules in framework code may be intentional.** If a file is
  named `core.py` or `__init__.py` and aggregates many sub-modules,
  high fan-in is structural. Flag only if fan-out is also high.
- **Tests importing private helpers is fine.** That's an established
  convention.

**Project-specific anti-patterns:**
{{EXTRA_ANTI_PATTERNS}}

**Hard constraints:**
- Don't recommend breaking cycles by adding more abstraction layers
  unless the abstraction has a clear independent purpose.
- Don't flag `__init__.py` re-exports as god-module behavior.

**Report format (under 500 words):**

### Cycles

For each runtime cycle:
- Path: `A → B → ... → A`
- Likely fix: <inversion / extract-shared / accept and document>
- One-line rationale.

### God-modules

For each:
- File: `<path>`
- Fan-in: N, Fan-out: M, Symbols: <top exports>
- Verdict: <legitimate aggregator | refactor candidate>

### Orphans

For each:
- File: `<path>`
- Last imported in: <commit / never>
- Verdict: <delete | promote to test fixture | keep with comment>

### Accidental coupling

For each pair:
- Modules: `<A>` ↔ `<B>`
- Shared symbols: <list>
- One-line note on whether the coupling is structural or accidental.
