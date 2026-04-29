---
name: code-survey
description: Multi-lens parallel-agent survey of a codebase that produces a prioritized, risk-classified, verification-policy-aware refactor recommendation list. Use this skill whenever the user wants a code-review pass, refactor scout, monolith scan, dedupe scan, code audit, "find long files," "are these files monolithic," "review this codebase for refactor opportunities," or asks for "the kind of analysis we ran on <prior project>." Also triggers on /code-survey, /code-review, /refactor-scout, "refactor sprint," "before-refactor analysis," or any request that asks for a coordinated cross-cutting code-quality scan rather than a single-file edit. Do not use for security audits (route to /security-review), performance profiling, or single-function inspection.
---

# code-survey

A skill for surveying a codebase across multiple orthogonal lenses,
synthesizing findings into a prioritized recommendation list, and
optionally handing the work off to /threads for execution.

The shape: **cheap-model parallel fan-out** across lenses, **main-model
synthesis** that does cross-cutting reasoning the fan-out can't.

## When to use

- "Review this codebase for refactor opportunities."
- "Are any of these files monolithic?"
- "Find duplicate helpers / dedupe scan."
- "I want to do a refactor sprint — survey first."
- "Run the kind of multi-pass analysis we did before."
- A long file lands on the user's desk and they want a sanity check.

## When NOT to use

- Single-file edits or one-off bug fixes — read the file directly.
- Security audit — route to /security-review.
- Performance profiling — use a profiler.
- "Just look at this one function" — that's a Read, not a survey.

## Operations — dispatch table

When the user's request matches one of these, follow the named
section in `references/workflows.md`:

| User's ask | Operation |
|---|---|
| "Set up code-survey for this project" / "bootstrap config" / first time | **Bootstrap** |
| "Run a code survey" / "scan the codebase" / "find monoliths" / no flags | **Scan (default kit)** |
| "Run a thorough survey" / "+ naming and constants" | **Scan (--thorough)** |
| "Run a full audit" / "everything" | **Scan (--full)** |
| "Just the dedupe pass" / "monolith pass only" | **Scan (--pass=<lens>)** |
| "Survey only the diff against main" / "scope to changes" | **Scan (--scope=diff:main)** |
| "Synthesize the results" / "wrap up the scan" / "produce the report" | **Synthesize** |
| "Turn this into a thread" / "spawn a thread for the refactor sprint" | **Propose thread** |
| "Compare the last two runs" / "A/B the surveys" / "diff the synthesis" | **Diff** |

If the user's ask doesn't match cleanly, ask which operation they
want before spawning agents. Don't invent operations.

**Auto-bootstrap.** If **Scan** is invoked on a project with no
config (no `.code_survey/config.json`), the skill auto-runs
**Bootstrap** first, surfaces the proposed config for user review,
then proceeds. The user does not have to invoke Bootstrap manually.

## Project-root anchor

Code-survey runs from project root, never a subdirectory. A scan
rooted in `gps_receiver/` would miss `scenario_engine/`; a scan
rooted in `~/Work1/Claude/` would scan every project at once. Both
are wrong-tree errors that the skill detects up front.

**Detection order** (first match wins):

1. **Primary — `.research/` peer at cwd.** If `<cwd>/.research/`
   exists, this is project root.
2. **Reinforcement — `.code_survey/` peer at cwd.** If
   `<cwd>/.code_survey/` (with at least `.gitkeep`) exists from a
   prior bootstrap, this is project root. Use this when the project
   has no `/research` history yet.
3. **Reinforcement — CLAUDE.md at cwd describing this project.**
   If `<cwd>/CLAUDE.md` exists AND names the project + subdirs (the
   "umbrella" CLAUDE.md pattern), accept as project root. Use when
   neither `.research/` nor `.code_survey/` exist (very-first
   bootstrap on a brand-new project).

If none match, **stop and ask** — do not silently survey the wrong
tree. Phrasing: "I think project root is `<best-guess>` because
`<reason>`. Proceed or override with `--root=<path>`?"

