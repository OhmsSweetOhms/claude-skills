---
name: orchestrator-handoff
description: Durable succession for multi-session programs and worker, coordinator or orchestrator sessions. Use to boot cold, resume, wrap near a context limit, create or update a resume cache, audit a worker handoff, repair a dead session's stale boot surface with authorization, or write successor launch prompts. Triggers on ORCHESTRATOR-CACHE, SESSION-HANDOFF, context-full wrap-ups, session succession, check the handoff, and coordination state that must survive without the transcript. Not for single-session task tracking or sprint boards. Also triggers on "running out of context" and "is my handoff durable".
---

# Orchestrator Handoff

Multi-session programs die in the gaps between sessions. A worker wraps at
its context limit, an orchestrator restarts cold, and the next session
boots from whatever was written down — which is usually a mix of stale
status, chat-only decisions, and a wrap-up note nobody's reading order
points at. This skill is the discipline that closes those gaps.

The one-sentence model: **decisions are durable, status rots, and every
artifact a successor needs must exist as a committed file — never only in
chat.**

If the project uses a threads-style workspace (`.threads/` dirs with
`thread.json`/`handoff.md` and an orchestration conventions file), this
skill layers ON TOP of those conventions — follow the project's canon for
thread-dir mechanics (attributed notes, record discipline, staging rules)
and use this skill for the orchestrator-session lifecycle around them.

## The four durable artifacts

An orchestrator's succession surface is exactly four things. Everything
else is transcript, and transcripts don't survive.

1. **The resume cache** (e.g. `ORCHESTRATOR-CACHE.md`) — the ONLY file a
   cold orchestrator reads first. Pointers + decisions-in-force, never
   live status. Template: `references/cache-template.md`.
2. **Dated immutable session narratives** (e.g.
   `SESSION-HANDOFF-<date>-<slug>.md`) — one per orchestrator session,
   written at wrap, never edited after, carrying the pinned banner
   (`references/wrap-protocol.md` step 4). History only and on no boot
   path; the cache and the Current-truth blocks must be sufficient without
   them.
3. **Banked launch prompts** — every cold-boot prompt for a worker or
   successor session lives as a committed file, not only in chat.
   Template: `references/launch-prompt-template.md`.
4. **Worker boot surfaces** — each worker's own current-truth block. The
   orchestrator doesn't own these but is responsible for AUDITING them at
   succession points, because a successor boots from them.
   Checklist: `references/succession-audit.md`. A lane orchestrator's OWN
   boot surface is the Current truth of its charter thread, which its
   launch prompt names; it overwrites that block at wrap.

## Core rules (each earned the hard way)

**Decisions land in the same commit as the change they reflect.** A
ruling made in chat and cached "later" is a ruling that a concurrent
session or a crash can orphan. When you decide something, the cache edit
rides the same commit as the note/change that implements it.

**No status lines in durable surfaces.** "Pass 1 running", "X is
blocked", "session in flight" — all of it rots within hours and then
actively misleads the next reader, who trusts it. Status lives in worker
handoffs and registries that are POLLED at boot; the cache stores only
what to poll. If you catch yourself writing "currently" into the cache,
stop.

**A decision still being litigated by a live worker is status, not a
decision.** The "same commit" rule only protects surfaces the deciding
session OWNS — when a worker re-rules something mid-exploration, it
updates its own handoff and has no reason to know the cache holds a
verbatim copy. So don't mirror a live thread's evolving rulings into the
cache: cache the pointer to the owning surface until the thread stops
churning them, then ink the settled version. If a ruling genuinely must
be duplicated cross-surface while still live (the whole program depends
on it), name the owning surface in the entry so auditors know what to
diff it against. (Earned 2026-07-07: a cache kept a trade ruling its
owning thread had refuted and re-ruled within hours — a cold orchestrator
would have inherited the inverted ruling.)

**Cite decisions by content, never by labels that can collide.** If two
documents enumerate options (a)/(b)/(c) in different orders — and they
will — a ruling recorded as "option (b)" silently inverts. Record rulings
as the words of the choice ("arrays", "8-bit", "custom polyphase"), with
the label at most parenthetical. Same reason coined shorthand that greps
into real identifiers is banned.

