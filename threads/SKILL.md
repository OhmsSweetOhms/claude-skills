---
name: threads
description: Manage debug-thread directories for hypothesis-driven investigations that span multiple sessions. Use this skill whenever the user wants to start a new debug thread, add a plan hop to an existing thread, capture a findings snapshot, register a diagnostic script, import external review feedback (from Codex, claude.ai, or a colleague), promote a diagnostic into a permanent regression test, close a thread, or link a thread to a `/research` session. Also use it to process a Codex handback end-to-end — the self-healing triage→reconcile→ADR→commit→kickoff lifecycle, including superseded/zombie-thread detection against actual worktree state, identifier-collision scrubbing, provenance-checked ADRs, and bash-safety/fingerprint commit linting. Also triggers on any mention of `threads/`, `thread.json`, "debug thread", "plan hop", "findings snapshot", "promote this diagnostic", "external comment", "process a handback", "handback lifecycle", "superseded thread", "zombie thread", or when the user is working inside a `threads/<subsystem>/<slug>/` directory. This skill plays nice with `/research`: it maintains the bidirectional link (`thread.json.linked_research[]` ↔ `session-manifest.json.spawning_thread`). Do NOT use for sprint boards, feature planning, or one-off debug commands — this is specifically for multi-hop investigations that accrete plans, data, and diagnostics over time.
---

# threads — Debug Investigation Container Pattern

A "thread" is a **hypothesis-driven, time-bounded investigation**
that accretes state across sessions. Instead of N scattered
`*-PLAN.md` files at the repo root and a pile of orphaned
`diagnose_*.py` scripts, everything for one investigation lives in
`threads/<subsystem>/<YYYYMMDD>-<slug>/` with a machine-readable
`thread.json` manifest.

The goal is **session handoff**: a future collaborator — your next
session, another Claude, or a human — can pick up the investigation
cold by reading one directory, not by grepping `Status:` across a
dozen markdown files.

## When to use this skill

Trigger on any of:

- User says `/threads ...` or types a command like "new thread",
  "promote this diagnostic", "close the auth-latency thread".
- User asks to start a debug investigation that will clearly span
  multiple plan revisions or produce diagnostic scripts.
- User mentions `threads/`, `thread.json`, `threads.json`, or refers
  to plan hops, findings snapshots, or external reviews.
- User is editing a file under `threads/<subsystem>/<slug>/` and
  needs to update manifests, add plan hops, or capture findings.
- User wants to link a `/research` session to a thread, or
  vice-versa.
- User wants a survey of the threads tree's overall state, asks
  "what's blocked," "thread status," "thread review," or wants to
  triage stale/blocked threads as a batch. → **Status review**
  workflow produces a frozen `review-<YYYY-MM-DD>.md` snapshot.

Do NOT trigger for:

- Sprint planning, feature roadmaps, TODO lists — those are not
  hypothesis-driven.
- One-off debugging that resolves in a single session with no
  artefacts worth preserving.
- Architectural or block-level design plans — those live at
  `<package>/*-PLAN.md` and are NOT debug threads. See the "Type A
  vs Type B" distinction in `references/layout.md`.

## Core concepts (read first)

Before you act on any operation, skim `references/layout.md`. It
covers:

- The directory tree and file roles.
- **Type A vs Type B**: architectural plans stay in place; only
  debug threads migrate into `threads/`. This distinction is
  load-bearing — misclassifying will churn the wrong files.
- Naming conventions (thread slug, plan numbering, findings,
  external-comment filenames).
- The unified status vocabulary (`active`, `blocked`, `superseded`,
  `closed`) and why nuance lives in `outcome` prose, not in the
  enum.

For exact JSON shapes, `references/schemas.md` has the
`threads.json`, `thread.json`, and external-comment frontmatter
contracts with examples.

