# Agent prompt: comment-debt lens

You are doing a comment-debt scan. Your job is to find stale
TODOs, FIXMEs, "removed once X" comments, and references to
issues/PRs that no longer exist.

**Project context:**
{{PROJECT_CONTEXT}}

**Files to analyze:**
{{FILES}}

**Search patterns:**

```
TODO
FIXME
XXX
HACK
removed once
remove once
see issue #
see PR #
deprecated:
DEPRECATED
```

For each match, evaluate:

1. **Is the referenced thing still relevant?**
   - Does the named issue/PR exist in the issue tracker? (If no
     access, note as `UNCLEAR`.)
   - Does the named code path / function / file still exist?
   - Has the "until X" condition been met? (E.g., "remove once
     v2 lands" — has v2 landed?)
2. **Is the marker still informative?** A TODO from 2019 saying
   "improve performance" with no specifics is comment-debt. A TODO
   from last week saying "switch to lib X once it ships v2.0" is
   actionable.

**Categorize each finding:**

- `STALE` — the referenced issue is closed / the referenced code
  is gone / the condition has been met. Action: remove the comment
  (and the dead code path it gates, if any).
- `OPEN` — still relevant. No action.
- `UNCLEAR` — needs human review (no issue tracker access, or the
  reference is ambiguous). Surface for the user.
- `VAGUE` — old TODO with no specifics. Either flesh out or remove.

**Encoded biases:**
- **Don't flag deferred-but-tracked items.** A TODO that includes
  a clear conditional ("when X is available") is open work, not
  debt — even if X hasn't happened.
- **Don't flag deprecated API markers** that are doing their job
  (warning users away). Those are documentation, not debt.
- **Comments can outlive their original author's intent.** If you
  can't tell whether a TODO is stale, mark UNCLEAR rather than
  STALE. The cost of removing a still-relevant comment > the cost
  of leaving a stale one for one more cycle.

**Project-specific anti-patterns:**
{{EXTRA_ANTI_PATTERNS}}

**Hard constraints:**
- Don't recommend removing comments that document non-obvious
  behavior, even if labeled TODO.
- Don't recommend removing comments that explain a workaround for
  a specific upstream bug, unless the bug is fixed AND the
  workaround is also removed.

**Report format (under 400 words):**

| File | Line | Marker | Reference | Category | Action |
|---|---|---|---|---|---|
| `<path>` | `<N>` | `TODO` | issue #123 | STALE | remove |
| `<path>` | `<N>` | `FIXME` | (no ref) | VAGUE | flesh out or remove |

Group by category. Lead with STALE (clear-cut removals).

Skip OPEN entries unless you ran out of findings to report.
