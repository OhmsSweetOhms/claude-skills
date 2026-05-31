# Reconciling threads across machines

The thread tree is committed to the repo, so the same `<threads-path>/`
can be worked on from more than one machine (or more than one clone)
that all push to a shared remote. When two clones each open, advance,
or close threads and then both push, the histories **diverge**: each
side has commits the other lacks. Pulling then requires a merge or
rebase, and the question is which files actually conflict and how to
resolve them without losing thread state.

This is distinct from the **Codex worktree** model in
`references/codex-handoff.md`, which is same-machine parallelism (git
worktrees in sibling directories, merged back at thread close, branch
never pushed). The "don't push the branch" rule there is scoped to the
long-lived worktree branch — it is **not** a ban on pushing the main
branch from a second machine. Cross-machine work is a normal
push/pull on the main branch; the reconciliation rules below are what
make it safe.

## Why it usually just works

Thread directories are **additive and independently authored**. Two
machines opening different threads, or adding plan hops / findings /
diagnostics to different threads, touch disjoint files. Git merges
those cleanly with no intervention — the union of both machines'
thread dirs is simply what you want.

The corollary: per-thread `thread.json` files only conflict when
**both machines edited the same thread**. That is the one place a
genuine three-way content merge is needed, and it is rare if the two
machines own different subsystems.

## The one guaranteed conflict surface: the registry

`<threads-path>/threads.json` (and its research mirror
`<project>/.research/INDEX.json`) is the single shared mutable file
that **every** thread-open, plan-hop, and close operation rewrites. So
whenever both machines did *any* thread work, the registry conflicts —
even when the underlying thread dirs merge cleanly.

Do **not** hand-merge the registry. It is a *derived aggregate* of the
per-thread `thread.json` files (see the "Auto-generated registry"
section of `SKILL.md`). The correct resolution is to clear the
conflict with either side's copy and then **rebuild it**:

```bash
# from the project root, after the thread dirs are merged
git checkout --theirs -- <threads-path>/threads.json   # (or --ours; the
                                                        #  content is about
                                                        #  to be regenerated)
python3 ~/.claude/skills/threads/scripts/index_threads_research.py
```

The indexer walks the merged on-disk thread dirs — which already hold
the union of both machines' work — and regenerates `threads.json` plus
`INDEX.json` from scratch. Hand-reconstructing the JSON would just be
doing the indexer's job by hand, error-prone and pointless.

## The two blocks the rebuild does not derive

The indexer regenerates almost everything from on-disk `thread.json`
files, but two blocks in `threads.json` are **asserted state, not
derived**, and a naive `--theirs`/`--ours` pick can silently drop the
other machine's contributions:

1. **`closure_log`** — append-only retirement record. Entries persist
   after a thread directory is deleted, so they cannot be reconstructed
   from on-disk dirs. The indexer *preserves* whatever `closure_log` is
   on disk and de-dups by `(thread_id, transition_date)`. If each
   machine closed different threads, picking one side's `threads.json`
   loses the other side's closures. **Seed the union before
   rebuilding** (recipe below).

2. **`current_metrics`** — if the project uses it, the indexer never
   writes it; it preserves verbatim whatever is on disk. If only one
   side carries the block, that side must be the one on disk before you
   run the indexer, or the metrics vanish.

### Seeding the `closure_log` union

Take both sides' `threads.json` from the conflicted merge, union their
`closure_log` arrays (de-dup by the same key the indexer uses), write
the result to disk, then let the indexer preserve it:

```bash
# extract the two conflicted sides
git show :2:<threads-path>/threads.json > /tmp/threads_ours.json
git show :3:<threads-path>/threads.json > /tmp/threads_theirs.json
```

```python
import json
ours   = json.load(open("/tmp/threads_ours.json"))
theirs = json.load(open("/tmp/threads_theirs.json"))

seed = dict(theirs)                       # either side; derived parts get rebuilt
merged, seen = [], set()
for e in (theirs.get("closure_log") or []) + (ours.get("closure_log") or []):
    k = (e.get("thread_id"), e.get("transition_date"))   # the indexer's idempotence key
    if k in seen:
        continue
    seen.add(k); merged.append(e)
seed["closure_log"] = merged
if "current_metrics" in ours:             # preserve it from whichever side has it
    seed["current_metrics"] = ours["current_metrics"]

json.dump(seed, open("<threads-path>/threads.json", "w"), indent=2, ensure_ascii=False)
open("<threads-path>/threads.json", "a").write("\n")
```

Then run the indexer (it rebuilds `threads`, `summary`, `promotion_log`
from on-disk dirs and preserves the seeded `closure_log` +
`current_metrics`), stage, and finish the merge.

## Procedure (start to finish)

1. **Fetch and assess**, don't pull blind:
   `git fetch` then `git rev-list --left-right --count HEAD...@{u}` to
   see ahead/behind. Diverged (both non-zero) means a merge or rebase,
   not a fast-forward.
2. **Merge (or rebase) the main branch.** Thread dirs merge cleanly;
   the registry and any same-thread `thread.json` edits will conflict.
3. **Resolve same-thread `thread.json` conflicts by hand** — these are
   real (both machines changed one thread). Take the union of plan hops
   / findings / status, preferring the more-advanced state.
4. **Resolve the registry by rebuild**, not by hand: seed the
   `closure_log` union (+ `current_metrics`), then run the indexer.
5. **Verify**:
   `python3 ~/.claude/skills/threads/scripts/index_threads_research.py --check`
   exits 0 and reports sane thread / closure / research counts.
6. **Commit and push.** The regenerated registry must land in the same
   commit as the merged thread edits — a drifted registry is a bug.

## Invariants and pitfalls

- **Never hand-merge `threads.json` / `INDEX.json`.** Always
  `--theirs`/`--ours` + re-index. The merge tool's line-level union of
  a generated file produces subtly wrong aggregates.
- **`closure_log` is the data loss trap.** It is the only thread state
  not recoverable from on-disk dirs once a thread is retired. Union it
  explicitly; do not let a side-pick drop it.
- **Same-thread edits are the only real merge.** If both machines
  worked the *same* thread, expect a genuine `thread.json` (and
  findings / handoff) conflict and resolve it on content, not
  mechanically.
- **Patch-id equality is not file-set disjointness.** `git cherry`
  can show two sides' commits as unique while they still edit the same
  registry — uniqueness of commits does not predict the conflict
  surface. Use a file-set intersection
  (`comm` of the two `git diff --name-only <base>..<side>` lists) to
  see what truly overlaps.
- **Run the indexer with the project's own interpreter** if the
  project pins one (e.g. a virtualenv). The indexer itself is stdlib
  only, but the surrounding verification (tests) usually is not.