For Codex worktree sessions, `references/codex-handback.md` defines
the root worktree handoff inbox (`codex-handoff/<plan-id>/` with
`handback.json`, `handback.md`, `scripts/`, `temp/`, `artifacts/`,
and the `questions/` ambiguity mailbox), the recording discipline,
lifecycle visibility rules, and the consumer triage step before
merge-back or next-hop activation.
The full workflow (bootstrap, launch, ambiguity mailbox, merge-back)
lives in `references/codex-handoff.md`, with templates under
`assets/templates/` (`codex-handoff-dir-README.md` for the inbox
README, `codex-handback-template.md` for handback.md,
`codex-question-template.md` for mailbox questions,
`codex-handback-retroactive-prompt.md` for recovery cases), the JSON
schema at `assets/schemas/codex-handback.schema.json`, and supporting
scripts at `scripts/` (`bootstrap_codex_worktree.sh`,
`emit_codex_launch_packet.py`, `watch_codex_questions.sh` +
`await_codex_answer.sh` for the mailbox, `triage_codex_handback.py`,
`merge_codex_worktree_back.sh`). The mailbox: when Codex hits an
architecture/contract decision the plan/ADRs/vectors don't pin, it
writes `questions/q-NN.md` (`status: open`) into the inbox and blocks
(1 h cap); the main session's background watcher wakes, answers in
the same file — or marks it `escalated` when it is a user-level
decision — and relaunches the watcher. Timeout on either side
degrades to the manual handback-as-blocked flow.

**The plan file IS the launch prompt.** When a plan hop launches Codex,
the plan file at `.threads/<thread-id>/<plan-NN>-*.md` is the design
artifact Codex consumes as turn 1. No separate prompt scaffold exists.
`scripts/emit_codex_launch_packet.py` packages the six mechanical
facts (plan-file absolute path, worktree, branch, base SHA, handback
inbox, thread/plan IDs) plus three generic operational rules (don't
push; stop on architecture/contract ambiguity — write a
`questions/q-NN.md` mailbox file and block on the answer, never infer
through it; write structured handback) that the user pastes into
Codex's sidecar terminal at turn 1. The plan must be fleshed out per the tiered
template before launch — base sections always filled, Codex add-ons
below the divider filled when the hop is a Codex hop.

