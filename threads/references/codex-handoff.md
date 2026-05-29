# Codex worktree handoff

A controlled pattern for offloading focused source-code work to a
Codex agent across the lifetime of a thread, while keeping
bookkeeping on `main`.

Two workflows, mirroring the dispatch table in `SKILL.md`:

- **Codex worktree handoff** — set up an isolated worktree at thread
  inception (idempotent; safe to re-run), emit the launch packet
  pointing Codex at the plan file, then hand it off. Re-invokeable
  across the thread's plan hops. The plan file IS the launch prompt.
- **Codex worktree merge-back** — run **once**, only at thread close,
  and **only on explicit user request**. Merges the long-lived
  worktree branch into `main`.

Plus a troubleshooting section seeded by the case study at
`.threads/receiver/20260427-chi-square-raim-design/handoff.md`
(2026-04-29-afternoon session log) — three concrete pitfalls and
their fixes. (The case study originally surfaced five issues; two
were specific to a sandboxed codex environment that this workflow
no longer uses. They survive in the case-study handoff and in
gps_design commits `4ca46a8` + `1f351dc` for the historical record.)

For the artifact contract Codex must emit at the end of each hop,
read `references/codex-handback.md`. That file defines the JSON and
Markdown pair, the root `codex-handoff/<plan-id>/` inbox, lifecycle
visibility while the files live only on the worktree branch, and the
required consumer triage before merge-back or next-hop activation.

## Lifetime model — read this first

The branch and worktree are **long-lived for the entire thread**:

- **One worktree per thread.** Bootstrap runs once at thread
  inception (or any time afterwards if the worktree was never set
  up). Re-runs are idempotent — they refresh the venv link and
  `.envrc` but don't re-cut the branch.
- **Many codex runs per worktree.** Each plan hop that needs
  source-code work hands off to codex against the same worktree.
  Codex leaves edits unstaged; the user's claude session on `main`
  reviews and commits them onto the worktree branch (the codex
  sandbox typically can't write `.git` metadata; the user's session
  on `main` can write into `<main>/.git/worktrees/<name>/` because
  it's not sandboxed).
- **Merge-back is a single terminal event.** Triggered only when:
  - The user explicitly asks for it
    (e.g., "merge the codex worktree back").
  - All plan hops are complete and the thread is ready to close.
  - The merge-back script never auto-merges; it always shows the
    incoming diff/log and asks for confirmation.

This is intentional. Merging mid-thread fragments the lineage of
codex's contribution and produces commits on `main` that don't
correspond to plan-hop closures. Keeping the branch isolated until
thread close means `main` gets one (or a small number of) clean
commits that map to the thread's PASS/FAIL outcome.

## The thread ↔ worktree link

A thread's `thread.json` carries a back-pointer to its worktree as
a top-level `codex_worktrees[]` array (see
`references/schemas.md`). Why this matters:

- **Status review must not miss in-flight codex work.** A thread
  with an active codex worktree may have *zero* recent commits on
  `main` and still be live. Without the back-pointer the review
  pass would mis-classify it as stale.
- **Cold-start handoff.** A future session reading the thread sees
  the worktree path and branch in one place — `thread.json` — and
  can `cd` into the worktree to see what's actually been written.
- **Mechanical cleanup at thread close.** Merge-back updates
  `status: "merged"` and records `merged_into: "<commit-hash>"`,
  same audit-trail discipline as `external_reviews[].merged_into`.
  An "active" worktree on a closed thread is a triage flag.

The bootstrap script prints a JSON snippet to paste into
`thread.json`; the merge-back script prints the field-update to
make. Neither edits `thread.json` directly — JSON edits stay in the
user's claude session where the rest of the thread's bookkeeping
lives.

### Handoff inbox and handback visibility before merge-back

Each thread's worktree has a single inbox per plan hop. The inbox is
the canonical location for everything Codex reads or writes — prompt,
README, handback, and session-created scripts/temp/artifacts. Splitting
the handoff infrastructure across two repos (e.g. prompt in
`.threads/`, handback in the worktree) is an anti-pattern that creates
drift; the inbox keeps it self-contained.

> **Public-repo caveat.** The inbox is **host-local**: `prompt.md` and the
> handback artifacts contain absolute paths (and thus the local username),
> because Codex needs real paths to operate. That is fine for a private
> project repo, where the inbox lands on `main` at merge-back as useful
> bookkeeping. But if the worktree's repo is **public** (e.g. a published
> skills repo), the inbox is a fingerprint-leak surface — **gitignore
> `codex-handoff/` in that repo** so the scaffolding never gets committed
> or published. The fix is "don't publish the inbox," not "scrub the paths"
> (they are not portable). Lesson from socks/20260424-hil-streaming-mode:
> a merge-back into a public skills repo nearly published the inbox's
> host-absolute paths; the work was instead reconstructed clean (skill
> files only) with `codex-handoff/` gitignored.

