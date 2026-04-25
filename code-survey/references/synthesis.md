# Synthesis — combine lens outputs into a recommendation list

The synthesis step is what makes the multi-lens approach worth
running. The lenses are pattern-matchers; the synthesis is where
cross-cutting judgment lives — and where Opus-grade reasoning
earns its keep.

This step is **always main-session work**, never a sub-agent.
Sub-agents lack the cross-pass visibility synthesis needs, and
delegating it dilutes the value.

## Inputs

- All `pass-N-<lens>/agent-*.md` files in the current run directory.
- The project config snapshot at `config.snapshot.json`.
- The scope at `scope.json`.

## Outputs

- `synthesis.md` — wrap-up MD, the durable artifact.
- `synthesis.json` — same data, machine-readable, for /threads ingest.

## Procedure (follow in order)

### Step 1: Gather all findings

Read every per-agent report. For each finding extract:

- `lens` — which lens produced it.
- `file` — what file or files it's about.
- `lines` — line range if specified.
- `verdict` — the lens's verdict vocab (SPLIT / KEEP / REFACTOR / EXACT / etc.).
- `description` — one-sentence summary.
- `recommendation` — the proposed action.

Hold them in a working set; nothing on disk yet.

### Step 2: Cross-pass reinforcement check

For each finding, check if the same file or symbol appears in
*another* lens's findings. Two cases:

- **Reinforced** — same file/symbol, complementary verdicts. E.g.,
  duplicate-helper flags two parity functions in different files,
  AND naming-drift flags those same two functions under different
  names. → high confidence, escalate.
- **Contradicted** — same file/symbol, opposing verdicts. E.g.,
  file-monolith flags `X` as SPLIT, naming-drift flags `X` as a
  consolidate target (which would be the opposite direction). → ask
  the user; lower the priority of both findings until resolved.

Reinforced findings get a `confidence: high` tag; standalone get
`confidence: medium`; contradicted get `confidence: review`.

### Step 3: Physics-floor filter

For every finding that uses words like "precision," "numerical,"
"residual," "convergence" — apply the project-config `physics_floor`
check. Ask:

> Is the residual this finding describes above the project's
> physics floor?

If no, demote to the **filtered-out** appendix with reason:
"physics-floor filter: residual is sub-`<floor>`, below the smallest
physical term modeled."

This is the most important guardrail. Without it, sub-mm
numerical-convergence detail gets escalated to "P1 precision bug,"
which wastes user attention and erodes trust in the synthesis.

The classic false positive: a fixed-point iteration count that
converges quadratically. Iter count differs between two
implementations, but both produce sub-floor residuals. Not a bug.
File this kind of finding under "intentional duplication" or
"naming drift," not "precision."

### Step 4: Risk classification

For each remaining finding, match its `recommendation` against the
config's `risk_classifier` lists:

- If it matches a **low** entry → `risk: low`.
- If it matches a **high** entry → `risk: high`.
- Otherwise → `risk: medium`.

If a finding's recommendation touches a `boundaries[]` glob, it's
**always** `risk: high` regardless of category — because crossing
a sacred boundary is the highest-cost change.

### Step 5: Priority rank

Bucket findings into priorities:

- **P1** — *bugs.* Numerical drift in constants (different value for
  the "same" thing). Behavioral regressions. Things that are wrong
  *now*, not just sub-optimal.
- **P2** — *real duplicates with reinforcement.* Confirmed by ≥2
  lenses (typically duplicate-helper + naming-drift). Worth doing
  even though the code "works."
- **P3** — *clarity refactors.* Long methods, in-place
  refactor-in-place candidates, file-level monoliths. Reduce cognitive
  load.
- **P4** — *cosmetic.* Comment-debt, accidentally-private/public,
  small naming inconsistencies.

Order P1 > P2 > P3 > P4 within each risk band; combine into the
final recommendation list.

### Step 6: Verification policy

Partition the recommendation list:

- **Low-risk** items: batch under one shared E2E gate at phase end.
- **Medium-risk** items: unit tests during the change; consider
  bundling 2-3 under a shared E2E gate if they're independent.
- **High-risk** items: per-item E2E gate. No batching.

Surface the partitioning explicitly in the wrap-up MD, with the
e2e_baseline command from config inline. The user shouldn't have
to look up how to verify.

### Step 7: Thread-worthy check

Apply config's `thread_worthy_threshold` rules:

- ≥`min_items` total findings (default 6) → thread-worthy.
- Any high-risk-with-verification → thread-worthy.
- Multi-day estimate (sum of effort estimates >1 session) → thread-worthy.

If thread-worthy: end the synthesis with a "Next: /code-survey
propose-thread" pointer. If not: end with "small enough for
direct commits; here's the suggested order."

### Step 8: Write the artifacts

Use `assets/templates/synthesis.md` and
`assets/templates/synthesis.json` as scaffolds. Substitute the
findings, fill in the cross-pass section, the per-lens highlights,
the recommendation table, the verification policy block, and the
filtered-out appendix.

Both files land in the run directory.

## What goes in the filtered-out appendix

The wrap-up MD ends with a "Did NOT surface" section. Anything
filtered out by the physics-floor check, by a `keep_rules` match,
by a `boundaries` rule, or by user-specified out-of-scope wording
gets one line each:

```
- <file>: <finding> — filtered: <reason>
```

This makes the filtering legible. A future user looking at the
wrap-up sees what was considered AND why it was set aside,
without having to dig through 8 per-agent reports.

## Re-synthesis

The user may run `/code-survey synthesize` again after holding,
skipping, or accepting individual findings. Re-synthesis re-reads
the same per-agent reports but applies the user's
hold/skip/accept decisions on top.

To support this, the synthesis state lives in a sidecar
`decisions.json`:

```json
{
  "decisions": [
    {"finding_id": "F1", "action": "accept"},
    {"finding_id": "F2", "action": "hold", "reason": "wait for thread X"},
    {"finding_id": "F3", "action": "skip", "reason": "false positive on review"}
  ]
}
```

Re-synthesis updates the wrap-up MD to reflect held/skipped items
in a separate section, and excludes them from the active
recommendation table.

## What synthesis is NOT

- **Not a sub-agent task.** The cross-pass reasoning is the value;
  delegating it loses the point.
- **Not a deterministic algorithm.** Risk classification needs
  judgment; "is this duplicate intentional?" needs judgment;
  bucketing P1/P2/P3/P4 needs judgment. Encode the principles, not
  rigid rules.
- **Not a final verdict.** The synthesis surfaces a recommendation
  list; the user accepts/holds/skips. The synthesis serves the
  user's decision, not its own coherence.
