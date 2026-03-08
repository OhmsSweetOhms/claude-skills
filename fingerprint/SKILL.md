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
    |                  |
    imports            imports
    |                  |
git-fingerprint-guard.py        fingerprint_scan.py
(hook: --scan-stdin)            (tool: --scan-dir, --scan-tree)
~/.claude/hooks/                ~/.claude/skills/fingerprint/
```

Both entry points use the same engine. Update patterns in one place.

## When to Use

- User asks to scan for secrets, PII, or personal information
- User asks to audit a repo before publishing or open-sourcing
- User asks to check if a project is clean / safe to share
- User says "fingerprint", "privacy check", "secret scan"
- Before first push of a new repo
- When onboarding an existing codebase that predates the git hooks

## How to Run

### Single repo scan
```bash
python3 ~/.claude/skills/fingerprint/fingerprint_scan.py --scan-dir /path/to/repo
```

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

**Secrets (all blocked):**
- Private keys (RSA, DSA, EC, OpenSSH, PGP)
- Cloud API keys (AWS, GCP, GitHub, GitLab, Slack, OpenAI)
- Generic API keys, passwords, tokens, secrets in assignments
- Database connection strings with credentials
- JWT tokens, OAuth tokens
- SSH public keys, .netrc entries

**Personal Identifiers (all blocked):**
- Auto-detected: `$USER`, hostname, git config name/email, home dir
- Additional patterns from `~/.claude/hooks/fingerprint-identity.txt`
- Email addresses, street addresses
- IP addresses (non-private), MAC addresses

**Fingerprint Material (all blocked):**
- Absolute paths containing usernames (`/home/user/`, `/Users/user/`, `/media/user/`)
- Machine hostnames in file content
- Author/copyright lines with real names (must use OhmsSweetOhms)

**Disabled rules:**
- Phone numbers -- removed because VHDL/HDL numeric constants (e.g. `4294967296`,
  `2147483648`) triggered massive false positives across every FPGA project

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
Additional identity strings to block, one per line. Variations, typos,
nicknames, aliases. Auto-detected values don't need to be listed.

**Project allowlist** (`.fingerprint-allowlist` in project root):
One regex per line. Patterns are matched against **line content** (not
filenames). Lines matching any pattern are skipped. Use for intentional
test fixtures or example values only.

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
   /home/username/project/path==>.
   /media/username/drive/path==>.
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
echo "/home/user/path==>./" > /tmp/replacements

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

The `git-fingerprint-guard.py` PreToolUse hook automatically gates
`git add`, `git commit`, and `git push`. It imports the same
`fingerprint_engine.py` so patterns are always in sync.

The `fingerprint_scan.py` tool handles `--scan-dir` and `--scan-tree`
for auditing repos that predate the hooks or for bulk scanning.

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
