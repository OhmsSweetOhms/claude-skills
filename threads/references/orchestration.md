# Multi-session orchestration — coordinator threads, cross-thread notes, the orchestrator cache

Patterns for running SEVERAL live thread sessions concurrently under
one supervising ("orchestrator") session. Distilled from the gps_design
L1C arc (2026-07-03/04): one top-level session + a scenario-matrix
session + an audit session + a coordinator session + Codex hops, all
against one checkout, without stepping on each other.

## Roles

- **Worker session** — owns exactly the thread(s) it was launched for.
  Full authority over those threads' files per the normal lifecycle.
- **Coordinator session** — owns a *coordination thread* whose plan is
  a CHARTER for spawning and driving child threads (see below). A
  coordinator is a worker whose deliverable is other threads.
- **Orchestrator session** — supervises the program. Owns no worker
  thread. Reads everywhere; writes only (a) its own artifacts (cache,
  session-handoff records), (b) ORCHESTRATOR NOTES (below), and (c) when
  its launch prompt names one, the `## Current truth` block of its
  campaign's charter thread — its own boot surface, overwritten at wrap
  (orchestrator-handoff `references/wrap-protocol.md` step 1). A cache is
  never that surface.

## The ownership rule (extends Record discipline)

**One session owns a thread's Current-truth block at a time.** A
non-owner session must NEVER overwrite another thread's Current-truth,
plan files, findings, or thread.json state. The single sanctioned
cross-session write is the **ORCHESTRATOR NOTE**:

- an **append-only Session-log entry** in the target thread's
  `handoff.md`, newest-on-top like any entry;
- **clearly attributed** in its heading
  (`### <date> — ORCHESTRATOR NOTE: <topic>`), so the owner and any
  cold reader can distinguish supervision from the owner's own record;
- **directive or context only** — decisions made above the thread,
  gate changes, cross-thread dependencies, cautions. It never restates
  the thread's own status back at it;