**Distinguish the decision ladder explicitly.** Three rungs: decisions
reserved for the USER (record which ones — e.g. release/freeze calls,
cross-cutting architecture changes), decisions in ORCHESTRATOR remit
(rule them, cache them, note them attributed), and decisions in WORKER
remit (let workers make them; audit afterward). When a worker escalates
something in your remit, rule it; when it's the user's, bring it to them
DRAFTED with options and a recommendation, never decided. Write the
boundary into the cache so successors inherit it instead of re-deriving
it.

**Non-owner writes are attributed and append-only** — an ORCHESTRATOR
NOTE entry in the worker's log, never a silent edit of their state. The
single exception is the succession rewrite (below), which requires the
owner to be dead and the user to authorize it.

**Verify worker claims independently before accepting them.** "Tests
pass" → re-run them (with the project's actual interpreter/venv).
"Generated docs regenerated" → re-run the generator and check for drift.
"Committed" → look at the commit. This is cheap, and roughly one audit in
two finds something — a stale generated artifact, a missed step, a claim
that was aspirational. Accept-with-verification is what makes delegation
safe.

**Two strikes on a manual discipline → make it mechanical.** If the same
written directive gets missed twice (a regen step, a check, a format
rule), the discipline is wrong, not the person. Stop re-policing it in
prose; wire it into a hook, a CI check, or a script — opt-in-by-detection
so it no-ops everywhere it doesn't apply — and record in the cache that
enforcement moved from prose to mechanism.

**One expensive resource, one holder.** Whatever the machine-wide scarce
thing is (a heavy replay, a GPU, a build box), the cache names the rule
and launch prompts remind workers to re-check the slot before taking it.

**Sanitize before banking.** Launch prompts and notes written in chat
casually contain absolute paths, usernames, and machine-local details.
Before committing them as files, replace with placeholders
(`$WORKBASE/...`, "the main checkout") — both for privacy guards and
because successors may run from different checkouts.

## Boot protocol (cold orchestrator)

1. Read the resume cache. Do NOT bulk-read anything else yet.
2. Poll live status: the project's registry/index command, then the
   current-truth block of each live worker handoff the cache lists. That
   is the whole live picture.
3. Delegate any deeper read (kickoffs, findings, research, ADRs) to a
   cheap read-only subagent with a SPECIFIC question; consume the
   conclusion. Inline-read only single facts at known locations. The
   orchestrator's context is the program's working memory — don't spend
   it on file dumps.
4. Treat cached decisions as settled. Re-litigate only with new
   evidence, and say so when you do.

## Succession events (the moments this skill exists for)

### You (any session) are running out of context

**Open `references/wrap-protocol.md` and follow it in full — do not wrap
from this summary.** (A session that wrapped from an earlier, shorter
summary skipped four steps.) Its steps, in order:

1. Overwrite your boot surface FIRST — the Current truth of the thread you
   own, or your charter thread's if you are a lane orchestrator; never a
   cache. Watch items go here.
2. Capture in-flight work, or write the explicit re-commission line.
3. State what of yours is uncommitted.
4. Write the narrative — history only, the pinned banner on a
   `SESSION-HANDOFF` file, nothing forward-looking.
5. Leave the successor pointer (boot surface, active plan, launch prompt).
6. Put session-earned facts in the hop's findings (they reach skill
   chapters at hop close) — never in a file every session reads at boot.
6a. Reconcile every work-order surface your boot protocol points at.
6b. Walk the cache's one-line decision index, every wrap: retire the lines a
   later decision replaced whole or whose one-time work is merged, offer the
   operator the ones an ADR or spec now covers, keep the rest — reading the
   full registry entry, never the one-liner alone.
7. Commit, explicit paths.
8. End with the USER's next moves, copy-paste-ready with absolute paths.

The order is survival-ranked — if you die mid-wrap, the boot surface is
right and only the narrative is missing, which is the recoverable failure.
This applies to workers and coordinators as much as orchestrators; it is
the other half of the audit below, and doing it means the audit finds
nothing.

### A worker session wraps or dies

Audit its boot surface before anyone resumes from it — checklist in
`references/succession-audit.md`. The classic failure: the dying session
writes an accurate wrap-up entry in its log but never overwrites its
current-truth block, and the cold-start reading order points at the
stale block, not the wrap entry. Check for internal contradiction
between the two, stale "NEXT" items that already happened, and label
collisions with rulings made elsewhere.

