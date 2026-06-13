---
name: fingerprint
description: "Scan a directory or git repo for PII, secrets, and digital fingerprint material. Use when the user asks to scan for secrets, check for PII, audit a repo for personal information, or verify a project is clean before publishing. Also use when the user says fingerprint, privacy check, or secret scan. Use for scrubbing git history of PII, rewriting author info, or cleaning committed secrets with git-filter-repo."
---

# Fingerprint Scanner

Scan a directory or git repository for PII, secrets, credentials, and
digital fingerprint material that could identify the developer.

## Architecture

```
fingerprint_engine.py              <- shared core (patterns, Scanner, identity, allowlist)
    |             |             |
    imports       imports       imports
    |             |             |
git-fingerprint-guard.py   commit-msg            fingerprint_scan.py
(Claude PreToolUse hook)   (git-native hook)     (tool: scan-{dir,tree,commits,unpushed})
~/.claude/hooks/           ~/.claude/hooks/      ~/.claude/skills/fingerprint/
                           git-hooks/
```

Three entry points share one engine. Update patterns once.

### Defense layers — what catches what

| Entry path                        | PreToolUse (Claude)   | commit-msg (git-native)  | --scan-commits (audit) |
|-----------------------------------|-----------------------|--------------------------|------------------------|
| `git commit -m "msg"` from Claude | ✅ shlex parser       | ✅ scans message file    | retrospective only     |
| `git commit -F file` from Claude  | ✅ reads file content | ✅                       | retrospective only     |
| `git commit` (editor) from Claude | ❌ no msg yet         | ✅                       | retrospective only     |
| `git commit --amend`              | ❌ no msg yet         | ✅                       | retrospective only     |
| `git merge` / `git rebase`        | ❌ git authors msg    | ✅                       | retrospective only     |
| Codex / external tool / user CLI  | ❌ no Claude hook     | ✅ (git itself enforces) | retrospective only     |
| `git commit --no-verify`          | ❌                    | ❌ (explicit bypass)     | retrospective catches  |

The `commit-msg` git hook is the bulletproof layer because git enforces it
regardless of which tool authored the commit. Installed globally via
`git config --global core.hooksPath ~/.claude/hooks/git-hooks/`.

## When to Use

- User asks to scan for secrets, PII, or personal information
- User asks to audit a repo before publishing or open-sourcing
- User asks to check if a project is clean / safe to share
- User says "fingerprint", "privacy check", "secret scan"
- Before first push of a new repo
- When onboarding an existing codebase that predates the git hooks

## How to Run

### Single repo scan (working tree)
```bash
python3 ~/.claude/skills/fingerprint/fingerprint_scan.py --scan-dir /path/to/repo
```

### Commit-message scans (history audit)

The working-tree scan only sees current files. To audit commit message
bodies (which can carry leaks like absolute paths in `Verification:`
trailers), use:

```bash
# All reachable commits (every ref)
python3 ~/.claude/skills/fingerprint/fingerprint_scan.py --scan-commits /path/to/repo

# Only commits not yet pushed to upstream
python3 ~/.claude/skills/fingerprint/fingerprint_scan.py --scan-unpushed /path/to/repo
```

Use `--scan-unpushed` as the natural pre-push check; use `--scan-commits`
for retrospective audits or to catch leaks that snuck through before the
`commit-msg` hook was installed. Findings include the short commit SHA
and subject so you can `git show <sha>` immediately.

If `--scan-commits` finds leaks, the fix path is `git filter-repo
--replace-message <table>` — see "Git History Scrubbing" below.

### Multi-repo tree scan (interactive)
For a parent directory containing multiple repos:

1. **Scan the tree** (returns JSON with repo map, git status, findings):
   ```bash
   python3 ~/.claude/skills/fingerprint/fingerprint_scan.py --scan-tree /path/to/dir
   ```

2. **Display the map** -- show the user a hierarchy with git status:
   ```
   /path/to/dir/
   ├── project-a/     [GIT] master, 2 modified    42 files, 3 findings
   ├── project-b/     [GIT] main, CLEAN TREE       18 files, CLEAN
   └── loose-file.md  (not in any repo)
   ```

3. **For repos with findings**, launch background Explore agents (one per
   repo) to read the flagged files and classify each finding:
   - What the PII is (absolute path, username, hostname, report file)
   - Where exactly it is (file, line, surrounding context)
   - Suggested fix (relative path, gitignore, filter-repo)
   - Whether it's a false positive

   Agents are read-only -- they research and report back, nothing more.

