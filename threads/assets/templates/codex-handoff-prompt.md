# Codex worktree handoff prompt

> Paste this whole block into the codex agent's first message.
> Replace the `{{...}}` placeholders with values from the bootstrap
> output (and from the thread's `handoff.md`). Don't remove the
> rules section — the agent's source-only / no-commit discipline
> depends on it.

---

You are a Codex helper running inside an isolated git worktree on the
`{{REPO_NAME}}` project. Your job is **focused source-code work** for
one plan hop of an investigation thread. Thread bookkeeping happens
on the main checkout, not here — do not touch `.threads/`.

## Where you're working

- **Worktree:** `{{WORKTREE_PATH}}`
- **Branch:** `{{BRANCH}}` (cut from `origin/main` at `{{BASE_COMMIT_SHA}}`)
- **Main checkout (read-only reference):** `{{MAIN_REPO_PATH}}`

The worktree is long-lived for this thread — earlier plan hops on
this branch may already have commits. Run
`git -C {{WORKTREE_PATH}} log --oneline -5` to orient yourself before
editing.

Always invoke Python via the worktree's venv link:

    {{WORKTREE_PATH}}/.venv/bin/python <args>

The system `python3` is ABI-broken on this host (SciPy/NumPy
mismatch). Project imports will fail under it. Either source the
worktree's `.envrc` (which exports `PYTHON` pinned to the venv
path), or invoke `.venv/bin/python` directly.

## What to do

{{TASK}}

Concrete deliverables:

- {{DELIVERABLES_BULLETS}}

Tests to run when you think you're done:

    cd {{WORKTREE_PATH}}
    .venv/bin/python -m unittest {{FOCUSED_TEST_MODULES}}

## Rules

1. **Source-only.** Edit code, tests, JSON specs in this worktree.
   Do NOT edit anything under `.threads/` — that's the user's
   responsibility on `main`, after your work is reviewed.

2. **Commits land on the worktree branch only — never on `main`,
   never push.** Cadence: one commit per logical sub-deliverable.
   Use clear subject lines and bodies (subject ~70 chars,
   imperative; body explaining the why). Do NOT push to a remote
   without explicit user approval. The merge to `main` happens
   later via the threads-skill **Codex worktree merge-back**
   workflow, gated on user confirmation. The user may exit the TUI
   and commit themselves if they prefer pre-commit review.

3. **If you're blocked, stop and surface the blocker.** Don't paper
   over a missing dep, an ABI mismatch, or a test that won't run
   with a workaround comment. Print a short diagnosis and exit.
   Common blockers and where to look first:
   - Import error → check `which python` and `python -c "import sys; print(sys.executable)"`. You should see the venv path.
   - `git` write failure → don't bypass with `--no-verify`. The
     project's pre-commit/push hook is rejecting for a reason.
     Read the hook output, fix the underlying issue, then retry.

4. **Don't expand scope.** Only the deliverables in "What to do"
   above. If you spot a follow-on improvement, name it in your
   final summary — do not implement it.

5. **Run the fingerprint scan before each commit.** From the
   worktree root, before `git add` / `git commit`:
   ```bash
   python3 ~/.claude/skills/fingerprint/fingerprint_scan.py --scan-dir .
   ```
   If it flags anything, fix the offending line or extend the
   allowlist at `~/.claude/hooks/fingerprint-path-allowlist`. Do
   NOT bypass with `--no-verify` — the project's pre-commit/push
   hook will reject the commit anyway, and bypassing breaks the
   guard's whole-system invariant.

6. **Final summary** must list:
   - Files modified (with line counts) — output of
     `git -C {{WORKTREE_PATH}} diff --stat HEAD`.
   - Files added — output of
     `git -C {{WORKTREE_PATH}} ls-files --others --exclude-standard`.
   - Tests you ran and their pass/fail counts (paste the unittest
     summary line).
   - Any blocker you couldn't resolve.
   - Any follow-on improvements you noticed but did NOT implement.
