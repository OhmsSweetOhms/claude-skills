---
name: fingerprint
description: "Scan a directory or git repo for PII, secrets, and digital fingerprint material. Use when the user asks to scan for secrets, check for PII, audit a repo for personal information, or verify a project is clean before publishing. Also use when the user says fingerprint, privacy check, or secret scan."
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

## Relationship to Git Hooks

The `git-fingerprint-guard.py` PreToolUse hook automatically gates
`git add`, `git commit`, and `git push`. It imports the same
`fingerprint_engine.py` so patterns are always in sync.

The `fingerprint_scan.py` tool handles `--scan-dir` and `--scan-tree`
for auditing repos that predate the hooks or for bulk scanning.

Performance: ~800 files in ~2 seconds.