4. **Main conversation reviews agent results** and presents a fix plan
   to the user. Group fixes by category:
   - Working tree fixes (path replacements, gitignore updates)
   - Files to remove from tracking (`git rm --cached`)
   - History scrub operations (mailmap, replace-text, invert-paths)

5. **Apply fixes in the main conversation** after user approval:
   - Fix absolute paths in working tree files
   - Add Vivado reports to `.gitignore`
   - Commit clean working tree
   - Run `git-filter-repo` for history scrub
   - Re-scan to verify CLEAN

### Suggested fix categories

| Finding Type | Suggested Fix |
|-------------|---------------|
| Username/hostname in Vivado reports | Add report files to `.gitignore`, remove from tracking |
| Username in TCL scripts | Replace absolute paths with `[file dirname [file normalize [info script]]]` |
| Username in Python paths | Replace with `Path(__file__).parent` or relative paths |
| Username in shell scripts | Use `SIM_DIR="$(cd "$(dirname "$0")" && pwd)"` pattern |
| Author/copyright real name | Replace with `OhmsSweetOhms` |
| Email in third-party code | Skip -- 3rd-party dirs are auto-excluded |

## What It Checks

**Barebones scope (deliberate).** The broad PII/secret battery was cut after
its false positives on technical writing cost more (in diagnose-and-retry
churn) than the rare real catch. What remains is three high-signal classes:
identity tokens, a few secret/path tripwires, and the git author/committer
**persona identity check** — the metadata surface that content scans cannot
see and the real leak vector. See the `fingerprint_engine.py` module
docstring for the rationale.

**Personal identifiers (all blocked) -- the primary guard:**
- A fixed set of identity tokens read from
  `~/.claude/hooks/fingerprint-identity.txt` (the PRIVATE config file; the
  engine source is public and holds no tokens).
- Case-insensitive **substring** match by default; a token prefixed with
  `=` matches on **word boundaries** instead (so a short name part doesn't
  fire as a substring of a common word).
- This is a complete identity fingerprint: home path, username, email
  local-part, and real-name parts are all substrings of those tokens, so a
  `/home/<user>/` absolute path is caught by the username token.
- There is **no** `$USER`/hostname/git-config auto-detection any more --
  the token set is exactly what the file declares (predictable; blocks only
  on a real leak).

