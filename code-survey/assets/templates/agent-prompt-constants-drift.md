# Agent prompt: constants-drift lens

You are doing a duplicated-constant detection pass. Your job is to
find physical or numerical constants defined in multiple places —
detecting both numerical drift (different values for the "same"
constant, a real bug) and naming drift (same value, multiple
aliases, a coordination tax).

**Project context:**
{{PROJECT_CONTEXT}}

**Files to analyze (and grep across the wider repo):**
{{FILES}}

**Project's relevant constants (search by both name and value):**
{{PROJECT_CONSTANTS_LIST}}

**Search strategy:**

1. **Grep for known values.** For each project-relevant constant,
   grep for its numerical value across the repo. Catalog every
   match.
2. **Grep for known names.** Grep for the standard symbol names
   (e.g. `C_LIGHT`, `OMEGA_E`, `WGS84_A`). Catalog every definition.
3. **Grep for inline literals.** Find numeric literals that match
   physical magnitudes near domain keywords (e.g. `1575.42e6` near
   "frequency", `6378137` near "radius"). These violate any "no
   hardcoded constants" rule.

**Categorize each finding:**

- `NUMERICAL-DRIFT` — different values for the same constant. **P1.**
  This is a real bug; the "same" constant has different effective
  values in different parts of the codebase.
- `NAMING-DRIFT` — same value, different names (e.g. `C` and
  `C_MPS` and `SPEED_OF_LIGHT`, all = 299792458). **P2.**
- `INLINE-LITERAL` — value hardcoded inline against project rule. **P3.**
- `CONSISTENT` — defined once, used everywhere. (Don't report.)

**Encoded biases:**
- **Numerical drift is always P1**, regardless of the count of
  files involved.
- **Don't flag `0`, `1`, `2 * pi`, `np.pi` etc.** — only domain
  constants the project considers physical.
- **Per-package definitions can be intentional** for import
  independence. If three packages each define `C_LIGHT` with the
  same value and the import graph shows they don't import each
  other, the duplication is reasonable. Flag for documentation,
  not consolidation.

**Project-specific anti-patterns:**
{{EXTRA_ANTI_PATTERNS}}

**Hard constraints:**
- Drift in WGS-84-style geodetic constants (eccentricity squared,
  flattening) is critical because positions are sensitive at the
  meter level. Always flag.
- Drift in time/frequency standards is critical because phase and
  Doppler chains compound the error.

**Report format (under 500 words):**

Lead with any **NUMERICAL-DRIFT** findings (these are P1 bugs).

Then a table:

| Constant | Value(s) | Defined in | Used in (cross-file) | Verdict | Drift? |
|---|---|---|---|---|---|

One row per constant. Include all definitions, not just the first.
Annotate any per-package architectural rationale for legitimate
duplication.

End with a recommendation list:
- For `NUMERICAL-DRIFT`: which file's value is canonical, and how
  to converge the others.
- For `NAMING-DRIFT`: which symbol is canonical, and which to
  alias or remove.
- For `INLINE-LITERAL`: the constant to import, and which inline
  uses to replace.
