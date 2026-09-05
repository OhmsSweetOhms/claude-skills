# Campaign kickoff — authoring guide

A campaign kickoff launches ONE session that orchestrates SEVERAL existing
threads as a single arc ("pick up the three RTL block threads and run them
as one campaign"). It is not a worker packet (no single deliverable gate)
and not a succession prompt (it starts a new arc rather than resuming
cached state) — though it usually points at the same durable surfaces.

## Where the file lives

Bank it as a committed file before it is ever pasted — a kickoff that
exists only in chat can't be audited, reused, or corrected. Two homes:

- Inside the owning thread/coordination dir when one thread clearly owns
  the arc.
- As a loose file in the threads workspace root when the campaign spans
  threads with no owner. In that case open the file with a one-paragraph
  rationale note (why it's a loose file, what it launches) so a later
  reader doesn't mistake it for an orphan.

Name it descriptively (`<arc>-session-kickoff-prompt.md`), never with a
coined code (Core Rule 30).

## The shape

The file is: title, the rationale note, then a SINGLE fenced code block
meant to be pasted verbatim as the new session's first message. Everything
the session needs goes inside the fence; nothing outside it reaches the
session. Internal order:

1. **Role statement.** "You are the main-session orchestrator for the
   <arc> campaign. You supervise/own threads X, Y, Z for this arc." State
   the reporting line (what escalates to the operator) in the same breath.
2. **Setup first.** The environment reconcile that must happen before any
   reading — typically a git fetch/status reconcile on the checkouts
   involved, so the session starts from verified-current trees rather than
   assumed ones.
3. **Cold start — reading order.** Numbered, bounded, cross-repo aware.
   Start from each thread's current-truth block, not its history. Close
   the list with "do not bulk-read anything else first"; deeper reads get
   delegated to cheap subagents with specific questions.
4. **Sequencing.** The dependency order across the threads — and the
   explicit release: *this is a dependency map, not a strait-jacket*.
   The session may reorder within dependencies as evidence arrives; what
   it may not do is silently drop an item. Sequencing that reads as
   mandatory step-lists produces sessions that stall on blocked step 2
   instead of advancing step 4.
5. **Execution mode & escalation.** How autonomous the session runs, and
   the decision ladder: what it rules itself, what it escalates
   (ADR-grade forks, cross-cutting architecture, anything reserved to the
   operator). Escalations arrive DRAFTED — options with a lean — never as
   open questions.
6. **Standing rules.** The disciplines that survive the whole campaign:
   staging by explicit path, no status in durable surfaces, sanitization,
   record immutability, whatever the project's canon requires. Name the
   project files that carry the canon rather than restating them.

## Authoring rules

- **State-free.** The prompt names threads and files, never their current
  status ("B2 is blocked on…" rots before the session even launches).
  Point at polled surfaces; let the boot sequence discover live state.
- **Instantiate, don't abstract.** Real thread slugs, real file paths
  (repo-relative), real branch names. A campaign prompt full of
  placeholders forces the new session to re-derive the recon this prompt
  exists to bank.
- **One paste, verifiably whole.** If the block is hand-pasted through a
  terminal, end it with `--- END KICKOFF ---` and instruct the session to
  confirm the sentinel arrived. Truncated kickoffs fail silently at the
  worst possible point — the standing-rules tail.
- **Audit against the universal invariants** in SKILL.md before banking:
  role, bounded reading, scope fence, escalation section, falsifiable
  acceptance where deliverables exist, no status, sanitized paths.
