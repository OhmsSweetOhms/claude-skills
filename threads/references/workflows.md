# Operation workflows

Step-by-step procedures for each operation. Find the section that
matches the user's ask in `SKILL.md`'s dispatch table, then follow
the steps here.

---

## Bootstrap

**Trigger:** repo has no `threads/` directory; user wants to adopt
the layout.

**Pre-flight:**
- Confirm `threads/` doesn't already exist (don't clobber).
- Decide where `threads/` lives. Default: repo root. If the repo
  has a clear "main package" dir (e.g., `gps_receiver/` in
  gps_design), the user may prefer it nested there. Ask.
- Decide which subsystem dirs to seed. Either:
  - Ask the user explicitly.
  - Infer from top-level package directories and confirm.
  - Start with one reasonable subsystem (often the user's current
    investigation area) and let more accrete naturally.

**Steps:**

1. Create the directory tree:
   ```
   <threads-path>/
     receiver/      # or whatever subsystems were chosen
     ...
   ```
2. Copy `assets/templates/top-level-README.md` to
   `<threads-path>/README.md`. Substitute `{{SUBSYSTEMS}}` with a
   formatted list of the chosen subsystem dirs.
3. Copy `assets/templates/top-level-CONVENTIONS.md` to
   `<threads-path>/CONVENTIONS.md`. No substitutions; this is the
   canonical schema reference.
4. Copy `assets/templates/top-level-threads.json` to
   `<threads-path>/threads.json`. Empty `threads` and
   `promotion_log` arrays.
5. Update `.gitignore`: append
   ```
   # Per-thread regeneratable outputs (exclude files inside temp/, not the dir
   # itself, so git can still track temp/README.md per threads-skill convention).
   <threads-path>/**/temp/*
   !<threads-path>/**/temp/README.md
   ```
6. Commit (only if the user asked you to; otherwise leave staged
   and tell them what to commit).

**Verification:**
- `python3 -c "import json; json.load(open('<threads-path>/threads.json'))"`
  succeeds.
- The user's `git status` shows the new files; nothing unexpected.

---

## New thread

**Trigger:** user wants to start investigating something new.

**Pre-flight:**
- Get the **subsystem** (one of the existing dirs under
  `threads/`, or ask if it doesn't exist yet — adding a subsystem
  is just `mkdir`).
- Get the **slug** (short, kebab-case, descriptive of the
  investigation, NOT the first plan hop).
- Get a **title** (one-line description for `threads.json` and
  `thread.json`).
- Get the **starting context** for plan-01: what's known so far,
  what hypothesis the first hop tests.

**Steps:**

1. Create the thread directory tree:
   ```
   <threads-path>/<subsystem>/<YYYYMMDD>-<slug>/
     diagnostics/
     temp/
     external-comments/
   ```
   Use today's date for `YYYYMMDD`.
2. Copy `assets/templates/thread.json` → `thread.json`. Substitute
   `{{ID}}`, `{{TITLE}}`, `{{TODAY}}`, `{{FIRST_PLAN_FILENAME}}`.
   Set `status: "active"`. Empty arrays for everything else
   (plan_hops gets one entry — see below).
3. Copy `assets/templates/thread-README.md` → `README.md`.
   Substitute placeholders.
4. Copy `assets/templates/thread-handoff.md` → `handoff.md`.
   Substitute placeholders. Seed the first "Session log" entry
   with the user's thread-initialization context: the thread's
   purpose, any prior-thread handoff they pasted, the confirmed-
   green baseline (test suite + pass/fail + wall time), and the
   prescribed reading order for a cold start. If the user
   didn't provide rich context, keep the entry short — don't
   fabricate. From this point onward, `handoff.md` updates
   automatically via the **New-plan-hop** and **Close-thread**
   workflows (they re-check the forward-looking sections and
   append a new session-log entry), and on user request for
   in-session notes.
5. Copy `assets/templates/plan-01-template.md` →
   `plan-01-<slug>.md`. Substitute placeholders. Use the user's
   starting context to fill the "Hypothesis" and "Steps" sections;
   don't fabricate detail beyond what they gave you.
6. Copy `assets/templates/temp-README.md` → `temp/README.md`. The
   table starts empty (will populate as diagnostics emit outputs).
7. Update `<threads-path>/threads.json`: append the new thread to
   `threads[]`. Don't touch `promotion_log[]`.
8. Add a single entry to `thread.json.plan_hops[]`:
   `{"num": 1, "file": "plan-01-<slug>.md", "status": "active", "outcome": null}`.