```text
<worktree>/codex-handoff/<plan-id>/
  README.md            (main-session-written: inbox description, who writes what)
  prompt.md            (main-session-written: curated launch prompt; Codex's input)
  handback.json        (Codex-written: machine-readable closure record)
  handback.md          (Codex-written: human-readable companion)
  scripts/             (Codex-written: throwaway probes, debug tests, helpers)
  temp/                (Codex-written: bulky or disposable generated working files)
  artifacts/           (Codex-written: curated evidence cited by the handback)
  socks_tracker.json   (Codex-written, optional: session-scoped SOCKS-skill
                        gap log; NOT created in project docs/ during a hop)
```

**Session-scoped trackers belong in the inbox, not in project `docs/`.**
When a Codex hop is instructed to create or update a SOCKS tracker
(or any similar session-scoped log), the artifact lives in
`codex-handoff/<plan-id>/socks_tracker.json`. The SOCKS-tracker
skill's default (`docs/socks_tracker.json` relative to the current
project directory) is appropriate for long-lived project trackers
that the main session maintains across hops, but inappropriate as a
Codex-hop default: a monorepo Codex worktree has multiple "project
directories" (`modules/<name>/`, `systems/<name>/`, the worktree
root), and per-hop tracker creation in each scatters tracker JSONs
across `docs/`, `modules/*/docs/`, `systems/*/docs/` with no single
canonical location. The inbox is the single canonical session-scoped
home. The main session promotes a tracker into a long-lived
`<project>/docs/socks_tracker.json` at triage if it has cross-hop
value; otherwise the tracker rides the worktree branch until
merge-back like any other inbox artifact. Plan files that ask Codex
to create or update a tracker must specify the inbox path
explicitly and instruct Codex to write the JSON directly (using the
schema at `~/.claude/skills/socks-tracker/SKILL.md`) rather than
invoking `/socks-tracker --log`, since the skill's cwd-relative
default would otherwise land the file in the wrong place.

The main session populates `README.md` (from
`assets/templates/codex-handoff-dir-README.md`) and `prompt.md`
(emitted via `scripts/emit_codex_launch_packet.py`) BEFORE launching
Codex. The emitter writes its output to
`<worktree>/codex-handoff/<plan-id>/prompt.md` when `--out` is
supplied; without `--out`, it prints the packet to stdout for
inspection or direct paste.

Codex writes the handback and session-created material during the
run. Main session reads after Codex exits, runs
`scripts/triage_codex_handback.py` for first-pass classification, and
promotes durable material into `.threads/`, tracked `data/`,
permanent tests, or follow-up plans.

Handback files are written on the Codex worktree branch. They do not
appear in the main checkout until terminal merge-back unless the main
session promotes or mirrors them. When a plan hop closes before the
worktree is merged, update that hop's `thread.json::plan_hops[].outcome`
on `main` with a prose pointer:

```text
(handback: codex-handoff/<plan-id>/handback.{json,md} on worktree
branch <branch> at <worktree-head-sha>; path <absolute-worktree-path>)
```

The path and branch must match `thread.json::codex_worktrees[]`.
After merge-back, leave the historical pointer in place or append
`merged to main at <merge-sha>` if the original wording would
otherwise confuse a cold-start reader.

