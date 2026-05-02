# threads/

This directory holds **debug investigations** — one subfolder per
investigation ("thread"). Plans, diagnostic scripts, captured data,
and external review notes all live together in the same folder, so
a future session (yours, a future Claude, or a colleague) can pick
up cold without hunting through the repo.

## Where things are

```
threads/
  README.md           ← you are here (how to use this directory)
  CONVENTIONS.md      ← formal rules, JSON schemas, file templates
  threads.json        ← machine-readable index of all threads + promotion log
{{SUBSYSTEMS}}
```

Each thread lives at `<subsystem>/<YYYYMMDD>-<slug>/` and looks
like:

```
20260414-example-investigation/
  README.md                      ← thread status, current plan, next step
  thread.json                    ← manifest (schema in CONVENTIONS.md)
  plan-01-<slug>.md              ← ordered plan hops
  plan-02-<slug>.md
  findings-<YYYY-MM-DD>.md       ← written at decision points
  diagnostics/diagnose_*.py      ← scripts (tracked)
  temp/                          ← regeneratable outputs (gitignored)
    README.md                    ← regeneration commands (tracked)
  external-comments/             ← Codex / claude.ai / colleague input
  data/                          ← optional; committed captures and fixtures
```

## Starting a new thread

1. Pick a subsystem folder. If none fits, add a new one — just
   `mkdir` the subsystem dir.
2. Create `<subsystem>/<YYYYMMDD>-<slug>/` — `YYYYMMDD` is today,
   `<slug>` is a short human-readable description.
3. Copy the thread skeleton from `CONVENTIONS.md` into it.
4. Write your first plan as `plan-01-<slug>.md`. The slug describes
   this hop's focus, not the thread overall.
5. Fill in `thread.json` — at minimum: `id`, `title`,
   `status: "active"`, `started`, `current_plan`.
6. Add a row to `threads.json`.

The `/threads` skill (if installed) automates these steps.

## Working inside a thread

- **New plan hop** (when the current plan resolves or gets refuted
  and the next step deserves its own plan): write
  `plan-NN-<next-slug>.md`. In `thread.json.plan_hops[]`: mark the
  old hop `closed` or `superseded`; add the new one as `active`;
  update `thread.json.current_plan`.
- **Diagnostic scripts**: any `diagnose_*.py` goes in
  `diagnostics/`, not at repo root. Add an entry to
  `thread.json.diagnostics[]` with the plan hop it belongs to and a
  one-line purpose.
- **Generated outputs**: CSVs, NPZ, pickles, plots → `temp/`. This
  directory is gitignored — contents never get committed. Instead,
  maintain a tracked `temp/README.md` that lists each expected
  output and the command to regenerate it. If an output cannot be
  regenerated (hardware trace, one-off recording), or if a
  committed test/gate depends on a specific snapshot, put it in
  `data/` instead and commit the bytes.
- **Findings**: write `findings-<YYYY-MM-DD>.md` when a plan hop
  closes or a decision point is reached. Never overwrite — new
  snapshot = new file.

## Handing off to Codex, claude.ai, or a colleague

When an outside reviewer sends comments or edits back:

1. Paste their response **verbatim** into
   `external-comments/<YYYYMMDD>-<source>-<subject>.md`. Sources:
   `codex`, `claude-ai`, `colleague-<name>`.
2. Fill in the frontmatter (`source`, `date`, `subject`, `kind`,
   `disposition: pending`). `kind` is `comment` (prose feedback),
   `edit` (they rewrote parts), or `mixed`.
3. Add a triage table — one row per point. Decide accept / reject /
   defer.
4. For accepted points: edit the plan and **commit** the change.
   Once committed, update the external-comment file with
   `disposition: merged` AND set `merged_into` to the commit hash.
   A review can only be marked `merged` once every accepted point
   has a commit hash.
5. For rejected: `disposition: rejected` with rationale in "Merge
   notes".
6. Add an entry to `thread.json.external_reviews[]`.
7. **Never edit the raw content section.** It's the attribution
   record.

## Promoting a diagnostic to a permanent test

When a `diagnose_*.py` proves valuable enough to run forever as a
regression gate:

1. `git mv diagnostics/diagnose_X.py <tests-dir>/test_X_regression.py`.
2. Refactor: remove the CLI, wrap assertions in the project's test
   framework idiom, keep shared helpers callable from other thread
   diagnostics.
3. Add rows to `thread.json.promotions[]` and
   `threads.json.promotion_log[]`.
4. Add a row to this thread's `README.md` under "Promoted
   artifacts".

`git mv` (not copy) preserves `git log --follow` back through the
thread.

## Closing a thread

1. Write a final `findings-<YYYY-MM-DD>.md`.
2. Set `thread.json.status` to `closed` (investigation complete),
   `superseded` (a successor thread took over), or `blocked`
   (external input needed — not closed).
3. Update the status in `threads.json`.
4. Leave the directory in place. It's the permanent record.

## See also

- `CONVENTIONS.md` — exact JSON schemas, status vocabularies, file
  templates, naming rules.
- `threads.json` — machine-readable index; the promotion log lives
  here.
- Each `<thread>/README.md` — the per-thread landing page.