9. Tell the user: thread directory created, plan-01 has the
   starter content from your message — review and refine before
   starting the actual investigation.

**Verification:**
- Both JSON files parse.
- The new thread shows up in `threads.json`.
- `ls <threads-path>/<subsystem>/<YYYYMMDD>-<slug>/` shows the
  expected files.

---

## New plan hop

**Trigger:** current plan hop resolved/refuted; the next step
deserves its own plan rather than appending to the current one.

**Pre-flight:**
- Confirm thread is `active` (or `blocked` and now unblocking).
- Confirm what `outcome` to write for the previous hop (one-line
  prose).
- Get the new hop's slug (different from the thread slug; describes
  THIS hop's focus).
- Get the new hop's hypothesis or first-step description.

**Steps:**

1. Read `thread.json`. Find the highest `num` in `plan_hops[]`.
   The new hop is `num + 1`.
2. Update the previous hop's entry:
   - If the next hop replaces the previous one (testing a different
     hypothesis after the previous was refuted/inconclusive), set
     `status: "superseded"`.
   - If the next hop continues from a closed result (e.g., a
     refinement after a partial answer), set `status: "closed"`.
   - Fill `outcome` with the one-line prose.
3. Append the new hop:
   `{"num": N, "file": "plan-NN-<slug>.md", "status": "active", "outcome": null}`.
4. Update `thread.json.current_plan` to the new hop's filename.
5. Update `thread.json.updated` to today's date.
6. Update `<threads-path>/threads.json`: find this thread's entry,
   update `current_plan` and `updated`.
7. Copy `assets/templates/plan-01-template.md` →
   `plan-NN-<slug>.md`. Adapt the title and "Parent plan" header
   to reference the previous hop.
8. Update the thread's `README.md` "Plan lineage" table — add the
   new row, update the previous row's status/outcome.
9. **Update `handoff.md`** — the forward-looking sections reference
   the old plan by name and go stale immediately:
   - Bump the `**Last updated:**` line to today.
   - Rewrite the `**Active plan:**` bullet in "Current state" to
     point at the new hop's filename + current hypothesis + anchor
     data (if any).
   - Re-check `**Blockers / in flight:**` and `**Confirmed-green
     baseline:**`. Drop anything that was specific to the closed
     hop; add anything new the new hop introduces.
   - Replace `**What the next session should do first:**` with the
     new hop's first steps (not the old hop's). If the new plan
     has a "Steps" section, derive the next-session items from it.
     Prefer cheapest-first ordering so a cold-start reader has a
     low-friction entry.
   - Update "Cross-references to carry forward" — add the new
     plan's anchor data files, new diagnostics dirs, any new
     reference docs the hop introduced.
   - Update "Reading order for a cold start" — point at the new
     active plan file; demote the closed hop to a "retains recipe
     value only" slot if still useful, or drop if not.
   - Append a **new Session-log entry at the top** describing the
     transition in prose: why the previous hop closed/superseded,
     what the new hop tests, what the key decision points are.
     Include commit hashes when they're known. Preserve all older
     session-log entries as history — they're the thread's memory.

   These edits go in the **same commit** that adds the new hop. A
   handoff that still names the closed plan as active is a
   cold-start trap.

**Verification:**
- `thread.json` parses.
- `thread.json.plan_hops[]` is in `num` order with one and only
  one `active`.
- `thread.json.current_plan` matches the new active hop's filename.
- `handoff.md`'s "Current state" bullet names the new plan file,
  not the closed one.
- `handoff.md`'s "What the next session should do first" reflects
  the new hop's steps, not the closed hop's.

---

## Findings snapshot

**Trigger:** a plan hop closed or a thread reached a decision
point; capture current understanding in a snapshot.

**Pre-flight:**
- Get the snapshot date (default: today).
- Get which plan hop this snapshot wraps up.
- Get the prose for the snapshot. Don't make this up — ask the
  user for the substance.

**Steps:**

1. Copy `assets/templates/findings-template.md` →
   `findings-<YYYY-MM-DD>.md`. Fill in date, plan-hop reference,
   and the user-provided prose.
2. Append to `thread.json.findings[]`:
   `{"file": "findings-YYYY-MM-DD.md", "date": "YYYY-MM-DD", "plan_hop": N}`.
3. Update `thread.json.updated`.
4. Update the thread's `README.md` "Findings snapshots" table.

**Verification:**
- `thread.json` parses.
- `findings-*.md` is in the thread root, not in `temp/`.

---

## Register diagnostic

