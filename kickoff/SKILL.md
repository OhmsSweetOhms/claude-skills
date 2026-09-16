---
name: kickoff
description: Author and emit work-packet kickoffs — the PRODUCER side of the packet lifecycle. Use when asked to write a kickoff, emit a packet, launch a fleet or campaign, draft a successor launch prompt, or author a continuation packet from a handoff, including standalone worktrees without threads. Classifies Codex worktree packets, succession prompts, campaign kickoffs, agent-fleet runs, and standalone continuations; routes to the owning skill or contract and supplies formats where none exists. Producer-only — for BOOTING a session FROM an existing kickoff or handoff, use orchestrator-handoff instead. Not for sprint planning or generic kickoff meetings. Also triggers on "fire", "tee it up", "kick off plan-NN", and "continue from this handoff", even without the word kickoff.
---

# Kickoff

The same lifecycle runs every session: boot → recon → rule → implement →
verify → bank → handback. The costly failures are at the *start* — a packet
emitted with a missing escalation section, an unstated scope fence, an args
schema the runner rejects, or a prompt that re-derives a value an authority
already pinned. This skill makes packet emission uniform: classify what
you're emitting, then either route to the protocol that owns it or encode
it from the formats below.

**Producer side only.** Consuming a kickoff (cold-booting from one) is the
orchestrator-handoff skill's territory; the emitted documents are
self-describing paste-blocks precisely so the consumer needs no extra
tooling.

## Step 0 — classify what you're emitting

| You are emitting | Variant | Protocol source |
|---|---|---|
| A Codex worktree packet (plan-NN handed to an isolated worktree session) | **codex-packet** | `/threads` skill (codex-handoff + emit script) **plus** the project's launch contract if one exists |
| A cold-boot prompt for a successor or worker session resuming succession state | **succession-prompt** | orchestrator-handoff skill (`references/launch-prompt-template.md`) |
| A kickoff for one session orchestrating several threads as a single arc | **campaign** | `references/campaign-kickoff.md` (this skill) |
| An in-session multi-agent fleet run via the Workflow tool | **fleet** | `references/fleet-packets.md` (this skill) |
| The next stage of a STANDALONE project (no threads workspace) — a continuance drafted from a handoff doc in the same worktree | **continuation-packet** | `references/continuation-packet.md` (this skill) |

Compound requests decompose: a campaign kickoff that will itself emit Codex
packets is TWO emissions — author the campaign prompt under **campaign**,
and let that session emit its packets under **codex-packet** when it runs.
Don't blend the formats.

## Universal emission invariants

Every variant, no exceptions. These are the recurring start-of-packet
failures, inverted:

1. **Role statement first.** Who the reader is, who they report to, and
   which decisions are already made and not theirs to reopen. A packet
   without a reporting line invites re-litigation of settled rulings.
2. **Bounded reading order.** Numbered files, in order, with an explicit
   "do not bulk-read anything else first". Unbounded recon burns the
   session's context before work starts.
3. **Scope fence with a NOT-your-scope section.** Adjacent work gets named
   as briefing-only. Blast radius beyond the fence is an escalation, not a
   judgment call.
4. **Escalation is a section, not a vibe.** Enumerate the stop triggers
   (contract ambiguity, net-new structure, interface/schema changes,
   re-deriving an authoritative value, cross-packet blast radius) and close
   with the default: *if you're unsure whether a choice is yours to make,
   it isn't — ask.* **The stop triggers are the ONLY stop points; say so.**
   Every worker packet states run-to-completion: finished steps, green
   gates, commits and phase boundaries are checkpoints, not pauses; the
   worker continues through every phase to the handback and resumes on its
   own when a question is answered or a detached job lands. Workers left to
   default stop between phases to report, which costs an operator round
   trip per phase for nothing (Codex, 2026-09-16). Codex packets get this
   mechanically from the threads emitter's turn-1 `RUN TO COMPLETION`
   block; a Claude-agent or hand-authored packet carries it in its
   escalation section.
5. **Falsifiable acceptance.** Every deliverable carries the exact command
   an independent verifier can re-run and the expected result. "Done" that
   can't be re-checked isn't done.
6. **No status in banked prompts.** Status rots; a banked kickoff that
   embeds live state misleads every later reader. Point at the surfaces
   that get *polled* (cache, handoffs, registry) instead.
7. **Sanitize before banking.** No absolute paths or usernames in any
   committed kickoff — repo-relative paths or placeholders only. The one
   exception is terminal-only launch material (e.g. a Fire Card) that is
   deliberately never committed *because* it carries absolute paths.
