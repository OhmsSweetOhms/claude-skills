# Operation workflows

Step-by-step procedures for each operation. Find the section that
matches the user's ask in `SKILL.md`'s dispatch table, then follow
the steps here.

---

## Bootstrap

**Trigger:** user wants to set up code-survey for a project, or
**Scan** runs and finds no config.

**Pre-flight:**
- Confirm `.claude/code-survey-config.json` doesn't already exist
  (use `--force` to overwrite).
- Read `CLAUDE.md` at project root and any nested ones.

**Steps:**

1. Read all CLAUDE.md files in scope.
2. Extract candidate config values per `references/config.md`:
   - "Hard Requirements" / "Constraints" → boundaries, physics_floor.
   - "Conventions" / "Architecture" → keep_rules.
   - "Running" / "Tests" → verification_command.
   - "Block IDs" / canonical lists → boundaries entries.
   - Phrases about precision/tolerance → physics_floor.
3. Copy `assets/templates/code-survey-config.json` to
   `.claude/code-survey-config.json`. Fill placeholders with the
   extracted values.
4. **Show the user the proposed config** in conversation, annotated
   with where each non-default value came from. Format:
   ```
   "boundaries[0]" — from gps_receiver/CLAUDE.md § Module Layout:
     "Each file mirrors one FPGA/PS block."
   "physics_floor" — from CLAUDE.md § Hard Requirements rule 2:
     "Nano-seconds matter. 1 ns ≈ 30 cm of pseudorange."
   ```
5. Wait for the user to review, edit, and accept.
6. Write the final config to disk.
7. Tell the user to commit it (the config is project state).

**Verification:**
- File parses as JSON.
- `version: 1`, `project_name` is non-empty.
- All globs in `boundaries[]` and `keep_rules[]` match at least one
  file in the project (warn if not).

---

## Scan

**Trigger:** user wants to run a code survey.

**Pre-flight:**
- Confirm config exists. If not, run **Bootstrap** first (or warn
  and proceed in generic mode if user insists).
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

1. Create the run directory:
   `.claude/workspace/code-survey/<YYYYMMDD-HHMM>/`.
2. Snapshot the config and scope:
   ```
   config.snapshot.json   # copy of .claude/code-survey-config.json
   scope.json             # which files are in scope, and why
   ```
3. For each lens in the kit:
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
4. After all lenses finish, tell the user the scan is complete and
   surface the per-pass file list. Recommend they run
   `/code-survey synthesize` next.

**Lens parallelism note.** Lenses 1, 2, 3 (the default kit) are
independent — run them all in parallel. Lenses 6, 7, 8 are also
independent. Don't sequence them unless a lens depends on another's
output (none currently do).

**Verification:**
- Run dir exists with expected lens subdirs.
- Each agent report parses as Markdown and has at least one finding
  or an explicit "no findings" verdict.

---

## Synthesize

**Trigger:** user wants to wrap up a scan, or run synthesis again
after hold/skip decisions.

**Pre-flight:**
- Identify the scan run to synthesize. Default: most recent.
  `--run=<dir>` overrides.
- Read all per-agent reports under `pass-N-<lens>/`.
- Read `config.snapshot.json`.
- If `decisions.json` exists in the run dir, read it (re-synthesis
  case).

**Steps:**

Follow the procedure in `references/synthesis.md` end-to-end:

1. Gather all findings.
2. Cross-pass reinforcement check.
3. Physics-floor filter.
4. Risk classification.
5. Priority rank.
6. Verification policy.
7. Thread-worthy check.
8. Write `synthesis.md` and `synthesis.json`.

After writing, surface the synthesis in the conversation with:
- One-paragraph executive summary.
- The recommendation table (top 10 if longer).
- Thread-worthy verdict + suggested next operation.

**Verification:**
- Both files exist in the run dir.
- `synthesis.json` parses.
- `synthesis.md` has all required sections (see template).
- Filtered-out appendix has at least the physics-floor drops listed.

---

## Propose-thread

**Trigger:** user wants to spawn a /threads thread from the
synthesis.

**Pre-flight:**
- Confirm a `synthesis.json` exists for the most recent (or
  specified) run.
- Confirm the project has /threads bootstrapped.
  (`<repo>/threads/threads.json` or similar.)

**Steps:**

1. Read `synthesis.json`.
2. Partition findings by risk into Phase 1 (low/medium-risk batch)
   and Phase 2 (high-risk per-item).
3. Generate a thread proposal in the format the /threads skill
   expects (see `/threads new` workflow):
   - Subsystem: infer from the project structure or ask.
   - Slug: `YYYYMMDD-<short-description>`.
   - Title: one-line description.
   - Hypothesis: "items <list> from the code-survey run dated
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

---

## Re-running operations

- **Re-running scan over the same scope** creates a new dated run
  directory; the old one stays. Users can compare runs over time.
- **Re-running synthesize on the same run** overwrites
  `synthesis.md` and `synthesis.json` in place, picking up any
  new `decisions.json` content (hold/skip/accept).
- **Re-running propose-thread** regenerates the proposal text;
  it doesn't track whether a previous proposal was used.

## Skipped / out-of-scope operations (v1)

- **Watch mode** (continuous re-scan on file change) — out.
- **Auto-/threads invocation** — out; compose, don't bundle.
- **Auto-git operations** — out; surface commit message proposals,
  don't run them.
- **JSON skill-tracker** for self-improvement — out per scope decision.
