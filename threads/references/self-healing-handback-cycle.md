# Self-healing handback lifecycle

The end-to-end cycle a **main session** runs when a Codex worktree
hands a plan hop back: triage → reconcile → ADR → close/launch →
commit → packet. "Self-healing" means each stage carries a *check* that
catches the predictable failure of that stage and repairs it before it
propagates — a superseded thread, a colliding identifier, an
unprovenanced citation, a truncated packet, a commit the guards will
reject. Run the stages in order; do not skip a check because the input
"looks clean" — the checks exist because clean-looking inputs are
exactly what slipped through before.

This reference is the **orchestration layer**. Each stage delegates the
mechanics to an existing atomic workflow or script; this file adds only
the cross-stage glue and the heal checks. Read alongside:

- `references/codex-handback.md` — the handback contract + consumer triage.
- `references/codex-handoff.md` — launch packet, merge-back, mailbox.
- `references/workflows.md` — **Process codex handback**, **Close thread**,
  **New plan hop**, **Status review**.
- `references/cross-machine-reconciliation.md` — registry divergence.

## When to run it

Trigger: a Codex worktree wrote `codex-handoff/<plan-id>/handback.{json,md}`
and control is back in the main session. Also run it (skipping stage 1)
for a **main-session-only** hop close — the reconcile/ADR/commit/packet
heal checks apply identically whether Codex or the main session did the work.

Dispatch row (SKILL.md): *"Process a codex handback end-to-end" /
"run the full handback lifecycle" / "close the hop and launch the next"*.

---

## Stage 0 — Snapshot ground truth (before reading the handback)

The handback describes what Codex *believes* it did. Capture what the
repo *actually* shows, so every later stage diffs against reality, not
against the handback's self-report.

```bash
WT=<worktree-path>; BR=<worktree-branch>
git -C "$WT" rev-parse "$BR"                 # actual branch HEAD
git -C "$WT" log --oneline -5 "$BR"
python3 ~/.claude/skills/threads/scripts/index_threads_research.py --print \
  | sed -n '/<thread-id>/,/^$/p'             # registry's view of the thread
```

Record the three numbers you will reconcile against: worktree branch
HEAD, the `base_commit`/`head` the handback *claims*, and the
`thread.json::codex_worktrees[]` SHAs.

---

## Stage 1 — Triage the handback

Mechanics: **Process codex handback** (`workflows.md`) +
`scripts/triage_codex_handback.py`. Classify every `discoveries[]`,
`follow_ons[]`, `investigations[]`, `blockers[]`, and unresolved
`gates[].caveats[]` item as `pre-merge blocker` / `post-merge follow-up`
/ `accepted as-is`.

**Heal check — gate verdicts vs evidence.** For each `gates[]` with
`verdict: pass`, confirm the `evidence_path` exists and says what the
verdict claims. A synthesis/impl gate must hand over the full failing-path
list, not just the timing summary (`codex-handback.md` §Synthesis-gate
evidence). A `pass` with a missing or summary-only evidence path is
downgraded to `unmeasured` until re-run — do not carry it forward as green.

Output: the triage `.md` with one row per item and a disposition. No
`pre-merge blocker` may be open when stage 5 commits.

---

## Stage 2 — Reconcile: detect superseded / zombie threads

The failure this heals: closing or extending a thread whose premise the
repo already moved past. Two shapes:

- **Superseded** — another thread/ADR/commit landed the same decision or
  obsoleted this hop's goal while Codex ran. The work may be redundant or
  conflicting.
- **Zombie** — `thread.json` says `active` but the worktree state contradicts
  it: the branch HEAD has moved past the handback's claimed `head`, the
  claimed `base_commit` is no longer an ancestor of the branch, or the
  worktree was merged/retired and the registry never caught up.

Checks (all read-only):

```bash
# Zombie: does the branch actually contain the SHAs thread.json records?
git -C "$WT" merge-base --is-ancestor <claimed-base> "$BR" \
  && echo "base OK" || echo "ZOMBIE: base not on branch"
git -C "$WT" rev-parse "$BR"   # == thread.json codex_worktrees[].head ?

# Superseded: did a sibling thread land the same contract since this hop opened?
python3 ~/.claude/skills/threads/scripts/index_threads_research.py --print \
  | grep -iE "<this hop's key nouns>"     # same ADR area / block / decision
git -C "$WT" log --oneline --since=<hop-open-date> -- <the files this hop owns>
# A frozen batch view of stale/blocked/superseded threads (writes a dated file):
python3 ~/.claude/skills/threads/scripts/status_review.py .threads --output /dev/stdout
```