8. **Plan and launch prompt are two files; the launch file is paste-block
   only.** The plan/rows document and the "You are the …" launch prompt
   never share a file, and the launch file carries no producer-side meta
   prose (no "banked prompt", "this wraps the kickoff", "paste below") —
   every line in it is addressed to the reader. State the role's
   negative space explicitly (a worker does not emit packets, author
   kickoffs, launch sessions, or schedule work; "the plan is written in
   <file> — execute it, don't re-plan it"). A worker that read
   orchestrator-voice meta in its launch file booted AS the orchestrator
   and tried to launch a packet (2026-08-16/17).
9. **Mailbox is file-then-doorbell.** For Claude worker sessions the
   packet names the orchestrator's `ListAgents` session name and the
   rule: write the question/handback FILE first, then `SendMessage`
   only `OPEN_QUESTION|HANDBACK <repo-relative path>`; block on the
   await script as fallback. For an explicitly bound Codex orchestrator and
   worker, use the threads skill `references/codex-mailbox-doorbell.md`:
   record both actual session UUIDs and deliver file-first events through
   `scripts/ring_codex_mailbox.py`. Claude's hook scan does not run in Codex;
   do not rely on it for that exchange. Existing Claude orchestration uses
   its own hook/watcher contract. One
   channel: the packet says the worker escalates via the mailbox ONLY,
   never also to the operator via AskUserQuestion (two channels, two
   possible rulings — seen 2026-08-17).
10. **Verify the emission survived.** Script-emitted packets: check the
   script's output landed. Hand-pasted blocks: append an explicit
   `--- END KICKOFF ---` sentinel so terminal truncation is detectable.
   Codex packets: emission is not done until the Fire Card and handback
   inbox exist.
11. **Every observable in the plan is read from the code, and audited
   before emission** (global Core Rule 32). Every behavioral claim, status
   token, JSON path, register or field name, file path, command, and
   acceptance clause in the plan and kickoff is grounded in the code at the
   emitted base commit, cited `file:line`, or marked as inference. Before
   declaring the packet emitted, run a **pre-emission token audit**: a cheap
   read-only agent (Sonnet/Haiku, per the token-economy rule) reads the
   plan and kickoff in full and, for every concrete observable they name,
   finds where the code emits or accepts it and whether it exists under the
   condition the row reads it. It returns a table of (row, quoted phrase,
   verdict EXISTS / DIFFERENT NAME / CONDITIONAL / MISSING, `file:line`,
   corrected token). The producer re-verifies every MISSING and CONDITIONAL
   row itself — auditors are wrong about one claim in three too — fixes the
   plan in place, and only then emits. Ask of each acceptance clause: what
   will the worker actually be able to observe and quote under this row's
   conditions? (Earned 2026-09-14: seven plan clauses in one orchestrator
   session named a status block that only exists when tracking is enabled,
   an engine state token the code never emits, a no-`/dev/mem`-holder
   condition a serving daemon violates by design, register reset semantics
   the RTL does not have, a mid-epoch behavior the pipeline could not
   provide as written, and a gate script path that did not exist; every one
   cost a worker question, and one audit run before emission caught three
   blockers that would have stopped a board leg.)

## Routing notes per variant

### codex-packet — route, don't re-encode
Invoke the `/threads` skill; its codex-handoff reference and
`emit_codex_launch_packet.py` own the worktree lifecycle, the plan-file-as-
prompt format, the handback JSON contract, and the questions mailbox.
**Two files here too (invariant 8):** the `plan-NN-<slug>.md` carries ONLY
the plan (why, hypothesis, rulings, steps, constraints, metrics); the
launch chart (project launch contract), role/reporting line, and turn-1
pointers go in a sibling `kickoff-planNN-<slug>.md`. "Plan-file-as-prompt"
means Codex EXECUTES the plan file, not that the plan file carries the
launch framing — mixing them re-creates the worker-acts-as-orchestrator
drift (2026-08-17, plan-11: chart + role were authored into the plan and
had to be split out). Then
check the project for a launch-contract doc (e.g.
`docs/codex-packet-launch-contract.md`) — project contracts layer required
extras on top (launch chart, Fire Card, build-host/license rules,
interface-integrity gates) and win over generic defaults. Re-encoding any
of this here would just create a second copy that drifts.

### succession-prompt — route, don't re-encode
Invoke the orchestrator-handoff skill and use its launch-prompt template
(role + reporting line, boot sequence, active task, queue, boundaries).
Its cardinal rule matters most at authoring time: the prompt is
deliberately state-free — state lives in the resume cache it points at.

### campaign — encoded here
Read `references/campaign-kickoff.md` before drafting. Covers the
paste-block shape (role → setup → cold-start reading order → sequencing →
escalation → standing rules), where the file gets banked, and why the
sequencing section is explicitly "not a strait-jacket".

### continuation-packet — encoded here
Read `references/continuation-packet.md` when the project has no threads
workspace and the succession surface is a handoff document. The product
is a SHA-anchored, fail-closed stage plan (steps table with pass
criteria, standing fences, a wrap step that updates the handoff). Do not
import threads machinery — no Fire Card, no handback JSON — the
universal invariants land directly in the plan instead.

### fleet — encoded here
Read `references/fleet-packets.md` before authoring a fleet run. Covers
packet-object authoring (prompt, contract_path, authority_pins,
physical_state), kind selection, the blocked-for-ruling resume protocol,
and the pre-flight checks that have historically eaten fleet runs (args
shape, smoke run, budget statement). The runtime policy itself lives in
the project's workflow script — cite it, never restate its parameters.