A valid invocation example: from
`/media/doogie/Work1/Claude/work/gps_design/` (which has
`.research/` and `gps_receiver/threads/`), the skill picks up that
cwd as root. From `gps_receiver/` it refuses; from
`/media/doogie/Work1/Claude/` it refuses.

## The 8 lenses

Each lens is one orthogonal way of looking at the codebase. The
default kit (1–3) is what runs without flags. Lenses 4–5 are the
"thorough" tier; 6–8 are the "full" tier.

| # | Lens | Tier | Default model | Purpose (one line) |
|---|---|---|---|---|
| 1 | file-level monolith | default | haiku | Find files that do multiple unrelated jobs |
| 2 | function-level long-method | default | sonnet | Find methods >60 lines or with deep nesting |
| 3 | duplicate-helper | default | haiku | Find duplicated code across files |
| 4 | naming-drift | thorough | sonnet | Symbols that grep finds under multiple names |
| 5 | constants-drift | thorough | haiku | Physical/numerical constants defined ≥2× |
| 6 | import-graph | full | haiku | Circular deps, ambiguous ownership |
| 7 | comment-debt | full | haiku | Stale TODOs, "remove once X" with dead refs |
| 8 | api-surface | full | sonnet | Accidentally-public / accidentally-private |

**Per-lens detail** — including agent prompt template, search
strategies, anti-patterns, and verdict vocabulary — lives in
`references/lenses.md`. Read it before spawning a lens for the
first time in a session.