Legacy worktree handbacks may still exist under
`.threads/<thread-id>/codex-handback-<plan-id>.{json,md}`. New
sessions should not write that layout.

## Why a worktree (and not a branch on main)

A separate worktree gives codex its own filesystem dir to mutate
without touching the user's main checkout. The branch lives in the
shared `.git`, so once thread closes it merges cleanly back. Three
properties make this safe:

- **No interleave with the user's edits on main.** Codex can run
  tests, edit files, mess with paths — none of it shows up on
  `main` until merge-back.
- **Cheap rollback.** If the agent goes off the rails, the user
  deletes the worktree and the branch. Nothing on `main` to undo.
- **One-thread one-worktree.** The slug-named branch + worktree
  dir keep parallel codex sessions from stomping each other.

## Codex worktree handoff

**Trigger:** user asks to hand a thread (or a specific plan hop) off
to a codex agent. Phrasings: "hand thread X off to codex", "spawn a
codex worktree on X", "spawn codex on X", "run codex on X".

**Pre-flight:**

- The thread exists at `.threads/<subsystem>/<YYYYMMDD>-<slug>/` and
  has an active plan with a clear next-step bullet list in
  `handoff.md` "What the next session should do first".
- The main checkout has a usable `.venv` (else the symlink trick
  doesn't help — the agent will fall back to the system Python and
  hit the SciPy/NumPy ABI trap from issue #1).
- `git fetch origin main` succeeds (else bootstrap can't cut a
  fresh-from-`origin/main` branch).
- The plan file at `.threads/<thread-id>/<plan-NN>-*.md` is fleshed
  out per the tiered template (`assets/templates/plan-01-template.md`):
  base sections always filled, Codex add-ons below the divider filled
  when the hop is a Codex hop. The plan IS the launch prompt; the
  launch packet only adds mechanical facts.

**Steps:**

1. **Resolve the slug.** From a thread id like
   `receiver/20260427-chi-square-raim-design`, the slug component
   is `chi-square-raim-design` — strip the `<YYYYMMDD>-` prefix.
   Branch name and worktree-dir suffix both match.

2. **Run the bootstrap script.** From the main checkout:
   ```bash
   bash ~/.claude/skills/threads/scripts/bootstrap_codex_worktree.sh \
       chi-square-raim-design
   ```
   The script:
   - `git fetch origin && git worktree add -b <slug> ../<repo>-<slug> origin/main`
     — always cuts from current `origin/main` HEAD, never from a
     stale local branch. If the worktree already exists it skips
     `worktree add` and just refreshes the venv link and `.envrc`.
   - Symlinks `.venv` from the main checkout
     (`ln -s ../<repo>/.venv .venv`).
   - Writes `.envrc` exporting `PYTHON=<worktree>/.venv/bin/python`
     so any `source .envrc` pins Python to the venv-symlink path.
   - Installs a worktree-local pre-commit hook that refuses staged
     `.threads/` changes. Codex session material goes in the root
     `codex-handoff/<plan-id>/` inbox.
   - Creates `codex-handoff/<plan-id>/` with `scripts/`, `temp/`,
     and `artifacts/` when `--plan-id` is provided or inferred.
   - Prints a `codex_worktrees[]` JSON snippet for the user to paste
     into `thread.json` (only on first creation; idempotent re-runs
     skip this).

   If you already know the thread id and plan id, render the prompt
   scaffold in the same invocation:
   ```bash
   bash ~/.claude/skills/threads/scripts/bootstrap_codex_worktree.sh \
       chi-square-raim-design \
       --thread-id receiver/20260427-chi-square-raim-design \
       --plan-id plan-03 \
       --render-prompt-out .threads/receiver/20260427-chi-square-raim-design/codex-handoff-plan-03.md
   ```

3. **Update `thread.json` with the worktree link** (first-time
   bootstrap only). Paste the printed snippet into the thread's
   `thread.json` as `codex_worktrees[0]`. Set `status: "active"`
   and leave `merged_into` / `merged_at` as `null`. Run the indexer
   so the registry sees the new field:
   ```bash
   python3 ~/.claude/skills/threads/scripts/index_threads_research.py
   ```

4. **Emit the launch packet.** Run:
   ```bash
   python3 ~/.claude/skills/threads/scripts/emit_codex_launch_packet.py \
       --main-repo . \
       --thread-id <subsystem/YYYYMMDD-slug> \
       --plan-id <plan-NN> \
       --out <worktree>/codex-handoff/<plan-NN>/prompt.md
   ```

   The emitter reads `thread.json::codex_worktrees[]` for the worktree
   path + branch, calls `git -C <worktree> rev-parse --short HEAD` for
   the base SHA, locates the plan file by glob, and writes the launch
   packet. Mechanical contents:

   - **Plan file** (absolute path) — Codex reads this as turn 1.
   - **Worktree** (absolute path) — where Codex does source-code work.
   - **Branch** — the long-lived worktree branch name.
   - **Base SHA** — current worktree HEAD.
   - **Handback inbox** — `<worktree>/codex-handoff/<plan-id>/` (the
     contract `references/codex-handback.md` defines).
   - **Thread + plan IDs.**

   Plus two generic operational rules baked into the script's output:

   - **Don't push the branch.** Worktree merge-back is a single terminal
     event at thread close, not at plan close.
   - **Write structured handback** per `references/codex-handback.md` to
     `<worktree>/codex-handoff/<plan-id>/handback.{json,md}` with the
     required gates / discoveries / follow_ons / blockers / investigations
     / handoff_artifacts shape.

   Any plan-specific operational rules live in the plan file's "Hard
   constraints" section (the tiered template's Codex add-on sections).
   **The plan file IS the launch prompt.** Codex reads it directly when
   given the absolute path; the launch packet only carries mechanical
   facts pointing at it.

   The plan must be fleshed out before launch per
   `assets/templates/plan-01-template.md`'s tiered template: base
   sections always filled, Codex add-ons below the divider filled when
   the hop is a Codex hop. The launch packet does not infer task
   scope, deliverables, tests, or constraints from anywhere — those
   live in the plan.

   The launch packet at `<worktree>/codex-handoff/<plan-id>/prompt.md`
   sits alongside the future handback artifacts. Both ride the
   worktree branch until terminal merge-back at thread close.

   If the same hop is launched a second time (e.g. after a Codex
   crash), regenerate the packet — the SHA will have moved forward if
   Codex committed anything, and the regen captures that.

5. **You (the user) open a sidecar terminal** — a separate
   tab, window, or pane in your terminal app on this same machine —
   and run codex interactively in the worktree:
   ```bash
   cd <worktree>
   source .envrc
   codex
   ```
   Then paste the launch packet from step 4 as the first turn (the
   plan-file absolute path is the first line of the packet so it can
   be copied without scrolling). The codex TUI is the watch-and-interact
   surface: events stream live, approval gates fire when codex wants
   to run a tool, and you can interject mid-thought.

   Claude (the main session) cannot launch this terminal for you —
   spawning an interactive TTY isn't possible from inside its own
   shell. The bootstrap script's final stdout block prints the
   exact `cd / source / codex` invocation; copy-paste it into the
   sidecar terminal.

6. **Watch + steer.** As codex works, the TUI shows every event
   (tool calls, file edits, agent messages, tool results). Approve
   gates as they fire. If codex goes off track, type a redirect
   message — that fires a `turn/start` against the same thread
   without losing context.

7. **Commit the codex output to the worktree branch.** When codex
   stops at a sensible checkpoint (or you intervene to pause it),
   review `git -C <worktree> diff` and either:
   - let codex commit on the worktree branch via the TUI (it has
     full git access in interactive mode), or
   - exit the TUI and commit yourself from the main claude session:
     ```bash
     cd <worktree>
     git add <files>
     git commit -m "..."   # subject + body per project commit conventions
     ```
   Either way, commits land on the **worktree branch**, never on
   `main`. Each plan hop's codex work typically produces one commit
   on the branch; multiple hops accumulate as separate commits —
   natural lineage that the merge-back absorbs as one unit at
   thread close.

   `.threads/` updates and the indexer regen happen on `main`, not
   on the worktree branch. Codex writes only source/test changes and
   `codex-handoff/<plan-id>/` session material.

**Verification:**

- `git -C <worktree> rev-parse HEAD` exists and (initially) matches
  `origin/main`. After the first codex commit the worktree HEAD
  advances ahead of main; that's expected.
- `<worktree>/.venv/bin/python -c "import sys; print(sys.executable)"`
  prints the symlinked path (proves the link resolves).
- `thread.json.codex_worktrees[0].status == "active"` and
  `path` / `branch` match what bootstrap printed.

## Retroactive handback

**Trigger:** a plan hop has already run or closed, but
`codex-handoff/<plan-id>/handback.json` and `.md` were not produced
at the time. This most often happens when a thread predates the
structured handback contract.

**Pre-flight:**

- The thread has an existing Codex worktree in
  `thread.json.codex_worktrees[]`; use that worktree rather than
  cutting a new one.
- Identify the plan id (`plan-02`, `plan-03`, etc.), the plan file,
  and the commit range that contains the plan's source work.
- Check both the new root inbox and the legacy `.threads/` location
  for existing handback files before reconstructing.
- Accept that chat-only discoveries cannot be recovered. The
  retroactive handback records committed evidence, not memory.

**Steps:**

1. **Resolve evidence.** Collect the plan file, `thread.json`,
   `handoff.md`, relevant findings files, and the worktree commit
   range. The range should be precise enough that Codex can list the
   commits that belong to the hop without swallowing later work.

2. **Fill the recovery prompt.** Use:
   ```text
   ~/.claude/skills/threads/assets/templates/codex-handback-retroactive-prompt.md
   ```
   Replace every placeholder, including the root handoff output paths
   and schema path. The prompt should state whether the main session
   has already closed the plan; if so, include `closure_status:
   closed` or `superseded` as applicable.

3. **Run Codex in the existing worktree.** The prompt is
   reconstruction-only: no source edits, no `.threads/` edits, only
   the root handoff artifacts.

4. **Commit the artifacts on the worktree branch.** Use a subject
   like:
   ```text
   plan-02 retroactive handback: reconstruct closure from committed evidence
   ```

5. **Update main-side pointers.** On `main`, update the closed plan
   hop's `outcome` prose with the worktree-only pointer described in
   **Handoff inbox and handback visibility before merge-back**. The
   main session may promote or mirror selected inbox content after
   triage; do not have Codex write those promoted `.threads/` files.

6. **Process the reconstructed handback.** Run the same consumer
   triage as a forward handback. Retroactive handbacks often have
   empty session-only arrays, but gate caveats and blockers can still
   be visible from committed evidence.

**Status-review flag:** `status_review.py` flags a closed or
superseded plan hop on a codex-enabled thread as
`missing_codex_handback` when neither the main checkout nor the
recorded worktree path contains the handback pair.

## Process codex handback

**Trigger:** a forward or retroactive handback exists and the main
session needs to decide what to do with `discoveries[]`,
`follow_ons[]`, `investigations[]`, `blockers[]`, or unresolved
`gates[].caveats[]`. Run this before merge-back, before activating
the next plan hop, or any time status review flags
`untriaged_codex_handback`.

**Steps:**

1. **Locate the handback JSON.** If the artifacts are worktree-only,
   use `thread.json.codex_worktrees[].path` and the plan id:
   ```bash
   python3 ~/.claude/skills/threads/scripts/triage_codex_handback.py \
       <worktree>/codex-handoff/<plan-id>/handback.json
   ```

2. **Classify every row.** Use exactly one disposition per item:
   - `pre-merge blocker` — resolve on the worktree before
     merge-back. Gate caveats that affect CI, clean-checkout
     behavior, portability, or reproducibility usually land here.
   - `post-merge follow-up` — route into a new plan hop, new thread,
     or backlog after merge.
   - `accepted as-is` — retain as context; no code or thread action.

3. **Record the decision.** Save the reviewed table as:
   ```text
   <thread>/codex-handback-<plan-id>-triage.md
   ```
   Prefer the main checkout thread directory so the triage record is
   visible before worktree merge-back. If the record must live on the
   worktree branch temporarily, use the same filename next to the
   handback pair and mention it in `handoff.md`.

4. **Act on pre-merge blockers before merge-back.** If a row is a
   pre-merge blocker, either resolve it on the worktree branch and
   cite the resolving commit in the triage table, or explicitly
   decide not to merge yet. Do not bury it as a future follow-up.

5. **Route post-merge follow-ups.** Create or update the successor
   plan hop, a new thread, or a backlog note. The arm/PS.B12 spawn
   intent from the synth-tropo session is an example of main-session
   routing that does not belong in a Codex handback unless Codex
   emitted it.

**Status-review flag:** `status_review.py` flags
`untriaged_codex_handback` when a visible handback contains blockers,
follow-ons, discovery follow-ups, or unresolved gate caveats but no
`codex-handback-<plan-id>-triage.md` record is visible.

## Codex worktree merge-back

**Trigger:** *only* when the user explicitly asks for it, AND the
thread is ready to close (all plan hops resolved). Phrasings:
"merge the codex worktree back", "the codex agent finished, pull
the work in", "thread is done, land the worktree".

**This workflow runs once per thread.** If the user invokes it
mid-thread, push back: "All plan hops resolved? Merge-back is the
terminal event for the worktree — confirm before I run it." Only
proceed on explicit confirmation.

**Pre-flight:**

- All plan hops in `thread.json.plan_hops[]` are `closed` or
  `superseded`.
- The user explicitly requested the merge.
- The main checkout is either clean, or dirty only with
  closure-session bookkeeping under `.threads/` and
  `.research/INDEX.json`. The merge-back script can stash that
  bookkeeping, merge the worktree branch, then pop the bookkeeping
  overlay back for the follow-up commit. Park unrelated dirty files
  separately before merge-back.
- `thread.json.codex_worktrees[<i>].status == "active"` for the
  worktree being merged. If `merged` already, the worktree was
  landed in a prior pass — abort.
- Every visible `codex-handoff/<plan-id>/handback.json` or legacy
  `codex-handback-<plan-id>.json` with actionable blockers,
  follow-ons, discovery follow-ups, handoff artifacts needing
  promotion, or unresolved `gates[].caveats[]` has been processed by
  **Process codex handback**. No `pre-merge blocker` row remains
  unresolved.

**Steps:**

1. **Run the merge-back script.** From the main checkout:
   ```bash
   bash ~/.claude/skills/threads/scripts/merge_codex_worktree_back.sh \
       /path/to/worktree
   ```
   It:
   - Verifies the worktree exists and is on the expected branch.
   - Checks main-side uncommitted state. If dirty state is limited
     to `.threads/` and `.research/INDEX.json`, it offers the
     closure-session overlay path: stash the bookkeeping, merge the
     worktree branch, then pop the bookkeeping back afterward. If
     any non-bookkeeping files are dirty, it refuses the merge.
   - Checks for uncommitted state in the worktree (modified +
     untracked). If present, lists the files and asks the user
     whether to (a) abort and commit them on the worktree branch
     first, (b) copy uncommitted state into the merge, or (c)
     proceed merging only the committed history.
   - Prints the incoming diff summary:
     `git log --oneline main..<branch>` and
     `git diff --stat main...<branch>`.
   - **Asks `Proceed with merge? [y/N]`** — does NOT auto-merge.
   - On confirm: runs `git merge --no-ff <branch>` on `main`. The
     `--no-ff` preserves the codex-handoff lineage as a merge
     commit; the merge commit's body should describe what the
     thread accomplished (the script prompts you to write it).
   - If a main-side bookkeeping overlay was stashed, pops it after
     a successful merge. The restored edits are intentionally left
     uncommitted so the main session can update `merged_into`,
     `merged_at`, rerun the indexer, and commit the closure
     bookkeeping as a follow-up commit.
   - On `--copy-uncommitted-and-merge` (option (b) above): copies
     uncommitted files from the worktree into the main working
     tree first, then proceeds with the merge.
2. **If the merge conflicts on bookkeeping files, keep the merge
   commit focused on landing the worktree branch.** In the
   synth-tropo close, the worktree branch carried older
   `README.md`, `handoff.md`, `thread.json`, registry, and research
   index state while `main` had newer closure bookkeeping. The
   practical resolution was:
   - let the merge commit take the worktree branch's bookkeeping for
     those conflicted paths so the branch history lands cleanly;
   - continue the merge;
   - pop the stashed main-side bookkeeping overlay;
   - update `codex_worktrees[].merged_into` / `merged_at` with the
     actual merge commit; and
   - commit that restored closure state as the post-merge
     bookkeeping commit.

   If the script exited after a merge conflict and it had stashed
   bookkeeping, it prints the stash reference. Restore it only after
   `git merge --continue` or `git merge --abort`:
   ```bash
   git stash pop stash@{0}
   ```
3. **Run focused tests with the venv Python:**
   ```bash
   .venv/bin/python -m unittest <focused test modules>
   ```
   The system `python3` may have a SciPy/NumPy ABI mismatch on this
   host. Always go through `.venv/bin/python` for project imports.
4. **Path-substitution if the branch was based on stale layout.** If
   the worktree branch was cut very early in the thread and the
   `main` layout has since moved (e.g., the pre-`.threads/` move),
   the merged files may carry stale path references. Run:
   ```bash
   bash merge_codex_worktree_back.sh <worktree> --rewrite-paths
   ```
   It applies the project's known stale-layout sed substitutions
   (today: `gps_receiver/threads → .threads`). Extend the script's
   sed line if a future layout move adds another mapping.
5. **Update `thread.json`** to record the merge:
   - Set `codex_worktrees[<i>].status = "merged"`.
   - Set `merged_into` to the merge commit hash on `main`.
   - Set `merged_at` to today's ISO date.
   This is the audit-trail equivalent of `external_reviews[].merged_into`.
6. **Update the thread's `handoff.md`** with a new session-log entry
   at the top describing the close-and-merge transition. Refresh
   "Current state" and "Blockers / in flight" to reflect that
   nothing is in flight on the worktree any more. If any plan-hop
   `outcome` prose pointed at worktree-only handback artifacts,
   preserve that pointer and append the merge commit if needed; see
   `references/codex-handback.md`.
7. **Close the thread** via the standard threads-skill **Close thread**
   workflow (sets `thread.json.status = "closed"`, sets the active
   plan hop to `closed` with an outcome, etc.).
8. **Regenerate the registry** as the final step:
   ```bash
   python3 ~/.claude/skills/threads/scripts/index_threads_research.py
   ```
   Commit the regenerated `.threads/threads.json` and
   `.research/INDEX.json` together with the handoff edits.
9. **Clean up the worktree:**
   ```bash
   git worktree remove /path/to/worktree
   git branch -d <slug>
   ```
   `git worktree list` should no longer show it. `thread.json`
   keeps `codex_worktrees[<i>]` as a historical record (status:
   merged + commit hash) — the directory and branch are gone but
   the audit trail survives.

**Verification:**

- `git status` on `main` is clean after the merge commit.
- The focused test command from step 2 passes.
- `git worktree list` no longer shows the codex worktree.
- `thread.json.codex_worktrees[<i>].status == "merged"` and
  `merged_into` is a real commit on `main`
  (`git rev-parse <hash>` succeeds).
- The thread's `handoff.md` "Current state" section names the merge
  commit hash that landed the codex work.
- `thread.json.status == "closed"`.

## Troubleshooting

The three issues observed during the case-study run on
`.threads/receiver/20260427-chi-square-raim-design/` that still
apply under raw-TUI mode (codex-worktree session 2026-04-29
afternoon, commits `4ca46a8` source + `1f351dc` thread). Each has
a one-line root cause and a mitigation that the bootstrap and
prompt template now apply by default.

### 1. `.venv` tied to the main checkout

**Symptom:** worktree has no `.venv`. `python3` runs but project
imports fail with `AttributeError: _ARRAY_API not found` and
`numpy.core.multiarray failed to import` (SciPy/NumPy ABI
mismatch). Tests appear to "skip" silently when this isn't caught.

**Root cause:** the venv lives at `<main-repo>/.venv`. A new
worktree is a fresh dir with no Python environment.

**Mitigation (applied by bootstrap):** `ln -s ../<repo>/.venv .venv`
inside the worktree. Relative-path symlink so it survives the
worktree being moved alongside the main repo. The `.envrc` exports
`PYTHON=<worktree>/.venv/bin/python` so the agent has a single
canonical invocation. Always run project Python via that path,
not `python3`.

### 2. Worktree branched from a stale commit

**Symptom:** the worktree's working tree shows a layout (file
paths, directory names) that doesn't match `main`. Merge-back
fails or requires manual sed scrubs on prose references in JSON
specs to reconcile.

