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

3. **Let the user pick** which repos to scrub.

4. **For each selected repo**, spawn a subagent to:
   - List findings with suggested fixes
   - Mark Vivado build artifacts for `.gitignore`
   - Apply fixes the user approves
   - Re-scan to verify CLEAN

### Suggested fix categories

| Finding Type | Suggested Fix |
|-------------|---------------|
| Username/hostname in Vivado reports | Add report files to `.gitignore` |
| Username in TCL scripts | Replace absolute paths with `[pwd]` or `$::env(HOME)` |
| Username in SV/Python paths | Replace with relative paths or `Path(__file__).parent` |
| Author/copyright real name | Replace with `OhmsSweetOhms` |
| Phone number (large integers) | Check if false positive (VHDL constants, math) |
| Email in third-party code | Add to `.fingerprint-allowlist` |

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
- Email addresses, phone numbers, street addresses
- IP addresses (non-private), MAC addresses

**Fingerprint Material (all blocked):**
- Absolute paths containing usernames (`/home/user/`, `/Users/user/`)
- Machine hostnames in file content
- Author/copyright lines with real names (must use OhmsSweetOhms)

## Output

The scanner reports each finding with:
- Category (SECRET, PII, IDENTITY, etc.)
- File and line number
- Masked match value (secrets are partially redacted)

Exit code 0 = clean, exit code 2 = findings that must be fixed.

All findings are logged to `~/.claude/hooks/fingerprint.log`.

## Configuration

**Identity file** (`~/.claude/hooks/fingerprint-identity.txt`):
Additional identity strings to block, one per line. Variations, typos,
nicknames, aliases. Auto-detected values don't need to be listed.

**Project allowlist** (`.fingerprint-allowlist` in project root):
One regex per line. Lines matching these patterns are skipped.
Use for intentional test fixtures or example values only.

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

1. **Commit or stash all working changes first** -- filter-repo refuses
   to run on a dirty tree.

2. **Scan history for PII** -- check every blob in every commit:
   ```bash
   for commit in $(git log --format="%H" --all); do
       git show $commit --name-only --format="" | while read f; do
           count=$(git show "$commit:$f" 2>/dev/null \
               | grep -cE 'IDENTITY_PATTERNS_HERE' 2>/dev/null)
           [ "$count" -gt 0 ] && echo "$commit  $count  $f"
       done
   done
   ```
   Also check author/committer: `git log --format="%an <%ae>" --all | sort -u`

3. **Create config files** in `/tmp/`:

   **mailmap** (author rewrite):
   ```
   New Name <new@email> Old Name <old@email>
   ```

   **replacements.txt** (content rewrite, `literal==>replacement`):
   ```
   /home/username/path/to/file==>relative/path
   old-hostname==>build-host
   ```

4. **Run filter-repo** with all three operations in one pass:
   ```bash
   python3 /tmp/git-filter-repo --force \
       --mailmap /tmp/mailmap \
       --replace-text /tmp/replacements.txt \
       --path secret-file.rpt --path another.rpt --invert-paths
   ```
   - `--mailmap`: rewrites author/committer
   - `--replace-text`: rewrites file content (literal or regex)
   - `--path ... --invert-paths`: deletes files from all history

5. **Verify** -- re-run the history scan from step 2 to confirm zero matches.
   Also check `git log --format="%an <%ae>" --all | sort -u`.

6. **Run `/fingerprint`** on the working tree to confirm it's still clean.

### Common PII sources in FPGA projects

| Source | Contains | Fix |
|--------|----------|-----|
| Vivado `.rpt` files | hostname, absolute paths, username | `--invert-paths` (delete from history) |
| TCL scripts (`synth.tcl`) | absolute paths to source files | `--replace-text` with relative paths |
| Symlinks in git | target paths with `/home/user/` | Content replacement won't help; these are fine if `.gitignore`d or local-only |
| Git author/committer | real name, personal email | `--mailmap` |
| Vivado `clockInfo.txt` | hostname | `--invert-paths` |

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

Performance: ~800 files in ~2 seconds.