- committed with **explicit-path staging** (concurrent sessions mean
  `git add -A` sweeps someone else's in-flight work).

The existing pre-commit record-discipline guard already permits this
(prepending Session-log entries is allowed); the ownership rule is
what makes it safe.

**One exception — a hop closed in a thread with no live owner.** A lane
orchestrator emits, fires and verifies packets in worker threads that no
session owns between packets. When it closes such a hop it overwrites
that thread's Current truth to present state in the closing commit
(Promote facts first, as any hop close), and says so, attributed, in the
Session-log entry it prepends. A note alone leaves the block every cold
session reads describing the hop as it stood before the fire. For the
same reason it writes, attributed, the `thread.json` hop rows, the plans
and the kickoffs of the packets it emits in such a thread — emitting,
firing and closing a packet cannot be done without them. It still never
touches a thread that has a live owner session, and it still never edits
that thread's findings.

## Coordination (charter) threads

A coordination thread's plan-01 is a **charter**, not a hypothesis:
who the coordinator is, which child threads it spawns (names, scopes,
one-line deliverables), the settled sequencing it must execute rather
than reopen, the hard constraints it enforces on every child, and an
explicit **escalation contract** (which decision classes go UP to the
user/orchestrator instead of being made locally — e.g. cross-subsystem
ADR amendments, freeze/release calls, hardware time). Two contracts
make it work:

- **Upward reporting:** the coordination thread's handoff
  Current-truth block IS the status surface the orchestrator polls —
  bounded, current at every hop transition, never a transcript.
- **Banked verdicts are verify-and-adopt:** research/recon results the
  charter cites are inputs, re-litigated only with new evidence.

## The orchestrator cache

A single durable file at the threads root (e.g.
`<threads-path>/ORCHESTRATOR-CACHE.md`) that lets a cold orchestrator
session boot on ~2k tokens instead of re-reading the program. Hard
rules, in tension order:

1. **Pointers + decisions ONLY — never live status.** Cached status
   ("X is running/paused/at step 3") rots in days and poisons cold
   readers. Live state stays in the registry + each thread's
   Current-truth block, polled fresh every session.
2. Contents: (a) a **resume protocol** (read this file → poll registry
   + the listed handoff Current-truth blocks → delegate deeper reads);
   (b) a **knowledge index** — artifact paths with one-line
   what's-inside/when-to-read; (c) **decisions in force** — stable
   until explicitly changed, each traceable to a thread/commit;
   (d) the update rule itself.
3. **Lazy reads behind cheap agents:** the protocol instructs the
   orchestrator to dispatch a low-cost subagent (e.g. Sonnet Explore)
   with a *specific question* against an indexed artifact, consuming
   conclusions — never bulk-reading kickoffs/findings/reports inline.
4. **Updated in place, same commit as the change it reflects** (a
   decision lands → its cache line lands with it). History in git.
   Dated `SESSION-HANDOFF-*.md` files remain immutable per-session
   records and are on no boot path; each carries, in its first ten
   lines, exactly `> Immutable session narrative — history, not a boot
   surface.` The pre-commit guard refuses a new one without it and any
   edit of one after its commit (adding that banner line excepted).

## Concurrency hygiene (all sessions, all the time)

- Explicit-path `git add` only; never `-A`/`.` (rule mirrored from the
  machine CLAUDE.md — it is load-bearing here, not stylistic).
- One writer per handoff file at a time; orchestrator notes are small
  and quick precisely to shrink the collision window.
- Shared mutable machine resources (RAM-heavy replays, board time) are
  arbitrated by the orchestrator as explicit named rules in the cache
  ("one heavy replay machine-wide"), not discovered via OOM.
- The auto-generated registry is regenerated by whichever session
  changed thread state, in that same commit; a session may deliberately
  defer the registry when it would aggregate another session's
  in-flight state — note it, and let the next regen self-heal.

### Long-idle packets: no watcher, no worker polling

A build-day / RTL / xsim packet can spend hours in a gate with no question.
That quiet interval is not a reason to wake its worker, and nothing is armed
to watch it. On the operator's "fire" the orchestrator runs
`fire_codex_worker.py --inbox <inbox>`, which opens the foreground TUI in a
detached tmux window through `launch_codex_worker.py`; the launcher atomically
creates schema-validated `worker-state.json` and records `WORKER_LAUNCHED`. The
orchestrator consumes the file, never a prose launch claim, and reads the live
receipt on every resume. Do not use a harness background task as a watcher:
Claude Code stops background Bash tasks on idle sessions under memory pressure
and tells the model not to restart them.

The worker binds its actual Codex session ID into the receipt as its FIRST
command in turn 1 — the orchestrator's answers are queued into the session by
that id, so an unbound worker cannot be reached at all. A `codex-self` job
verifies that binding at launch and again before ringing; a wrong or stale
session ID fails closed while `result.json` remains authoritative.

Questions, answers and the handback announcement travel as blocks in the
inbox's one `mailbox.md` (`mailbox/scripts/mb.py`); the worker is woken by
`codex queue` and the orchestrator by a `Stop` hook the fire armed, so there is
no questions watcher to arm and nothing types into a pane. Require
every long command to run through `launch_codex_mailbox_job.py`. The detached
supervisor retains logs, writes `jobs/<job-name>/<run-key>/result.json`
atomically on terminal state, and may send only a path-only self-doorbell to
the same Codex worker. Claude↔Codex content still travels only through the
mailbox file; neither agent messages or pings the other — each harness wakes
its own side.

Only the external command is detached. The Codex TUI worker stays interactive
and may be redirected at any time. A redirect that invalidates an active job
writes `control.json` through the launcher's `--cancel <run-dir> --reason ...`
path. The supervisor owns a transient systemd user scope and kills the complete
cgroup. `cancelled` is valid only when every command reports
`cleanup_verified: true` and the cgroup is empty or removed; otherwise the
packet is blocked on an unverified cleanup. A redirect unrelated to the active
job leaves it running.

If the operator wants periodic visibility, an orchestrator-side loop may read
the branch tip, governor state and mailbox locally and report them without
asking or waking the worker. Unchanged state never creates a worker turn.
When no compatible doorbell exists, the terminal result remains authoritative
and the operator resumes the worker manually. The packet's `HANDBACK` block
ends any orchestrator-side status display.

Three files own three different facts; never merge them into prose or duplicate
their fields:

- `worker-state.json` — foreground worker process/lifecycle, launcher-owned.
- `progress.json` — plan checkpoint and next action, worker-owned.
- `jobs/<job-name>/<run-key>/result.json` — detached command terminal result,
  mailbox-supervisor-owned.

---

## Hard-won facts from the gps_design program

Moved verbatim from that project's `CLAUDE.md` (2026-09-04).

- **A Codex/Opus worktree's pre-commit guard reserves `.threads/` for the
  main checkout**, so every packet's thread-dir deliverable (a memo, a format
  pin) arrives UNCOMMITTED in the worktree; the orchestrator promotes it. This
  is correct behaviour, not a failed handback. **An in-process Opus agent
  cannot `SendMessage` its parent session** ("is this process's own main
  session") — its RETURN is the doorbell; put that in the launch prompt
  instead of a ring instruction.
- **A Claude-session packet worker ends its turn on `OPEN_QUESTION` and
  does not resume when the answer file lands** — `ListAgents` and
  `SendMessage` it by name after `answer_question.py`, or it idles
  (plan-16 lost ~4 h). Its handback arrives the same way, as a
  `HANDBACK <path>` ring; check every operator rider has a metric key
  before accepting.

Verbatim, moved from the same file on 2026-09-20 (thread
`cross-cutting/20260920-orchestrator-context-diet`, plan-01 Step 3).

- **Re-run a claim's evidence before trusting it, worker or reviewer** —
  packet 4's "CRLB-efficient once acquired (0.246 Hz)" was a truth-selected
  subset (the deterministic population gives 0.327 Hz median / 2.56 Hz
  RMSE); its "1.63e-11 parity" was one bin (worst 2.07e-11, 20× outside
  the taxonomy's Class-3 bound; operator-accepted as a documented
  exception, lane decision 14); the adversarial review's "absolute paths in
  lane docs" had zero hits in its own audit output. Roughly one claim in
  three moved on re-run.
- **A Codex worker's sandboxed mailbox job cannot reach the systemd user bus:
  the record stays `queued` with an empty supervisor log (bitten three times by
  2026-09-15: Iridium plan-25, shard 5, shard 7).** The remedy is a host launch
  of the same contract (the orchestrator's, on the worker's `q-NN`, or the
  operator relaunching the worker unsandboxed); the launcher-refuses-in-sandbox
  fix is routed to the threads skill and has not landed.
- **An emitter trap (2026-09-19):** the threads emitter resolves a MERGED worktree
  when the thread's active `codex_worktrees[]` entry has no `path`: pass
  `--worktree-path` on every emit until the skill is fixed.