**Root cause:** the bootstrap was run against a stale local branch
or commit instead of current `origin/main`.

**Mitigation (applied by bootstrap):** the script always
`git fetch origin && git worktree add -b <slug> <path> origin/main`.
If the stale-layout case survives anyway (e.g., a layout move
landed *after* the worktree was cut), use the merge-back script's
`--rewrite-paths` flag for the known-symbol substitution.

### 3. Thread bookkeeping diverges between main and worktree

**Symptom:** the codex agent tries to update `thread.json` /
`handoff.md` but the worktree's `.threads/` layout differs from
main. Edits land in the wrong place.

**Root cause:** mixing source-code work and thread bookkeeping
inside the same isolated worktree.

**Mitigation (applied by prompt rule 1):** codex does
**source-only** work. Thread updates happen on `main` after each
codex run, by the user's main claude session. Rule 1 of the handoff
prompt enforces this with a "do NOT touch `.threads/`" instruction.

## Bookkeeping interactions with other workflows

- **New plan hop.** The thread's plan hops accrue on `main` per the
  normal **New plan hop** workflow. Each new hop's "What the next
  session should do first" content seeds the *next* codex prompt.
  The worktree branch absorbs the actual code work for whichever
  hops use codex.
- **Findings snapshot.** Write findings on `main` after each
  codex-produced result lands on the worktree branch, in the same
  cadence as ordinary findings (one per hop closure). The findings
  file references the worktree commit hash(es) the snapshot is
  measuring against.