**Trigger:** user wrote (or moved) a `diagnose_*.py` into the
thread's `diagnostics/` and wants to track it.

**Pre-flight:**
- Confirm the file lives at `<thread>/diagnostics/diagnose_*.py`.
  If it's at repo root or another location, move it first via
  `git mv` so history is preserved.
- Get the plan hop it belongs to.
- Get a one-line `purpose` description.

**Steps:**

1. Append to `thread.json.diagnostics[]`:
   ```json
   {"script": "diagnostics/diagnose_<name>.py", "plan_hop": N, "purpose": "<one-line>"}
   ```
2. If the diagnostic produces a regeneratable output the user
   wants tracked: append to `thread.json.temp[]`:
   ```json
   {"file": "temp/<output>", "plan_hop": N, "regenerate_with": "<exact shell cmd>"}
   ```
   Also update `temp/README.md` to add the regen command row.
3. Update `thread.json.updated`.

**Verification:**
- `thread.json` parses.
- Script is at the registered path; `temp/<output>`'s regen
  command actually works (run it once if practical).

---

## Import external review

**Trigger:** Codex, claude.ai, or a colleague sent feedback on the
thread (a plan hop, a diagnostic, the overall direction).

**Pre-flight:**
- Get the **source** (`codex`, `claude-ai`, `colleague-<name>`,
  `other`) and date.
- Get the **subject** (usually a plan-hop filename, or a short
  topic in kebab-case).
- Get the **kind** (`comment`, `edit`, `mixed`).
- Get the **raw content** verbatim from the user. If they pasted
  prose, use it as-is. If they pasted code or a markdown file,
  preserve formatting.

**Steps:**

1. Filename: `external-comments/<YYYYMMDD>-<source>-<subject>.md`.
2. Copy `assets/templates/external-comment-template.md` to that
   path. Substitute frontmatter fields and the title line.