If the surface is stale and the owner is dead: propose a **succession
rewrite** to the user (their call — it breaks append-only). On approval,
overwrite the current-truth block with the wrap-entry state, lead the
block with an attribution marker (who rewrote, when, why, where the
superseded content lives — git), fix the header date, commit with the
reasoning in the message.

Then bank a successor launch prompt (template in references) so the next
session boots deterministically instead of from a verbal summary.

### The orchestrator itself hands off

Run the self-audit — the same standard applied to workers, on yourself:

- Every ruling made this session is in the cache (grep your own session
  for "ruled/decided/declared" if unsure).
- The live-workers table matches reality: rows for finished workers carry
  a verify flag ("close authorized — VERIFY it executed") rather than an
  assumption; role text contains no status that will rot.
- Everything that exists only in chat is banked: launch prompts, rulings,
  escalation drafts.
- **Reconcile every surface your own boot protocol points at** — the
  work-order document, the playbook, the roadmap, whatever a cold
  successor is told to read *after* the cache. The cache being correct is
  not enough: a successor that boots exactly as instructed still lands on
  those files, and if they still announce as "next to author" work this
  session finished, the correct boot produces the wrong next move. Walk
  your own resume protocol's reading list and ask of each file, "is this
  still true after today?" Move completed work into the done/history
  section **with its measured results and, crucially, whatever it left
  open**, and put the successors in its place marked drafted-and-unfired
  so nobody mistakes a draft for a launch. (Earned 2026-09-09: a lane
  cache was updated correctly all session while the playbook it pointed
  at still listed a capability as un-started that two closed packets had
  built and repaired that same day.)
- Put your **watch items** in your boot surface — your charter thread's
  Current truth (its OPEN and NEXT bullets) — not in the narrative: the
  specific things the next orchestrator must verify or expect (an
  unexecuted close, an expected escalation, a pending push). Watch items
  are the highest-value part of a handoff; they encode your unfinished
  suspicions, which is exactly why they belong on the surface a successor
  boots from, and not in a narrative that is on no boot path.
- Write the dated session narrative: what happened, what was decided and
  done (with commit hashes), what the session got wrong, evidence
  pointers. History only, with the pinned banner (`references/wrap-protocol.md`
  step 4).
- Put session-earned facts — mechanisms proven, approaches refuted,
  diagnosis patterns, anything a future agent would re-derive at
  board/build cost — into the hop's findings; they reach the owning skill
  reference chapter at hop close (threads **Promote facts**). The project
  `CLAUDE.md` gets at most a one-line pointer. Scrub claims the session
  refuted.
- Commit everything, explicit paths, in as few commits as coherent.
  On shared checkouts, check `git diff --cached --name-only` first — a
  concurrent session's staged work can race into your commit; unstage
  theirs, commit yours, restage theirs.
- **End with the USER's next moves, copy-paste-ready with ABSOLUTE
  paths.** The wrap's final chat message lists the operator's reserved
  actions in order (merges to fire, sessions to launch, packets to
  emit), each with the absolute paths and commands they need — session
  CWDs, launch-prompt files to paste, worktrees, gate scripts.
  Absolute paths are correct here and only here: chat guidance for the
  human at their own terminal, never a tracked file. Sanitization
  applies to what gets committed, not to what gets handed to the
  operator.

The test of a good handoff: a successor booting from the cache alone
makes the same next three moves you would have made — and the USER
knowing, without asking, which buttons are theirs to press.

## Launch prompts

Every worker and successor gets a banked cold-launch prompt. The shape
that works (full template with rationale in
`references/launch-prompt-template.md`):

1. **Role + reporting line** — who they are, who they report to, and
   which decisions are already made and NOT theirs to reopen.
2. **Boot sequence** — exactly which files to read, in order, and an
   explicit "do not bulk-read anything else first".
3. **Active task** — priority #1, with the mechanics already reconned
   (branch names, entry points, gotchas) so the session spends context
   on work, not rediscovery.
4. **The queue** — what comes after, in order, so the session doesn't
   stall when task #1 finishes mid-session.
5. **Boundaries** — escalation rules, reserved decisions, shared-resource
   rules, disciplines (staging, scanning, checks).

Write the prompt as if the reader knows nothing you didn't write down —
because they don't.