- **Promote diagnostic.** Run on `main` only, against the *merged*
  version of the diagnostic (not the worktree-branch copy). Promote
  after merge-back, not before — `git mv` lineage breaks if the
  source path was on a still-unmerged branch.
- **External review (Codex/claude.ai/colleague).** The codex worktree
  pattern and the **Import external review** workflow are
  orthogonal. A codex worktree carries source code; an external
  review is a feedback artefact pasted verbatim into
  `external-comments/`. They can co-exist on the same thread.
- **Status review.** `index_threads_research.py` copies
  `thread.json.codex_worktrees[]` into each `threads.json`
  registry entry. `status_review.py` reads that registry copy and
  surfaces active worktrees in its AUTO-block. A `status: active`
  worktree on a `closed` or `superseded` thread is flagged as an
  orphaned worktree (cleanup needed).
- **Codex handback.** Every Codex hop should end with
  `codex-handoff/<plan-id>/handback.json` and `.md` on the worktree
  branch, plus any helper scripts, temp files, or curated evidence
  under the same inbox. The main session consumes those artifacts using
  `references/codex-handback.md` before closing the hop, starting a
  follow-on hop, or merging the worktree back.
- **Close thread.** The merge-back close step invokes the standard
  **Close thread** workflow. Ordering matters at commit granularity:
  the source merge lands first, then the closure bookkeeping lands
  as the follow-up commit. If the close workflow already produced
  uncommitted `.threads/` / `.research/INDEX.json` edits in the same
  session, stash that overlay before merge-back and pop it afterward
  instead of committing it ahead of the source merge. A closed thread
  with `codex_worktrees[<i>].status == "active"` is the
  orphan-worktree case the status-review flags.