3. Inside the "Raw content (verbatim — do not edit)" section:
   - For markdown content: paste between the
     `<!-- BEGIN RAW -->` / `<!-- END RAW -->` delimiters as-is.
   - For code: wrap in a fenced ```python (or appropriate
     language) block between the delimiters.
   - For mixed (e.g., reviewer's full email with attachments):
     paste the prose; for binary attachments, note their location
     (e.g., "PDF at <path>").
4. Set frontmatter `disposition: pending`. Leave `merged_into: []`.
5. Triage table starts with placeholder rows. Don't fabricate the
   triage — that's a separate human pass after capture.
6. Append to `thread.json.external_reviews[]`:
   ```json
   {
     "date": "YYYY-MM-DD",
     "source": "<source>",
     "subject": "<subject-line>",
     "kind": "<kind>",
     "disposition": "pending",
     "file": "external-comments/<YYYYMMDD>-<source>-<subject>.md",
     "merged_into": []
   }
   ```
7. Update the thread's `README.md` "External reviews" table.

**The cardinal rule:** don't paraphrase the raw content. Don't
"clean up" the reviewer's wording. The raw section is the
attribution record. If you spot an obvious typo, note it in the
triage's "Merge notes" — never edit the raw section.

**When the review is later triaged → merged:**

- Edit each accepted plan/code change in a separate commit.
- Write each commit's hash into the triage table's "Commit"
  column.
- Once every accepted point has a hash, flip frontmatter
  `disposition: merged` and populate `merged_into[]` with the
  hashes.
- Update the corresponding `thread.json.external_reviews[]`
  entry's `disposition` and `merged_into`.

---

## Promote diagnostic

**Trigger:** a diagnostic has proven valuable enough to run forever
as a regression gate.

**Pre-flight:**
- Confirm the diagnostic lives at
  `<thread>/diagnostics/diagnose_<X>.py`.
- Decide the destination test path. For Python projects this is
  usually `<package>/tests/test_<X>_regression.py`. Confirm with
  the user — frameworks vary (pytest vs unittest, tests/ vs
  test/, repo-root vs package-relative).
- Get the **assertion threshold** the test will gate on. The
  diagnostic was written as exploratory; the test needs a concrete
  pass/fail bar, usually with a measured baseline + headroom.
- Decide which test group/tier the test belongs to (unit,
  integration, regression, stress) — ask if unclear.

**Steps:**

1. `git mv <thread>/diagnostics/diagnose_<X>.py <test-path>`.
2. Refactor the moved file:
   - Remove the CLI / argparse / `if __name__ == "__main__"`
     block.
   - Wrap the assertions in a `unittest.TestCase` (or pytest test
     function — match the project's existing convention).
   - Use `setUp` / `tearDown` to install/restore any monkey-patches
     so the test doesn't leak state to siblings.
   - Keep the helper functions module-level so other thread
     diagnostics can still import them.
   - If the test imports from sibling diagnostics, add a `sys.path`
     prepend computed from `Path(__file__)` — the thread's
     `diagnostics/` dir isn't a Python package and shouldn't
     become one.
3. Register in the project's test runner:
   - For `run_tests.py`-style: add the dotted-path entry to the
     appropriate group list.
   - For pytest with auto-discovery: nothing to do beyond
     filename.
4. Update `<thread>/thread.json`:
   - Remove the diagnostic entry from `diagnostics[]`.
   - Append to `promotions[]`:
     ```json
     {
       "date": "YYYY-MM-DD",
       "from": "diagnostics/diagnose_<X>.py",
       "to": "<test-path>",
       "reason": "<one-line: the gate this enforces>",
       "plan_hop": N
     }
     ```
5. Update `<threads-path>/threads.json.promotion_log[]` with the
   mirror entry.
6. Update the thread's `README.md` "Promoted artefacts" table.

**Why `git mv`, not copy:** `git log --follow <test-path>` chains
back through the diagnostic's history. A copy creates two
independent files that drift; `git mv` makes the test the
diagnostic's continuation.

**A note on similarity heuristics:** if the refactor reshapes the
file heavily, git's default 50% rename-detection threshold may
fail and the move shows as `delete + add` in the staged state.
That's still recoverable: `git log --follow -M20% <test-path>`
chains across lower similarity. Note this in a comment near the
test class.

**Verification:**
- The test runs. `python3 -m pytest <test-path>` (or the project's
  runner) finds and executes it.
- `git log --follow <test-path>` shows the diagnostic's commits
  in the chain (use `-M20%` if the refactor was heavy).
- `thread.json.diagnostics[]` no longer lists it;
  `thread.json.promotions[]` has the new entry.

---

## Close thread

**Trigger:** investigation is complete (or supersded by another
thread, or blocked indefinitely).

**Pre-flight:**
- Confirm the user wants to close vs `superseded` vs `blocked`.
  Each has a specific meaning — see `references/layout.md`.
- For `closed`/`superseded`, confirm a final findings snapshot
  exists. If not, write one first via the **Findings snapshot**
  workflow.

**Steps:**

1. Set `thread.json.status` to the chosen value.
2. Set the active plan hop's `status` to match (typically `closed`
   or `superseded`) and fill its `outcome`.
3. Update `thread.json.updated`.
4. Update `<threads-path>/threads.json`: find this thread's entry,
   update `status` and `updated`.
5. Update the thread's `README.md` status header.
6. **Update `handoff.md`** — it was pointing at an active plan that
   no longer exists:
   - Bump the `**Last updated:**` line to today.
   - Rewrite the `**Active plan:**` bullet to reflect closure
     (e.g. `"(none — thread closed YYYY-MM-DD)"` with a pointer
     to the final findings snapshot and the thread's outcome).
   - Shorten or remove `**What the next session should do first:**`
     — for a closed thread this is either empty, or points at
     downstream threads that consumed the outcome. For a
     `superseded` thread, name the successor thread explicitly.
     For `blocked`, name the blocker + what unblocks it.
   - Simplify "Reading order for a cold start" — the reader is
     consulting the thread as history, not to continue work.
     Point them at the final findings snapshot and the outcome
     summary rather than at the (now-closed) plan.
   - Append a **new Session-log entry at the top**: closure
     rationale, what the thread established, what was ruled out,
     where follow-up work (if any) lives. Include the closure
     commit hash. Preserve older session-log entries as history.
7. Don't delete the thread directory. It's the permanent record.

**Verification:**
- `thread.json.status` and `threads.json.threads[].status` agree.
- The active plan hop's `outcome` is filled.
- `handoff.md` no longer names a plan as "active" — it either
  reflects closure, names a successor, or describes the blocker.

---

## Link research

See `references/research-integration.md` for the bidirectional-link
mechanics. Short version:

1. Add an entry to `thread.json.linked_research[]` with `path`,
   `title`, `spawned_by_this_thread`, and (optionally)
   `consumed_artifacts[]`.
2. If you can write to the research session's
   `session-manifest.json` (it exists, schema is permissive), add a
   `spawning_thread: "<subsystem>/<YYYYMMDD>-<slug>"` field.
3. If both sides exist already and disagree, ask the user which
   side is correct before overwriting.
4. Update `thread.json.updated` and the thread's `README.md`
   "Research linkage" table.
