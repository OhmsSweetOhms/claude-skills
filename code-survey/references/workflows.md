# Operation workflows

Step-by-step procedures for each operation. Find the section that
matches the user's ask in `SKILL.md`'s dispatch table, then follow
the steps here.

---

## Project-root detection (precedes every operation)

Every operation runs from project root. Before doing anything else,
detect the root using the order below (first match wins). If none
matches, **stop and ask** — never silently survey a wrong tree.

1. **`.research/` peer at cwd.** If `<cwd>/.research/` exists, root
   is cwd. Strongest signal because `/research` and code-survey
   target the same project scope.
2. **`.code_survey/` peer at cwd** (with at least `.gitkeep`). Use
   when the project has no `/research` history yet but has been
   bootstrapped before.
3. **CLAUDE.md at cwd describing this project.** Read the top of
   `<cwd>/CLAUDE.md`; if it names a project + lists subdirs (the
   "umbrella CLAUDE.md" pattern), accept cwd as root. Use only
   when neither (1) nor (2) applies — i.e., very-first bootstrap.

**Fail-loud rule.** If none of the above matches:
> "I don't see `.research/`, `.code_survey/`, or a project-level
> CLAUDE.md at `<cwd>`. I think project root might be
> `<best-guess-from-git-toplevel-or-parent>`. Proceed there, or
> override with `--root=<path>`?"

Do not guess. Wrong-tree errors waste agent budget and produce
confusing artifacts.

---

## Bootstrap

**Trigger:** user explicitly wants to set up code-survey for a
project, or **Scan** is invoked on a project with no
`.code_survey/config.json` (auto-bootstrap).

**Pre-flight:**
- Run project-root detection (above). Stop on failure.
- Confirm `<root>/.code_survey/config.json` doesn't already exist
  (use `--force` to overwrite).
- Detect legacy config at `<root>/.claude/code-survey-config.json`.
  If found, read it and treat its values as the seed for the new
  config (migration path), then propose deletion of the legacy file
  at the end. Do not silently delete.
- Read `CLAUDE.md` at project root and any nested ones.

**Steps:**

1. Read all CLAUDE.md files in scope.
2. Extract candidate config values per `references/config.md`:
   - "Hard Requirements" / "Constraints" → boundaries, physics_floor.
   - "Conventions" / "Architecture" → keep_rules.
   - "Running" / "Tests" → verification_command.
   - "Block IDs" / canonical lists → boundaries entries.
   - Phrases about precision/tolerance → physics_floor.
3. Create the artifact tree:
   ```
   mkdir -p <root>/.code_survey
   touch <root>/.code_survey/.gitkeep
   ```
   The `.gitkeep` is the project-root anchor for future runs (so
   detection step 2 succeeds even before the first scan).
4. Copy `assets/templates/code-survey-config.json` to
   `<root>/.code_survey/config.json`. Fill placeholders with the
   extracted values (or migrated legacy values).
5. **Show the user the proposed config** in conversation, annotated
   with where each non-default value came from. Format:
   ```
   "boundaries[0]" — from gps_receiver/CLAUDE.md § Module Layout:
     "Each file mirrors one FPGA/PS block."
   "physics_floor" — from CLAUDE.md § Hard Requirements rule 2:
     "Nano-seconds matter. 1 ns ≈ 30 cm of pseudorange."
   ```
6. Wait for the user to review, edit, and accept.
7. Write the final config to disk at `<root>/.code_survey/config.json`.
8. If the legacy `<root>/.claude/code-survey-config.json` existed,
   surface a one-line removal proposal: "Legacy config at
   `.claude/code-survey-config.json` is now superseded by
   `.code_survey/config.json`. Delete the legacy file?"