**Discipline:** when authoring a plan doc that includes Codex
execution steps, *reference this workflow rather than restating its
conventions inline*. Plans should point at the workflow ("per the
Codex worktree handoff in `references/codex-handoff.md` and handback
contract in `references/codex-handback.md`") and let the templates
+ schema + scripts handle scaffolding. Inlining the inbox layout,
handback schema fields, or recording discipline in plan docs leads
to drift — the skill evolves the contract while the plan doc holds
a stale snapshot. The bootstrap script invoked from the plan does
everything the inline spec would have specified, but stays current
when the skill updates.

To process a handback **end-to-end** — triage, reconcile against actual
worktree state (superseded/zombie detection), author provenance-checked
ADRs, close/launch, lint the commit and cleanup commands past the
bash-safety and fingerprint guards, and emit a verified-whole kickoff
packet — follow `references/self-healing-handback-cycle.md`. It is the
orchestration macro over **Process codex handback**, **Close thread**,
and **New plan hop**, adding the cross-stage heal checks and a
per-stage report card.

**Prompt and handback share the inbox.** The Codex launch packet
belongs in the same `<worktree>/codex-handoff/<plan-id>/` directory
as the handback Codex will write back. `scripts/emit_codex_launch_packet.py`
writes its output to `<worktree>/codex-handoff/<plan-id>/prompt.md`
when `--out` is supplied. Splitting prompt and handback across two
repos creates a maintenance burden (two locations to sync, drift
between them, references that go stale when one side moves). Keep the
handoff infrastructure self-contained in the worktree branch; on
terminal merge-back, the inbox lands on `socks/main` (or equivalent)
as a single coherent unit.

## Operations — dispatch table

When the user's request matches one of these, follow the named
section in `references/workflows.md`:

| User's ask | Workflow section |
|-----------|------------------|
| "Adopt threads/ in this repo" / "initialize threads/" | **Bootstrap** |
| "Start a new thread for X" / "new thread under Y subsystem" | **New thread** |
| "Add a plan hop" / "write plan-NN for <existing thread>" | **New plan hop** |
| "Capture findings" / "write a findings snapshot" | **Findings snapshot** |
| "Register this diagnostic" / "track diagnose_*.py in thread.json" | **Register diagnostic** |
| "Import Codex/claude.ai/colleague feedback" / "add external comment" | **Import external review** |
| "Promote this diagnostic to a test" / "it's a regression gate now" | **Promote diagnostic** |
| "Close the thread" / "mark thread as done" | **Close thread** |
| "Retire closed threads" / "audit and delete closed thread directories" / "clean up the working tree" / "garbage-collect closed threads" | **Retire thread** |
| "Thread status" / "thread review" / "what's blocked" / "/threads --review" / triage stale threads as a batch | **Status review** |
| "Link this research session to the thread" / "wire up the research back-pointer" | **Link research** |
| "Hand thread X off to codex" / "spawn a codex worktree on X" / "spawn codex on X" / "run codex on X" | **Codex worktree handoff** (`references/codex-handoff.md`) |
| "Recover a missing codex handback" / "retroactive handback" / "closed plan has no handback" | **Retroactive handback** (`references/codex-handoff.md`) |
| "Triage codex handback findings" / "process codex handback" / "classify handback follow-ons" | **Process codex handback** (`references/codex-handoff.md`) |
| "Run the full handback lifecycle" / "process this handback end-to-end" / "close the hop and launch the next" / "self-healing handback cycle" | **Self-healing handback lifecycle** (`references/self-healing-handback-cycle.md`) |
| "Merge the codex worktree work back" / "the codex agent finished, pull the work in" | **Codex worktree merge-back** (`references/codex-handoff.md`) |
| "Reconcile threads pulled from another machine" / "thread state diverged across clones" / "threads.json merge conflict after pull" | **Cross-machine reconciliation** (`references/cross-machine-reconciliation.md`) |
| "Orchestrate several live threads" / "coordination thread" / "charter thread" / "orchestrator cache" / "write a note into another session's thread" / concurrent sessions on one repo | **Multi-session orchestration** (`references/orchestration.md`) |
| "Wrap this session" / "hand off to a successor" / "give me a handoff prompt" / "cold-launch prompt" / running out of context mid-thread | **Session succession → the `orchestrator-handoff` skill** (wrap-protocol, launch-prompt template, succession audit) — but SCOPE-GATED, see below |

**Session-succession scope gate.** This skill owns the thread's FILES
(handoff.md Current-truth/session-log discipline, findings, registry);
the `orchestrator-handoff` skill owns SESSION succession (the ordered
wrap protocol, successor launch prompts, the orchestrator cache). Which
you need depends on the thread's blast radius, not its age:

- **Small thread** (one RTL module, one C file, one investigation that a
  successor can resume from handoff.md Current-truth alone): the
  threads file discipline IS the handoff. Close the hop, overwrite
  Current-truth, append the session log — done. Do NOT drag in the
  wrap-protocol machinery.
- **Program-level thread** (spans substrates — BD + firmware + Linux +
  host; carries live board/bench state a successor must inherit; runs
  multi-session with cold successors; or produces rulings OTHER threads
  must obey): load `orchestrator-handoff` and follow wrap-protocol.md
  — boot surface FIRST, in-flight work captured, uncommitted state
  stated, narrative LAST — and maintain a thread-local launch prompt
  (durable + state-free, pointing at the boot surfaces). Rulings that
  cross thread boundaries go to the orchestrator cache, not just the
  thread (the fresh-cache corollary: thread-scope stays in the thread).

The test in one line: *if a successor booting cold from handoff.md
alone would miss something load-bearing (bench state, a cross-thread
ruling, an in-flight build), you need the orchestrator-handoff canon.*

If the user's ask doesn't match cleanly, ask which operation they
want before acting. Don't invent new operations.

The codex handoff inbox is a delivery mechanism for thread work, not
a separate domain — a thread already has a plan; codex executes
source-code work in an isolated worktree while bookkeeping stays on
`main`. The worktree + branch are **long-lived across the thread's
full lifetime** (all plans, all hops): bootstrap once at thread
start, re-invoke codex against the same worktree N times as the
plan progresses, and merge back to `main` exactly once at thread
close — and only when the user explicitly requests it. The
merge-back script never auto-merges; it shows the incoming diff and
prompts for confirmation. See `references/codex-handoff.md` for the
full workflow and the pre-built bootstrap / launch-packet / merge-back
scripts under `scripts/`. The plan file at
`.threads/<thread-id>/<plan-NN>-*.md` is the launch prompt; the
launch packet (`scripts/emit_codex_launch_packet.py`) emits the six
mechanical facts pointing Codex at it.

## Integration with `/research`

`references/research-integration.md` covers the bidirectional link:

- **Thread side**: `thread.json.linked_research[]` — array of
  `{path, title, spawned_by_this_thread, consumed_artifacts[]}`
  entries. Each entry records a research session the thread either
  **spawned** (thread preceded research) or **consumed** (thread
  used research produced independently).
- **Research side**: `session-manifest.json.spawning_thread` —
  string path like `"threads/receiver/20260414-nav-anchor-precision"`.
  Optional field; the `/research` schema is permissive so adding it
  doesn't break anything.

The two sides should stay in sync when both exist. The **Link
research** workflow handles writing both.

## Auto-generated registry

`<project>/.threads/threads.json` is **auto-generated**, not
hand-edited. The canonical source for every thread is its own
`thread.json` (status, plan hops, findings, promotions, linked
research, etc.). The registry is the aggregate produced by
`~/.claude/skills/threads/scripts/index_threads_research.py`,
which also writes `<project>/.research/INDEX.json` for the
research-side mirror.

**Mutating workflows** — new thread, new plan hop, findings snapshot,
register diagnostic, import external review, promote diagnostic,
close thread, link research, codex worktree merge-back — must
regenerate the registry as their **final step**:

```bash
python3 ~/.claude/skills/threads/scripts/index_threads_research.py
```

**Status-review workflow** also fires the indexer, but as a **first
step**: the review reads `threads.json` to compute status counts and
triage candidates, so a stale registry produces a misleading review.
Same command, run before `status_review.py`.

Run it from the project root (the directory containing `.threads/`
and `.research/`). The script discovers the project root from the
current working directory; pass `--project-root <path>` if invoking
from elsewhere. Add `--check` to validate without writing (exits 1
on any cross-reference findings); add `--print` to also see a
one-line summary plus per-finding-kind breakdown.

The registry must always be regenerated and committed in the same
commit as the per-thread edits. A drifted registry is a bug.

Because the registry is the one shared mutable file every thread
operation rewrites, it is also the guaranteed conflict point when the
same repo is worked on from more than one machine and both push. When
that happens, never hand-merge the registry — resolve by rebuild, and
seed the union of the non-derived blocks (`closure_log`,
`current_metrics`) first. See `references/cross-machine-reconciliation.md`.

## Invariants (violate these and the pattern falls apart)

- **Unified status enum.** Threads and plan hops both use
  `active | blocked | superseded | closed`. Don't invent new
  values. Put substance in `outcome` prose.
- **External-review raw content is never edited.** The verbatim
  section is the attribution record. Only the triage table and
  merge notes get updated.
- **Merge requires a commit hash.** An external review may only be
  marked `disposition: merged` when every accepted triage-table
  point has a commit hash in `merged_into[]`. This makes every
  accepted external idea traceable to a git operation.
- **Promotion uses `git mv`, not copy.** Preserves
  `git log --follow` lineage from the test back through the
  diagnostic's history.
- **`temp/` is gitignored; `temp/README.md` is tracked.** The
  README documents regeneration commands; the bytes don't get
  committed. If an output can't be regenerated, it goes in
  `data/` instead.
- **Handoff tracks plan state.** Whenever a plan hop is added,
  closed, superseded, or materially edited in place, the
  `handoff.md` **"Current truth" block** must be **overwritten** (not
  appended) against the new plan state in the same commit — kept
  bounded, dead claims let leave it, "RULED OUT" kept current — and a
  new Session-log entry appended at the top describing the transition.
  The New-plan-hop and Close-thread workflows include this step;
  in-place plan edits are the user's responsibility to mirror. A
  Current-truth block that still names a closed/refuted claim is a
  cold-start trap.
- **Record discipline is enforced, not just convention.** Three
  edit-classes (CONVENTIONS § "Record discipline"): the Current-truth
  block + `notes`-as-pointer are **overwritten**; the Session log is
  **append-only**; `findings-*.md` bodies / closed `outcome` /
  external-review verbatim are **immutable** (a correction is a new
  findings file; a dead one may gain only a `> SUPERSEDED by …`
  banner). `scripts/check_record_discipline.py`, wired as a pre-commit
  hook, **blocks** commits that edit a findings body or a past
  Session-log entry. Don't bypass it with `--no-verify` without a
  reason — it is catching exactly the back-edit that poisons threads.

## Sanity checks before acting

When asked to modify a thread, always:

1. Read the thread's `thread.json` first. If the file doesn't
   parse, stop and flag the corruption — don't append to a broken
   manifest.
2. Check `thread.json.status`. If `closed` or `superseded`, confirm
   with the user before adding new hops or diagnostics.
3. Check the top-level `threads/threads.json` exists and references
   this thread. If out of sync, fix it as part of the change.
4. If you're writing a new plan hop, the previous hop should be
   marked `closed` or `superseded` with an `outcome` before the new
   one is added.

These are cheap to check and expensive to recover from if skipped.

## Session-skill tracker (optional)

For multi-skill investigations where you'd like to capture gaps and
improvement ideas about the skills themselves as you use them, run:

```bash
bash scripts/init_tracker.sh threads <other-skill> [<more>]
```

This creates `.claude/workspace/skill-tracker-<YYYYMMDD>.md` (in the
project's workspace dir) with a template, entry schema, and an
end-of-session pass instruction. Idempotent — existing trackers are
left alone. Pattern is borrowed from `/socks-tracker`, but works for
any skills the session is exercising. At session end, the template
prompts you to group entries per skill and propose concrete edits
(file + before/after) without applying them automatically.

When to bother: long sessions, fresh-skill shakedown runs, or
hand-offs where the next session might benefit from the skill being
sharper. Skip for one-shot tasks where there's nothing to learn.

**Thread handoff points are the standing trigger.** Whenever a
workflow updates `handoff.md` (New plan hop, Close thread, hop
closure after a Codex handback), pause and flush any skill friction
the hop surfaced into the tracker before it fades — a workflow step
that needed a workaround, a script that fought the situation, a
reference that was stale for what the code actually looked like.
Hop transitions are where that context is still concrete and citable
(the findings/commit to quote as evidence already exist), and it is
exactly the knowledge the next session loses. If no tracker exists
yet, one friction observation is reason enough to init one.

## Templates

`assets/templates/` holds the file skeletons this skill copies
during Bootstrap and New-thread operations. Each template has
placeholder tokens like `{{SLUG}}`, `{{DATE}}`,
`{{SUBSYSTEM}}` — substitute them literally when copying.
Templates:

- `top-level-README.md` — the project-level `threads/README.md`.
- `top-level-CONVENTIONS.md` — the project-level `threads/CONVENTIONS.md`.
- `top-level-threads.json` — empty threads/promotion_log arrays.
- `thread-README.md` — per-thread landing page.
- `thread.json` — per-thread manifest skeleton.
- `thread-handoff.md` — per-thread rolling session-to-session
  handoff journal. Created at thread birth; updated by the
  New-plan-hop and Close-thread workflows whenever the plan
  changes (forward-looking sections get stale otherwise), and on
  user request for in-session notes. See "Handoff journal vs
  README vs findings" below for the cadence contract.
- `plan-01-template.md` — starter plan.
- `findings-template.md` — snapshot skeleton.
- `external-comment-template.md` — verbatim + triage scaffold.
- `temp-README.md` — regeneration-commands scaffold.
- `codex-handback-retroactive-prompt.md` — recovery prompt for a
  closed or already-executed plan hop that lacks structured
  handback artifacts.
- `review-template.md` — index-scope status-review skeleton with
  AUTO-BEGIN/AUTO-END markers and boilerplate manual sections
  (strategic tiers, cross-tier file overlaps, critical-path
  observations, recommendations). Used by the **Status review**
  workflow; the script `scripts/status_review.py` rewrites the auto
  block, manual sections persist across regen passes.

## Handoff journal vs README vs findings

Three prose artefacts, three different cadences and purposes:

| File | Cadence | Role |
|------|---------|------|
| `README.md` | Changes rarely; structural | Status header, plan-lineage table, findings table, research linkage, next-step pointer. The stable overview. |
| `findings-<YYYY-MM-DD>.md` | Once per plan hop closure / decision point | Point-in-time snapshot. What was measured, what was refuted, what the current best understanding is. Never overwritten. |
| `handoff.md` | **Whenever a plan changes** (new hop, close, supersede, material in-place plan edit) + on user request for in-session notes | Two parts, two cadences: a **bounded "Current truth" block** (overwritten each hop — the only forward-facing state) and an **append-only Session log** (dated journal, newest-first). See CONVENTIONS § "Record discipline — three edit-classes". |

`handoff.md` is **updated by the New-plan-hop and Close-thread
workflows**, alongside `README.md` and `thread.json`. It is also
updated on user request for in-session notes. The reason: when the
plan changes, the handoff's **"Current truth" block** goes stale
immediately, and a cold-start reader lands on a misleading snapshot.
The fix: **overwrite** that block against the new plan state in the
**same commit** that changes the plan — keep it bounded, let dead
claims leave it (move the "why it died" into a Session-log entry),
and keep the "RULED OUT" line current. Do **not** append the change
into the block as a changelog — that is the poison the bounded block
exists to prevent. A new Session-log entry at the top describes the
transition in prose. The `thread.json` `codex_worktrees[].notes`
field stays a **pointer** to this block, never a parallel changelog.

`handoff.md` is never **derived mechanically** from other files —
that's what `thread.json` is for. Always edit with attention: the
Session-log narrative is the human record, and the forward-looking
sections are re-thought, not auto-populated.
