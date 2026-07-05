# Succession audit — checklist + rewrite protocol

Run this when a supervised session wraps (context-full), dies, or hands
you a "check my handoff" request — and on YOURSELF before ending an
orchestrator session. The failure mode it catches is universal: sessions
under context pressure write their freshest state into a NEW log entry
and never reconcile the OLD boot surface, leaving the two in
contradiction — with the cold-start reading order pointing at the stale
one.

## The audit

**1. Boot-surface vs wrap-entry consistency.**
Read the current-truth/state block AND the newest session-log entry.
List every claim in the boot surface that the wrap entry (or reality)
contradicts: things marked PENDING that are done, "NEXT" actions already
executed, gates described as blocked that have cleared, escalations
described as open that were ruled. Any hit means the boot surface is
stale.

**2. Label-collision scan.**
Find every ruling recorded as a label ("option (b)", "variant 2",
"plan A") and trace the label to its defining list. If two documents
enumerate the options in different orders — common when an escalation is
summarized twice — the label silently inverts. Rulings must be stated as
the words of the choice; flag and fix any that aren't.

**3. Claim verification (spot-check, cheap, high hit-rate).**
- "Tests pass" → re-run them, with the project's actual interpreter/venv
  (system python vs project venv is a classic false failure).
- "Generated artifacts regenerated" → re-run the generator, check git
  drift is zero. A generated file that differs from a fresh build means
  either a missed regen (fix: regenerate) or a hand-edit (worse —
  investigate).
- "Committed / landed" → look at the actual commit, its file list, and
  whether required companions (index regens, registry updates) rode
  along.
- Check the commit's claimed verification trailer against what you can
  reproduce.

**4. In-flight losses.**
Ask what was running when the session wrapped: recons, background tasks,
subagents. Anything not processed into a committed artifact is LOST and
must be re-commissioned explicitly — otherwise it silently drops out of
the program.

**5. Reading-order check.**
Does the workspace's cold-start reading order actually reach the freshest
truth? If the wrap entry is the real state but the reading order stops at
the stale block, a successor boots wrong even though the information
exists.

**6. Open questions faithfully carried?**
A good wrap entry carries its uncertainties forward ("B1 emitter = TBD",
"banking location unresolved") rather than papering over them. Missing
uncertainty is a red flag: compare the wrap entry's confidence against
the session's last real evidence.

## The succession rewrite

When the boot surface is stale and its owner is dead, someone must fix it
or the next session inherits the contradiction. Overwriting another
session's state block breaks the append-only convention, so:

1. **Get authorization** — it's the user's call (offer the alternative:
   leave the block, route successors around it via the launch prompt;
   strictly cleaner on convention, but every future cold read stays
   wrong).
2. **Rewrite from the wrap entry**, not from memory: the block becomes
   the wrap-entry state plus any rulings that landed after it.
3. **Lead with an attribution marker**: who rewrote, when, why (owner
   wrapped without reconciling; user authorized), and where the
   superseded content lives (git — never delete history, never keep a
   stale copy inline for "reference").
4. **Fix the header/date metadata** so the file self-describes as
   current.
5. **Defuse the traps you found** in the audit — restate letter-ruled
   decisions as words, mark re-commissioned recons, carry the open
   questions.
6. **Commit with the story in the message**: what was stale, why the
   rewrite was authorized, what a reader of the old block would have
   gotten wrong.
7. **Bank the successor launch prompt** in the same pass (see
   `launch-prompt-template.md`) and update the orchestrator cache's
   live-workers table to point at both.

## Self-audit (orchestrator ending its own session)

Same standard, applied inward — then two additions:

- **Chat-only artifact sweep.** Launch prompts, rulings, escalation
  drafts, and analysis that exist only in the conversation get banked as
  files now or lost.
- **Watch items.** End the dated session narrative with the specific
  things the next orchestrator must verify or expect: unverified
  transitions ("close authorized — confirm it executed"), expected
  escalations, pending pushes/syncs, known coverage gaps. Watch items
  encode your unfinished suspicions — they are the part of your judgment
  that would otherwise die with the session.