Heal actions:

- **Zombie** → fix the registry before anything else: correct
  `codex_worktrees[].{base_commit,head,status,merged_*}` from the real
  `git` SHAs, re-run `index_threads_research.py`. If the worktree was
  already merged/retired, set `status` accordingly and **stop** — do not
  author a next hop on a dead worktree. Cross-machine divergence →
  `references/cross-machine-reconciliation.md`.
- **Superseded** → set the plan hop / thread `status: superseded`, point
  `outcome` at the thread that won, and route any salvageable work as a
  follow-up against the winner. Do not commit superseded work as if live.

Only when both come back clean does the thread proceed to stages 3–6.
**Flag, don't silently proceed:** if a check is ambiguous, write the
finding into the triage doc and surface it to the user rather than
guessing the thread is alive.

---

## Stage 3 — Scrub identifier shorthand before writing

The failure this heals: a shorthand you coin in a doc/commit/ADR
(a scratch name, a slug, an abbreviation) that **collides with a real
core/block/entity name**, so a future agent greps the token and lands on
unrelated source — or the live-docs rule (global CLAUDE.md #13) is
violated by a retired name surviving in the working tree.

Build the collision set once per project, then check every token you are
about to *write*:

```bash
# Canonical IDs the shorthand must not shadow (socks/gps_design example):
grep -oE '"(PL|PS)\.[A-Z0-9a-z]+"' shared-interfaces.json | sort -u   # block IDs
ls socks/modules/*/src/*_axi.vhd | sed 's#.*/##;s/\.vhd//'            # real core entities
# For each shorthand token TOK you introduce in the doc/ADR/commit:
git grep -n -w "$TOK" -- '*.vhd' '*.py' '*.json'   # does it already mean something?
```

Heal action: if a coined token resolves to a real symbol, rename the
shorthand (qualify it, prefix the thread slug, or spell it out). Retired
names go only in git history, never in the working-tree doc you are about
to commit. This is a *write-time* gate — run it before stage 4 authors
ADRs and before stage 5 stages the commit, not after.

---

## Stage 4 — Author ADRs with provenance-checked citations

ADR-grade decisions the hop surfaced get written to the durable decision
log (per-block `decisions.md` → `ADR-PL-<BLOCK>-NNN`; system →
`docs/decision-log.md` → `ADR-NNN`). Promote only when all four hold:
real alternatives, rippling consequences, a future agent could reach the
wrong conclusion without the why, and evidence exists.

**Heal check — every citation resolves.** An ADR is worthless if its
provenance is a dangling pointer. For each citation, verify the referent
exists *and* says what the ADR claims it says:

```bash
# Findings / research / spec paths cited in the ADR:
for p in <cited-paths>; do test -e "$p" && echo "OK $p" || echo "DANGLING $p"; done
# Commit SHAs cited as evidence:
for s in <cited-shas>; do git cat-file -t "$s" >/dev/null 2>&1 \
  && echo "OK $s" || echo "MISSING $s"; done
# Literature: the PDF/quote must be in the repo's research tree, not from memory.
ls .research/**/pdfs/ 2>/dev/null
```

A dangling citation is a STOP: find the real referent or delete the
claim. Never cite a paper, SHA, or findings doc you have not opened in
this session — reconstruct from committed evidence only, the same rule
the retroactive handback uses.

---

## Stage 5 — Close the plan / author the next hop

Mechanics: **Close thread** (if the thread is done) or **New plan hop**
(if it continues) in `workflows.md`. Write the closed hop's `outcome`
with the worktree pointer convention from `codex-handback.md`
§Lifecycle (`handback: codex-handoff/<plan-id>/... on branch <BR> at
<sha>`). If the next hop launches Codex, draft its plan file now (the
plan file *is* the launch prompt) — but emit the packet in stage 6,
after the commit lint, so the packet references committed SHAs.

---

## Stage 6 — Pre-commit lint (commit message + commands)

The failure this heals: a commit or a cleanup command that a guard
rejects *mid-workflow*, after you have already staged — or worse, one
that the fingerprint guard blocks only at push, after the SHA is cut.
Lint **before** running. The guards match on the literal command/text
string, not on intent, so a safe command containing a blocked substring
is still rejected.

**Bash-safety guard** (`~/.claude/hooks/bash-safety-guard.sh`) — the
cleanup/merge commands a handback cycle tends to emit, and their rewrites:

| Blocked pattern | Rewrite that passes |
|-----------------|---------------------|
| `rm -rf <dir>` / `rm -r` / `rm -f` | remove files individually, or let the run-dir live under the module's `build/` and leave it; never recursive-force |
| `git reset --hard` | `git stash` or commit first |
| `git clean -f` | `git clean -n` to preview, then remove named files |
| `git branch -D <b>` | `git branch -d <b>` (safe), or delete manually |
| `git push --force` | `git push --force-with-lease` |
| redirect under `/tmp`, `/etc`, `/var`, … | keep scratch **inside the repo** (`<module>/build/…`); project-local `rm`/`find` is exempted only when the target is in-repo |
| a `grep`/`echo`/commit body that merely *contains* `rm -rf`, `reset --hard`, etc. | store the literal in a variable, use a character class (`rm -r[f]`), or reword the prose — the matcher cannot tell a quoted string from a command |

**Fingerprint guard** (`~/.claude/hooks/git-fingerprint-guard.py`, runs
on commit/push) — staged content is scanned for identity strings (git
`user.name`/`user.email`), secrets (AWS keys, tokens), PII, and absolute
home paths; **all findings BLOCK**. Lint the message and the diff:

```bash
# Repo-relative paths only — no absolute /home/<user>/… in message or code.
git diff --cached | grep -nE '/home/[a-z]+/|/Users/[a-z]+/' && echo "FINGERPRINT RISK"
# No real names / emails in the commit body (use the Co-Authored-By trailer only).
```

Rewrite absolute paths to repo-relative, drop machine/user names, move
any secret to an ignored file. Commit message follows global CLAUDE.md
#14: imperative subject (~70 chars), 1–2 short paragraphs of *why*, and a
`Verification:` trailer listing the exact commands run. Only commit/push
when the user has asked; branch first if on the default branch.

---

## Stage 7 — Emit the next kickoff packet (and verify it survived the terminal)

Mechanics: `scripts/emit_codex_launch_packet.py --plan-id <next>` writes
`codex-handoff/<plan-id>/prompt.md`. If instead you hand the user a
copy-paste kickoff block in chat, the failure to heal is **terminal
truncation** — a long fenced block clipped mid-line pastes as a corrupt
prompt that silently drops constraints.

Heal check — confirm the packet is whole:

```bash
P=<worktree>/codex-handoff/<plan-id>/prompt.md
wc -l "$P"; tail -3 "$P"          # last lines present and not cut mid-sentence
grep -cE '^`{3}' "$P"             # count fence lines; expect an even number
```

For a chat-delivered block, end it with an explicit sentinel line (e.g.
`--- END KICKOFF ---`) and confirm that sentinel is visible in the
rendered output; if it is not, the block was truncated — resend it as a
file via `SendUserFile` instead of inline.

---

## Report card — report each step's outcome

A lifecycle run is not done until you report, one line per stage, what
happened and the heal verdict. Template:

```text
Handback lifecycle — <thread-id> <plan-id>
  0 ground truth   : branch HEAD <sha>; claimed head <sha>; registry <sha>  [match|MISMATCH]
  1 triage         : <n> items — <p> pre-merge / <f> follow-up / <a> as-is  [blockers cleared|OPEN]
  2 reconcile      : superseded=<no|thread>; zombie=<no|fixed:what>          [LIVE|SUPERSEDED|DEAD]
  3 scrub          : <n> shorthand tokens checked; collisions=<none|renamed:…>
  4 ADRs           : <n> authored; citations <n>/<n> resolved               [clean|DANGLING fixed]
  5 close/launch   : plan <closed|hop-NN authored>; outcome pointer written
  6 commit lint    : bash-safety <pass|rewrote:…>; fingerprint <pass|rewrote:…>; commit <sha>
  7 packet         : prompt.md <lines>, fences balanced, sentinel present    [whole|RESENT as file]
```

Any stage that triggered a heal reports *what* it repaired, not just
"pass" — the repair is the signal. If a stage stopped the cycle
(zombie/dead worktree, dangling citation, open pre-merge blocker),
report the stop and what the user must decide; do not paper over it to
reach a green card.
