The handback artifacts (`codex-handoff/<plan-id>/handback.json` and
`codex-handoff/<plan-id>/handback.md`) are the ONLY record the main
session will see of this session. Chat output ends when this session
ends; nothing in the transcript survives unless you commit it to a
file the main session reads.

Four things you MUST record before terminating the session:

**1. Plan execution as closure record.** Gate verdicts, anchor
commits, engineering deliverables, regression baseline, diff stat.
Required even when the plan closes cleanly. Mandatory fields per
the JSON schema: `gates[]`, `commits[]`, `worktree.diff_stat`,
`status`. The closure record is the bookkeeping anchor for
`thread.json::plan_hops[].outcome`.

`status` records Codex's progress/outcome axis:
`complete | gate-incomplete | blocked | scope-cut`. It is not the
thread lifecycle enum. If the main session has already closed or
superseded the plan, record that separate lifecycle state in
`closure_status` (`active | blocked | superseded | closed`). For
normal forward handbacks, omit `closure_status` until the main
session makes the closure decision.

Each `gates[]` entry should include `caveats: []` when there are no
caveats. If a gate passes only because of local state, an
untracked fixture, a branch-only artifact, an environment quirk, or
another contingency that affects future validity, portability, or
reproducibility, record it in that gate's `caveats[]`. Do not emit
`status: complete` with that contingency only as a discovery
footnote. If the caveat must be resolved before merge-back, also
add a `blockers[]` entry or a `follow_ons[]` entry with routing
that makes the pre-merge action explicit.

**2. Mid-session inquiries as `investigations[]`.** When the user
asks you to produce a theory, prove a hypothesis, run a diagnostic
beyond the plan's deliverables, or assess an adjacent thread,
append a structured entry to `handback.investigations[]` with:

- `triggered_by`: their prompt verbatim or paraphrased ≤ 1 line
- `question`: the inquiry restated as a question
- `answer`: your finding, ≤ 5 sentences for prose; numbers go in
  `evidence.key_numbers`
- `evidence`: a primary path or `commit:<sha>`, plus `key_numbers`
  for any quantitative result, plus `calculation` for any
  derivation worth preserving
- `code_paths_anchored_in`: file:symbol references for the code
  that produced or verified the answer

The bar is "did this inquiry produce a finding worth preserving?"
Routine clarifications (which test to run, where the venv lives)
are not investigations and should not be recorded.

**3. Spontaneous flags as `discoveries[]`.** When you notice
something while doing the plan work that the next codex hop should
know — a column type contract, a fixture pitfall, a constraint
that surprised you — append a `discoveries[]` entry. These are
unprompted; investigations are prompted. The split is the audit
trail: `triggered_by` distinguishes them.

**4. Session-created helper material as `handoff_artifacts[]`.** Any
script, debug test, generated file, log, plot, reduced table, or other
helper artifact you create for the session must live under the
root-level `codex-handoff/<plan-id>/` inbox, not under `.threads/`.
Use:

- `scripts/` for throwaway probes, debug tests, and helper scripts
- `temp/` for bulky or disposable generated working files
- `artifacts/` for curated evidence cited by the handback

Record each durable or potentially useful file in
`handoff_artifacts[]` with its path, kind, status, and promotion
recommendation. If the file is disposable scratch, either omit it or
mark the recommendation as `discard`. The main session promotes
selected material into `.threads/`, permanent tests, or tracked data.

**Calibration.** If you find yourself answering something
substantive in chat without writing it down, stop and ask: which
of the four buckets does this belong in? If none, it's not
substantive. If one, write it before continuing the dialogue —
otherwise it will be lost when the session ends, and the main
session will have no way to recover it.
