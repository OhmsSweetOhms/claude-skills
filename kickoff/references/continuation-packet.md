# Continuation packet — authoring guide (standalone / non-threads projects)

A continuation packet kicks off the NEXT stage of work in a project that
has no threads workspace and no orchestrator machinery — typically a
dedicated worktree with a handoff document as its only succession surface.
The ask usually sounds like "kick off a continuance of the work in this
worktree; the handoff is <file>". The producer input is the handoff; the
product is a stage execution plan the next session (or this one) executes
against.

Do not import the threads machinery here — no Fire Card, no handback JSON,
no worktree emission scripts. The universal invariants still bind; this
format is how they land without the ecosystem.

**If the handoff embeds its own cold-start prompt, that IS the boot
path.** Consume it directly — do not author a new kickoff to wrap a
kickoff the handoff already carries. The producer work this skill owns
begins with the NEXT artifact the session must emit: the stage execution
plan below, and at wrap, the refreshed cold-start prompt inside the
updated handoff.

## The shape

One plan file, banked next to the handoff it derives from, named for the
packet/stage it executes (`<packet>-stage<N>-plan.md`). Sections in order:

1. **Header — provenance and execution mode.** What packet/stage this
   executes, which committed spec/packet doc it derives from, and the
   handoff state it was drafted from — anchored by commit SHA, not prose
   ("Drafted from the handoff state at `<sha>`"). State the execution
   mode explicitly; the strong default is **fail-closed**: the first miss
   at any step stops the run and is reported, not worked around.
2. **Environment pins.** Tool versions and working directory, once, up
   front ("All tools are X. All commands run from Y unless stated").
   Every command in the plan then inherits them.
3. **Steps table.** One row per step: action, pass criterion. The pass
   criterion is the falsifiable-acceptance invariant in table form —
   every row names what an independent checker would verify, not "done".
4. **Per-step detail sections.** Only where the table row can't carry it.
   Put KNOWN BLOCKERS first and label them as what they are (a latent
   defect to fix at step 1 beats a mid-run surprise at step 5). Where a
   check could pass vacuously, say what real verification looks like
   ("verify the regenerated wrapper text reads 8 — not just the build
   exit code"). Long-running steps state their handling (background +
   poll; never truncate a run to save time).
5. **Fences held throughout.** The scope fence as a standing section, not
   per-step fine print: frozen files ("if it appears to need a change:
   stop and report, do not edit"), forbidden operations (hardware,
   destructive targets), out-of-scope work that needs separate operator
   authorization, vendored dirs, preserved untracked paths, and the
   sanitization rule (no absolute paths or usernames in tracked files or
   commit messages).
6. **Bank-and-document step (last table row, expanded at the end).** The
   wrap is part of the plan: evidence report with numbers, handoff
   updates (its remaining-work list and cold-start prompt), commit
   discipline (design-note messages with `Verification:` trailers,
   explicit-path staging, pre-commit scans), and the push posture
   (usually: do not push — operator-fired).

## Authoring rules

- **The handoff is the source of truth; the plan is its executable
  projection.** Every number, SHA, and expected delta in the plan traces
  to the handoff or a committed spec — never re-derived. If the handoff
  doesn't settle something the plan needs, that's a question for the
  operator before the plan is banked, not a guess inside it.
- **Anchor state by SHA, then leave it alone.** A dated plan pinned to a
  commit doesn't rot the way status prose does — the pin IS the freshness
  contract. If the tree moves past the pin before execution, the plan is
  re-validated against the new state, not silently reused.
- **Escalation here is fail-closed, not a mailbox.** With no orchestrator
  to bounce to mid-run, the stop conditions must be in the plan itself:
  the fences say stop-and-report, the execution mode says first-miss
  halts. Anything discovered that the fences don't cover gets reported at
  the halt, never absorbed.
- **The plan updates the handoff, not the reverse.** Closing the loop is
  a plan step: the wrap edits the handoff's remaining-work and cold-start
  sections so the NEXT continuance starts from truth. A plan that
  executes without updating its succession surface strands the successor.