9. Tell the user that `.code_survey/` is project state and should
   be committed (the directory is git-tracked; only `temp/`
   subdirs are gitignored — the bootstrap can also append
   `.code_survey/*/temp/` to the project's `.gitignore` if it
   isn't already covered).

**Verification:**
- File parses as JSON.
- `version: 1`, `project_name` is non-empty.
- All globs in `boundaries[]` and `keep_rules[]` match at least one
  file in the project (warn if not).
- `.code_survey/.gitkeep` exists.

---

## Scan

**Trigger:** user wants to run a code survey.

**Pre-flight:**
- Run project-root detection. Stop on failure.
- Confirm `<root>/.code_survey/config.json` exists. If not,
  **auto-run Bootstrap** (per the dispatch table). Proceed in
  generic mode only if the user explicitly opts out of bootstrap.
- Determine the lens kit:
  - default (no flags): lenses 1, 2, 3.
  - `--thorough`: + lenses 4, 5.
  - `--full`: + lenses 6, 7, 8.
  - `--pass=<lens>`: just that lens.
- Determine scope:
  - default: all source files in the project.
  - `--scope=diff:main`: `git diff --name-only main` filtered to
    source extensions.
  - `--scope=path:X`: files under path X.
- If scope > 50 files, ask the user to confirm before fanning out.

**Steps:**

1. Create the session directory:
   `<root>/.code_survey/session-<YYYYMMDD-HHMMSS>/`.
   Use seconds (HHMMSS) so two scans on the same minute don't
   collide.
2. Snapshot the config and scope:
   ```
   config.snapshot.json   # copy of .code_survey/config.json
   scope.json             # which files are in scope, and why
   ```
3. **Inventory the thread tree** (used later by Synthesize). Run
   the helper:
   ```
   scripts/inventory_threads.py <root> > \
     <session-dir>/thread-tree-snapshot.json
   ```
   The snapshot includes every `**/threads/*/*/thread.json` under
   `<root>` — both **active** and **closed** threads, tagged by
   status. Including closed threads matters: a recommendation that
   a closed thread already landed should be flagged "already done"
   rather than "new opportunity." If the helper script doesn't
   exist yet, fall back to glob+jq inline (see
   `references/synthesis.md` § Step 4').
4. For each lens in the kit:
   a. Create the lens subdir: `pass-N-<lens>/`.
   b. Group files into thematic batches (3-6 files per batch):
      orchestrators / blocks / utilities / tests / etc. Use the
      project's directory structure as a hint.
   c. For each batch, prepare an agent prompt by copying
      `assets/templates/agent-prompt-<lens>.md` and substituting:
      - `{{PROJECT_CONTEXT}}` from the config (boundaries,
        physics_floor, project-specific anti-patterns).
      - `{{FILES}}` with the batch's file paths.
      - `{{ANTI_PATTERNS}}` with the encoded anti-patterns from
        SKILL.md plus any from `config.lens_overrides[lens]`.
   d. Spawn the agents in parallel. Default model is Haiku unless
      `config.lens_models[lens]` says otherwise (or `--model=`
      overrides).
   e. As each agent returns, write its report to
      `pass-N-<lens>/agent-K-<batch-name>.md`.
5. After all lenses finish, tell the user the scan is complete and
   surface the per-pass file list. Recommend they run
   `/code-survey synthesize` next.

**Lens parallelism note.** Lenses 1, 2, 3 (the default kit) are
independent — run them all in parallel. Lenses 6, 7, 8 are also
independent. Don't sequence them unless a lens depends on another's
output (none currently do).

**Verification:**
- Session dir exists with expected lens subdirs.
- `thread-tree-snapshot.json` exists and parses.
- Each agent report parses as Markdown and has at least one finding
  or an explicit "no findings" verdict.

---

## Synthesize

**Trigger:** user wants to wrap up a scan, or run synthesis again
after hold/skip decisions.

**Pre-flight:**
- Run project-root detection. Stop on failure.
- Identify the scan session to synthesize. Default: most recent.
  `--session=<dir>` overrides.
- Read all per-agent reports under `pass-N-<lens>/`.
- Read `config.snapshot.json`.
- Read `thread-tree-snapshot.json` (for the thread-tree dedupe pass).
- If `decisions.json` exists in the session dir, read it
  (re-synthesis case).

**Steps:**

Follow the procedure in `references/synthesis.md` end-to-end:

1. Gather all findings.
2. **Write `raw-recommendations.md`** — every finding from every
   lens, pre-curation. This is the implicit-save artifact.
3. Cross-pass reinforcement check.
4. Physics-floor filter.
5. **Thread-tree dedupe** — classify each surviving finding into
   one of three buckets (NEW / SUBSUMED / TENSION) by comparing
   against `thread-tree-snapshot.json`. Match on file-path overlap
   first, then on title-keyword overlap.
6. Risk classification.
7. Priority rank.
8. Verification policy.
9. Thread-worthy check.
10. Write `synthesis.md`, `synthesis.json`, **and**
    `thread-proposals.md`.
11. **Update `<root>/.code_survey/index.md`**: prepend a one-line
    entry with session id, recommendation count, NEW/SUBSUMED/TENSION
    counts, top-3 P1 items.

After writing, surface the synthesis in the conversation with:
- One-paragraph executive summary.
- The recommendation table (top 10 if longer).
- The three-bucket count from `thread-proposals.md`.
- Thread-worthy verdict + suggested next operation.

**Verification:**
- All four files exist in the session dir
  (`raw-recommendations.md`, `synthesis.md`, `synthesis.json`,
  `thread-proposals.md`).
- `synthesis.json` parses.
- `synthesis.md` has all required sections (see template).
- Filtered-out appendix has at least the physics-floor drops listed.
- Every recommendation in `synthesis.md` has a NEW / SUBSUMED /
  TENSION tag (no untagged items).
- `index.md` has been touched and the new session is at the top.

---

## Propose-thread

**Trigger:** user wants to spawn a /threads thread from the
synthesis.

**Pre-flight:**
- Confirm a `synthesis.json` and `thread-proposals.md` exist for
  the most recent (or specified) session.
- Confirm the project has /threads bootstrapped (any
  `**/threads/*/thread.json` under root will do).

**Steps:**

1. Read `thread-proposals.md`. **Only the NEW bucket is eligible
   for spawning.** Items in SUBSUMED point at existing threads;
   items in TENSION need user coordination first.
2. For each NEW proposal, read its referenced findings in
   `synthesis.json`.
3. Generate a thread proposal in the format the /threads skill
   expects (see `/threads new` workflow):
   - Subsystem: from `thread-proposals.md` (synthesis attributes
     each proposal to a subsystem based on file paths touched).
   - Slug: `YYYYMMDD-<short-description>`.
   - Title: one-line description.
   - Hypothesis: "items <list> from the code-survey session dated
     <date> land without behavior drift on <e2e_baseline>."
   - Plan hops: phase 1 = one plan-01 with sub-steps; phase 2 =
     one plan per high-risk hop.
   - Verification policy: per the synthesis output.
   - E2E gate: from config's `e2e_baseline`.
4. **Surface the proposal in conversation. Do NOT invoke /threads
   directly.** The user reviews, edits, and invokes /threads
   themselves. This separation matters: it keeps the on-disk
   thread state out of any race with the synthesis being modified.

**Verification:**
- The proposal text is structurally valid for `/threads new`
  (subsystem, slug, hypothesis all present).
- The e2e_baseline is reproduced verbatim from config.
- No SUBSUMED or TENSION items are accidentally proposed as new
  threads.

---

## Diff

**Trigger:** user wants to compare two code-survey artifacts. Two
modes:

- **session-vs-session** — compare two prior `synthesis.json`
  outputs. Useful for tracking refactor progress over time
  ("what changed between last week's survey and this week's?").
- **session-vs-thread** — compare a code-survey session against an
  existing thread's plan-hop step list. Useful when the prior
  survey was lost (only the spawned thread remains) but you want
  an A/B against a fresh run.

**Pre-flight:**
- Run project-root detection.
- Identify the two operands:
  - `--a=<session-id-or-path>` and `--b=<session-id-or-path>` for
    session-vs-session.
  - `--a=<session-id>` and `--thread=<path-to-thread-dir>` for
    session-vs-thread.
- If both operands are sessions, confirm they have
  `synthesis.json`. If one is a thread, confirm it has
  `thread.json` and at least one `plan-*.md` file.

**Steps (session-vs-session):**

1. Load `synthesis.json` from both sessions.
2. Match findings across the two sets. Match key (in priority
   order):
   a. Identical `(file, lines)` tuple → same finding.
   b. Same `file` + overlapping line range + same `lens` → same
      finding.
   c. Same `recommendation` text (after lowercasing/whitespace
      normalize) → same finding.
3. Bucket each pair / singleton:
   - **Stable** — present in both, same verdict, similar
     recommendation.
   - **Drifted** — present in both but verdict or recommendation
     changed.
   - **Resolved** — present in A, absent in B (likely fixed; check
     git log for B's session date to confirm).
   - **New** — absent in A, present in B.
4. Write `<root>/.code_survey/diff-<sessionA>-vs-<sessionB>.md`
   with one section per bucket and a summary table.

**Steps (session-vs-thread):**

The thread's plan-hop steps are the prior recommendation list.
Extract them by parsing the `plan-*.md` files in the thread:

1. For each `plan-*.md`, find the `## Steps` (or `## H1 — `, etc.)
   sections and extract: title, files touched, intended commit
   message. These become the "prior recommendations."
2. Load `thread-proposals.md` and `synthesis.json` from the
   code-survey session.
3. Match thread-step-titles against session findings using the
   same key as session-vs-session, with `file` overlap as the
   primary signal (since plan-hop "files touched" are explicit).
4. Bucket:
   - **Both stacks agree** — thread step has a corresponding
     finding in the session.
   - **Survey adds** — finding in the session that the thread
     doesn't address (potential follow-up thread or scope addition).
   - **Thread covers (survey didn't surface)** — thread step with
     no matching finding (either the thread covered it already,
     the codebase changed since, or the lens missed it this run).
5. Write
   `<root>/.code_survey/diff-<sessionA>-vs-<thread-slug>.md`.

**Verification:**
- Diff artifact exists and parses as Markdown.
- Every finding from both inputs appears in exactly one bucket
  (no double-counting, no orphans).

---

## Re-running operations

- **Re-running scan over the same scope** creates a new
  session-<HHMMSS> directory; the old one stays. Users can compare
  sessions with **Diff**.
- **Re-running synthesize on the same session** overwrites
  `synthesis.md`, `synthesis.json`, `thread-proposals.md`, and
  refreshes the `index.md` entry in place. `raw-recommendations.md`
  is also regenerated. `decisions.json` content (hold/skip/accept)
  is honored.
- **Re-running propose-thread** regenerates the proposal text;
  it doesn't track whether a previous proposal was used.
- **Re-running diff** overwrites the diff artifact.

## Skipped / out-of-scope operations (v1)

- **Watch mode** (continuous re-scan on file change) — out.
- **Auto-/threads invocation** — out; compose, don't bundle.
- **Auto-git operations** — out; surface commit message proposals,
  don't run them.
- **JSON skill-tracker** for self-improvement — out per scope decision.