**Default model is Haiku.** Sonnet is opt-in for reasoning-heavy
lenses (per `references/config.md`'s `lens_models` mapping). **Never
auto-spawn Opus for sub-agents** — cost/overkill; main-session
synthesis is where Opus-grade reasoning belongs.

## Encoded anti-patterns (apply to every lens)

These bias every lens away from over-fragmenting and from false
positives. They're the hard-won lessons from the ad-hoc survey
that motivated this skill:

1. **Length is not monolith.** A long file can be cohesive. Bias
   toward KEEP unless multi-concern is *demonstrated*, not
   merely "the file is long."
2. **Constructors are usually KEEP.** They legitimately accumulate
   parameter wiring; extracting `_init_X()`, `_init_Y()` typically
   relocates noise without reducing it.
3. **State machines are usually KEEP.** Splitting per-state methods
   obscures the transitions, which are the algorithm.
4. **ICD/spec/protocol procedures must not fragment.** If the
   project config names a file or function as ICD-traceable, it
   stays whole. (Project-specific; sourced from
   `.claude/code-survey-config.json`.)
5. **2-file duplicates: hoist to existing canonical owner.** Don't
   propose a new module for a 2-occurrence dedupe. New module only
   at 3+ files.
6. **Physics floor.** Claims framed as "precision bug" or
   "numerical issue" must pass the project-config physics-floor
   check before being flagged P1. Sub-mm numerical-convergence
   detail does NOT qualify as a precision concern under any
   "ns matters"-style rule.
7. **Naming drift hides duplicates.** When a symbol search finds
   "single canonical owner," try grepping by the *math* (constants
   used, parameter shape) too — duplicates can hide under different
   names.
8. **Same code, different role ≠ duplicate.** If two
   implementations exist because they serve different roles
   (e.g., one is firmware-bound and can't import the other), that's
   intentional duplication, not a dedup target. Document, don't
   merge.

## Artifact layout

Code-survey writes its config and every scan run into a single
project-root tree at `.code_survey/`. The tree is **git-tracked**
(small text artifacts; durable across machines and reviewable in
PRs); only `temp/` subdirs are gitignored.

```
.code_survey/
  config.json                      # project config (was .claude/code-survey-config.json)
  .gitkeep                         # project-root anchor; created by Bootstrap
  index.md                         # session log, newest-first; auto-updated each Synthesize
  session-<YYYYMMDD-HHMMSS>/
    config.snapshot.json           # frozen copy of config.json at run time
    scope.json                     # files surveyed + why each was included
    pass-1-file-monolith/
      agent-1-<group>.md           # raw per-agent report (Haiku output, unfiltered)
      ...
    pass-2-long-method/
    pass-3-dedupe/
    raw-recommendations.md         # aggregated per-lens hits, PRE-curation
    thread-tree-snapshot.json      # inventory of all threads at run time (active + closed)
    thread-proposals.md            # candidate threads, dedup'd vs. tree (3 buckets)
    synthesis.md                   # curated, risk-classified final artifact
    synthesis.json                 # machine-readable parallel
    temp/                          # gitignored; per-run scratch
  diff-<sessionA>-vs-<sessionB>.md # A/B comparison artifact (Diff op, optional)
```

### Why three recommendation artifacts, not one

- **`raw-recommendations.md`** — what the lens agents found before
  any filter. Necessary so a future agent (or future you) can
  reconstruct the original signal. Without it, the curated synthesis
  is the only record and you cannot tell whether an item was never
  flagged or was flagged and dropped.
- **`synthesis.md`** — what survived the anti-pattern filter,
  physics-floor filter, KEEP-bias, risk classifier, and
  cross-pass reinforcement check. The deliverable.
- **`thread-proposals.md`** — what's eligible for **new** thread
  spawn after dedup'ing against the project's existing thread tree.
  Three buckets: NEW, SUBSUMED, TENSION. See `references/synthesis.md`.

Past surveys stay on disk under their session dirs. The Diff
operation compares any two sessions on demand.

## Composability with other skills

- `/research` — for deep investigation if a finding looks like a
  bug, not a refactor.
- `/threads` — sprint execution (via `/code-survey propose-thread`
  handoff). The skill writes a fully-populated thread proposal;
  the user invokes /threads themselves.
- `/ultrareview` — third-party check on the recommendation list
  before execution.

## Sanity checks before acting

1. **Are we at project root?** Apply the detection order in
   "Project-root anchor" above. If detection fails, stop and ask
   before doing anything else.
2. **Does the project have a config?** If `.code_survey/config.json`
   is missing, **auto-run Bootstrap** (surface proposed config for
   review, wait for user accept, then proceed). Generic-mode
   fallback exists but should be the explicit user choice, not
   the default.
3. **Is the scope sensible?** Before fanning out agents over 200
   files, confirm the scope with the user. Surveys at >50 files
   want `--scope=path:` or `--scope=diff:` narrowing.
4. **Is there a recent run?** If a survey already exists from
   today on the same scope, ask whether to re-run, re-synthesize,
   or **Diff** against it.

## Reference files

Read on demand:

- `references/lenses.md` — the 8 lens specs (purpose, prompt
  template, search strategies, anti-patterns).
- `references/config.md` — config schema, bootstrap procedure,
  defaults, examples.
- `references/synthesis.md` — synthesis template, cross-pass
  reinforcement check, physics-floor filter, risk classifier,
  thread-worthy check.
- `references/workflows.md` — operation procedures (bootstrap,
  scan, synthesize, propose-thread).
- `references/lessons-learned.md` — six cautionary cases the
  synthesis step should respect.

## Templates

`assets/templates/` holds skeletons the workflows copy:

- `code-survey-config.json` — project config seed (written to
  `<root>/.code_survey/config.json` at bootstrap).
- `synthesis.md` — curated wrap-up MD scaffold.
- `synthesis.json` — parallel JSON scaffold.
- `thread-proposals.md` — three-bucket dedupe scaffold
  (NEW / SUBSUMED / TENSION).
- `agent-prompt-<lens>.md` × 8 — per-lens agent prompt templates
  (substitute project context at scan time).

## Scripts

`scripts/` holds helpers the workflows shell out to:

- `inventory_threads.py <root>` — globs every
  `**/threads/*/*/thread.json` under `<root>` and emits a JSON
  snapshot (id, status, plan files, files touched). Consumed by
  Synthesize for the three-bucket dedupe pass.
