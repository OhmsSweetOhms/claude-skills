The handback artifacts (`codex-handback-<plan-id>.json` and
`codex-handback-<plan-id>.md`) are the ONLY record the main session
will see of this session. Chat output ends when this session ends;
nothing in the transcript survives unless you commit it to a file
the main session reads.

Three things you MUST record before terminating the session:

**1. Plan execution as closure record.** Gate verdicts, anchor
commits, engineering deliverables, regression baseline, diff stat.
Required even when the plan closes cleanly. Mandatory fields per
the JSON schema: `gates[]`, `commits[]`, `worktree.diff_stat`,
`status`. The closure record is the bookkeeping anchor for
`thread.json::plan_hops[].outcome`.

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

**Calibration.** If you find yourself answering something
substantive in chat without writing it down, stop and ask: which
of the three buckets does this belong in? If none, it's not
substantive. If one, write it before continuing the dialogue —
otherwise it will be lost when the session ends, and the main
session will have no way to recover it.