**Content tripwires (all blocked):**
- Private keys (`-----BEGIN ... PRIVATE KEY-----`, all variants)
- AWS access key IDs (`AKIA…`)
- Quoted secret/token/password/api-key assignments (`secret = "…"`)
- Absolute-path mount prefixes (`/home/`, `/Users/`, `/media/`, `C:\Users\`) —
  kept because global Core Rule 15 mandates them, and they trip on **any**
  username, not just ours.

**Git author/committer persona check (all blocked):** every commit (on
`git commit`) and every unpushed commit (on `git push`) must be authored and
committed by the persona (`OhmsSweetOhms` + its non-leaking emails) — a
real name/email in commit metadata is blocked. This is the surface content
and message scans never see.

**Removed (no longer scanned):** the vendor key-prefix battery (GCP, GitHub,
GitLab, Slack, OpenAI, JWT, OAuth, DB URLs, SSH pubkeys, .netrc), standalone
email/MAC/street-address regexes, the author/copyright real-name heuristic,
and the long-disabled phone/IP rules. Add anything you still want caught as an
explicit token in `fingerprint-identity.txt`.

## Output

The scanner reports each finding with:
- Category (SECRET, PII, IDENTITY, etc.)
- File and line number
- Masked match value (secrets are partially redacted)

Exit code 0 = clean, exit code 2 = findings that must be fixed.

All findings are logged to `~/.claude/hooks/fingerprint.log`.

## 3rd-Party Code

The scanner automatically skips directories named: `tools`, `vendor`,
`third_party`, `thirdparty`, `extern`, `external`, `node_modules`,
`.venv`, `venv`. Repos under these directories are excluded from
`--scan-tree` results. This prevents false positives from code you
don't control (IDE plugins, library sources, etc.).

The `THIRD_PARTY_DIRS` set is defined in `fingerprint_engine.py` and
used by `find_git_repos()` to prune the directory walk.

## Configuration

**Identity file** (`~/.claude/hooks/fingerprint-identity.txt`):
The **sole** source of identity tokens (no auto-detection). One token per
line; plain = substring, `=token` = word-boundary. Include variations,
typos, nicknames, aliases. Keep real tokens here only -- never in the
public engine source.

**Gitignore** (`.gitignore` in project root):
Files matching `.gitignore` patterns are automatically excluded from
scanning. For git repos this uses `git check-ignore --no-index` for
accurate matching (including tracked files that were later gitignored).
For non-git directories, patterns are matched with fnmatch.

**Project allowlist** (`.fingerprint-allowlist` in project root):
One regex per line. Patterns are matched against **line content** (not
filenames). Lines matching any pattern are skipped. Use for intentional
test fixtures or example values only.

**Path allowlist** (`.fingerprint-path-allowlist` in project root or
`~/.claude/hooks/fingerprint-path-allowlist` globally):
One fnmatch-style glob per line. Matching files skip the secret-tripwire
rules while identity-pattern checks still run. Use this for third-party
reference material such as `.research/**`.

**Known limitation:** The `.fingerprint-allowlist` file itself is not
excluded from scanning. Avoid putting literal scannable values (long
digit sequences, email addresses) in the allowlist -- use regex
character classes or anchored patterns instead.

## Git History Scrubbing

The working-tree scanner and hooks only protect new commits. PII already
baked into git history requires `git-filter-repo` to rewrite.

### Prerequisites

```bash
# git-filter-repo is a standalone Python script -- no pip needed
curl -sL https://raw.githubusercontent.com/newren/git-filter-repo/main/git-filter-repo \
    -o /tmp/git-filter-repo && chmod +x /tmp/git-filter-repo
```

### Workflow

1. **Fix PII in working tree first** -- replace absolute paths, fix
   TCL scripts, update Python files. Then commit so the tree is clean.

2. **Check author/committer:**
   ```bash
   git log --format="%an <%ae>" --all | sort -u
   ```

3. **Create config files** in `/tmp/`:

   **mailmap** (author rewrite):
   ```
   OhmsSweetOhms <noreply@OhmsSweetOhms> old-name <old@email.com>
   ```

   **replacements.txt** (content rewrite, `literal==>replacement`):
   ```
   /ABSOLUTE/HOME/PATH/PREFIX==>.
   /ABSOLUTE/MEDIA/PATH/PREFIX==>.
   old-hostname==>build-host
   ```

4. **Run filter-repo** with all operations in one pass:
   ```bash
   python3 /tmp/git-filter-repo --force \
       --mailmap /tmp/mailmap \
       --replace-text /tmp/replacements.txt \
       --path secret-file.rpt --path another.rpt --invert-paths
   ```
   - `--mailmap`: rewrites author/committer in all commits
   - `--replace-text`: rewrites file content (literal or regex)
   - `--path ... --invert-paths`: deletes files from all history

5. **Verify:**
   ```bash
   git log --format="%an <%ae>" --all | sort -u
   python3 ~/.claude/skills/fingerprint/fingerprint_scan.py --scan-dir .
   ```

### Batch scrubbing multiple repos

When cleaning many repos at once, create a shared mailmap and
replacements file, then loop:

```bash
echo "OhmsSweetOhms <noreply@OhmsSweetOhms> old-name <old@email>" > /tmp/mailmap
echo "/ABSOLUTE/REPO/PATH==>./" > /tmp/replacements   # use your real absolute path prefix

for repo in repo1 repo2 repo3; do
    cd "$repo"
    python3 /tmp/git-filter-repo --force \
        --mailmap /tmp/mailmap \
        --replace-text /tmp/replacements
    cd ..
done
```

### Common PII sources in FPGA projects

| Source | Contains | Fix |
|--------|----------|-----|
| Vivado `.rpt` files | hostname, absolute paths, username | `--invert-paths` (delete from history) |
| Vivado `clockInfo.txt` | hostname | `--invert-paths` |
| TCL scripts (`synth.tcl`) | absolute paths to source files | `--replace-text` or fix in working tree |
| Python test scripts | hardcoded absolute paths | Fix in working tree + `--replace-text` |
| Git author/committer | real name, personal email | `--mailmap` |
| Deprecated doc files | may contain PII in examples | `--invert-paths` to remove from history |

### Notes

- `git-filter-repo` rewrites all commit SHAs. Only use on repos with no
  shared remote, or coordinate with all collaborators.
- Always commit working changes before running -- filter-repo operates on
  committed history only.
- The `--force` flag is needed if the repo has a remote configured or if
  filter-repo has been run before.
- filter-repo removes the `origin` remote as a safety measure. Re-add it
  after verifying the scrub: `git remote add origin <url>`.

## Relationship to Git Hooks

Two hooks share the engine:

1. **`git-fingerprint-guard.py`** -- a Claude Code PreToolUse hook that
   gates `git add`, `git commit`, and `git push` invocations made from
   inside Claude Code sessions. Catches early but is invisible to git
   commands run by other tools (e.g. Codex) or by the user in their
   own shell.

2. **`commit-msg`** -- a git-native hook (`~/.claude/hooks/git-hooks/commit-msg`)
   installed globally via `git config --global core.hooksPath
   ~/.claude/hooks/git-hooks/`. Runs inside git's own commit flow on
   every commit, regardless of which tool launched it. This is the
   authoritative layer.

The `fingerprint_scan.py` tool handles `--scan-dir`, `--scan-tree`,
`--scan-commits`, and `--scan-unpushed` for ad-hoc audits.

### Installing the commit-msg hook on a new machine

```bash
# 1. Hook script is in ~/.claude/hooks/git-hooks/commit-msg
ls ~/.claude/hooks/git-hooks/commit-msg

# 2. Point git globally at the directory
git config --global core.hooksPath ~/.claude/hooks/git-hooks/

# 3. (Optional) Drop a per-repo backup so the hook still works if
#    core.hooksPath is later disabled or pointed elsewhere
cp ~/.claude/hooks/git-hooks/commit-msg /path/to/repo/.git/hooks/commit-msg
```

**Note:** `core.hooksPath` is a single-path setting -- once set, git
ignores per-repo `.git/hooks/` entirely. If you previously had custom
hooks in any repo, copy them into `~/.claude/hooks/git-hooks/` too or
they'll stop firing.

### When the hook blocks (agent guidance)

The hook is a PreToolUse interceptor: when it detects fingerprint
material, it blocks the entire Bash invocation **before any of the
chained commands run**. Practical implications for an agent retrying
after a block:

- **Don't assume your prior `git add` persisted.** Even though the
  hook itself is read-only (`git diff --cached`), if you chained
  `git add … && git commit …` in one Bash call, the chain was
  intercepted before either ran in some block scenarios. After fixing
  the flagged content, always run `git status --short` first, then
  **re-stage every file you intended to commit** — don't trust that
  the index still reflects your earlier intent.
- **Re-run the fingerprint scan on the modified file before retrying.**
  Cheap insurance that your fix actually addressed the violation.
- **Multi-file commits split if you only re-stage the flagged file.**
  Common foot-gun: hook blocks on file A; you fix A, re-stage A only,
  commit. Files B/C/D that you originally intended to ride along get
  left behind, requiring follow-up commits. Re-stage the full original
  set or use a single `git add` of all paths together.

### Most common foot-gun: absolute filesystem paths in commit messages

The single most common cause of agent-driven blocks is writing
absolute filesystem paths into commit message bodies, session-log
entries, or any tracked content (`/home/<user>/...`,
`/media/<user>/...`, etc.). The username component trips the identity
check. **Always use repo-relative paths** in commit messages and
tracked docs — `../<sibling-dir>` for worktrees, `.threads/...` for
thread paths, etc. This rule is also stated in the user's global
CLAUDE.md but bears repeating here because the hook enforces it
loudly and recovery is multi-step.

## Operational Notes

- **Agents scan, main conversation fixes** -- use background Explore
  agents for read-only investigation (reading flagged files, classifying
  findings, suggesting fixes). All edits, git operations, commits, and
  filter-repo work must happen in the main conversation where
  Bash/Edit/Write permissions are available.
- **Fix working tree before history** -- always fix and commit PII in
  the current tree first, then run filter-repo. This avoids committing
  dirty files that filter-repo can't see.
- **Batch is faster** -- for multi-repo cleanups, create shared mailmap
  and replacements files and loop, rather than crafting per-repo configs.
- **Re-scan after filter-repo** -- filter-repo can leave behind
  artifacts in the working tree. Always re-scan to confirm clean.

Performance: ~800 files in ~2 seconds.
