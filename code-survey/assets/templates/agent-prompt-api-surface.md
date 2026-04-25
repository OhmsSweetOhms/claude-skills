# Agent prompt: api-surface lens

You are doing an API-surface analysis. Your job is to find symbols
whose visibility is inconsistent with their actual use:
**accidentally private** (underscore prefix but imported externally)
or **accidentally public** (no prefix but never imported by
anyone).

**Project context:**
{{PROJECT_CONTEXT}}

**Files to analyze:**
{{FILES}}

**Strategy:**

For each `def foo` / `class Foo` / module-level constant in scope:

1. **Check the visibility convention.** Does it have an underscore
   prefix (`_foo`)? Does it appear in `__all__`?
2. **Find external imports.** Grep for `import foo`, `from X import
   foo`, `X.foo` across the project. Count call-sites that are NOT
   in the same module.
3. **Cross-reference with convention.** Underscore-prefix +
   external imports → mismatch. No prefix + zero external use →
   mismatch.

**Categorize each finding:**

- `ACCIDENTALLY-PRIVATE` — underscore-prefixed symbol that's imported
  externally. Either rename (drop the underscore) or refactor to
  not need the cross-module import.
- `ACCIDENTALLY-PUBLIC` — public-named symbol with zero external
  imports. Either prefix with underscore (signal private) or
  remove if truly dead.
- `OK` — visibility matches actual use. (Don't report unless asked.)

**Encoded biases:**
- **Test files importing private helpers is fine.** That's an
  established convention. Don't flag `from X import _helper` if
  the importer is a test file — flag only if a non-test module
  reaches into a sibling's private internals.
- **Module-private but package-public is OK.** A symbol prefixed
  `_` in `mod.py` that's re-exported via `package/__init__.py`
  is using `__init__.py` as the public API surface. Not a mismatch.
- **API stability.** Don't recommend renaming a public symbol that
  external (out-of-repo) code may depend on, even if internal usage
  is zero. Surface with `UNCLEAR` instead.

**Project-specific anti-patterns:**
{{EXTRA_ANTI_PATTERNS}}

**Hard constraints:**
- Don't propose renaming symbols that appear in any
  documentation, README, or public-API spec.
- Don't propose deleting an "unused" symbol if it's a deliberate
  public API even with zero current callers (the package may
  publish a stable API for downstream).

**Report format (under 400 words):**

### Accidentally private

| Symbol | Defined in | Imported externally by | Recommendation |
|---|---|---|---|
| `_foo` | `<file:line>` | `<file>` | rename to `foo` OR refactor to avoid import |

### Accidentally public

| Symbol | Defined in | External use count | Recommendation |
|---|---|---|---|
| `bar` | `<file:line>` | 0 | prefix as `_bar` OR remove if dead |

### Unclear

| Symbol | Defined in | Concern |
|---|---|---|
| `baz` | `<file:line>` | <one-line concern needing user review> |

Lead with `ACCIDENTALLY-PRIVATE` — that's the higher-impact category
because cross-module reaches into private internals indicate
architectural drift.
