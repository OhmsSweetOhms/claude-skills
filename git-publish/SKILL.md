---
name: git-publish
description: >
  Source control ~/.claude with a dual-repo strategy: a private GitHub repo for
  personal config (settings, hooks, memory) and a public repo for shareable
  skills. Handles SSH deploy key generation, git-filter-repo identity scrubbing,
  git submodule wiring, fingerprint-safe commits, and day-to-day push/pull
  workflows. Use this skill whenever the user wants to set up git for their
  Claude Code config, publish skills publicly, back up their ~/.claude directory,
  sync Claude settings across machines, scrub PII from commit history, or manage
  the public/private repo split. Also triggers on "git publish", "push skills",
  "sync claude config", "new machine setup", or "scrub identity".
---

# git-publish

Set up and maintain dual-repo source control for `~/.claude`: a **private**
repo for personal configuration and a **public** repo for shareable skills.

## Architecture

```
~/.claude/                          ← private repo (claude-config)
├── .gitignore
├── .gitmodules                     ← points to public skills submodule
├── .fingerprint-allowlist
├── CLAUDE.md
├── settings.json
├── hooks/                          ← hook scripts (private)
├── projects/*/memory/              ← tracked via git add --force
└── skills/                         ← public repo (claude-skills) as submodule
    ├── .gitignore
    ├── socks/
    ├── fingerprint/
    └── ...
```

## When to Use

- User wants to set up git tracking for `~/.claude`
- User wants to publish skills to GitHub
- User wants to back up or sync config across machines
- User wants to scrub PII/identity from commit history
- User says "git publish", "push skills", "sync config", "new machine setup"
- User wants to add a deploy key or fix SSH authentication for the repos

## Prerequisites

Before starting, check what's already in place:

```bash
# Check existing state
ls ~/.claude/.git 2>/dev/null && echo "Private repo exists" || echo "No private repo"
cd ~/.claude/skills && git remote -v 2>/dev/null || echo "No skills remote"
ls ~/.ssh/claude-*-deploy 2>/dev/null || echo "No deploy keys"
git filter-repo --version 2>/dev/null || echo "No git-filter-repo"
```

If everything exists, skip to **Day-to-Day Workflow**. If partially set up, pick
up from the appropriate step. If nothing exists, start from Step 0.

## Step 0: Install git-filter-repo

Only needed once. Required for identity scrubbing.

```bash
mkdir -p ~/.local/bin
curl -sL https://raw.githubusercontent.com/newren/git-filter-repo/main/git-filter-repo \
    -o ~/.local/bin/git-filter-repo && chmod +x ~/.local/bin/git-filter-repo
export PATH="$HOME/.local/bin:$PATH"
git filter-repo --version
```

## Step 1: SSH Deploy Keys

Generate **repo-scoped** SSH keypairs — one per repo. More secure than a
personal key (limits blast radius if compromised).

```bash
mkdir -p ~/.ssh && chmod 700 ~/.ssh
ssh-keygen -t ed25519 -f ~/.ssh/claude-skills-deploy -N "" -C "deploy:claude-skills"
ssh-keygen -t ed25519 -f ~/.ssh/claude-config-deploy -N "" -C "deploy:claude-config"
```

Append host aliases to `~/.ssh/config`:

```
Host github-skills
    HostName github.com
    User git
    IdentityFile ~/.ssh/claude-skills-deploy
    IdentitiesOnly yes

Host github-config
    HostName github.com
    User git
    IdentityFile ~/.ssh/claude-config-deploy
    IdentitiesOnly yes
```

```bash
chmod 600 ~/.ssh/config
```

**User action required:** Display the public keys and instruct the user to add
each as a **read-write deploy key** on the corresponding GitHub repo:

```bash
cat ~/.ssh/claude-skills-deploy.pub   # → GitHub repo Settings → Deploy keys
cat ~/.ssh/claude-config-deploy.pub   # → same for config repo
```

Verify connectivity before proceeding:
```bash
ssh -T github-skills 2>&1
ssh -T github-config 2>&1
```

## Step 2: Create GitHub Repos

The user creates these in their browser (or via `gh` CLI if installed):

1. `<username>/claude-skills` → **Public**, empty (no README)
2. `<username>/claude-config` → **Private**, empty (no README)

Ask the user for their GitHub username — use it everywhere instead of
hardcoding. The username also becomes the git author identity.

## Step 3: Prepare and Push Public Skills Repo

The skills/ directory likely already has a git repo with history. The goal is to
scrub any real identity from the history and push it to the public repo.

### 3a. Full backup

```bash
cd ~/.claude
rsync -a --exclude .git skills/ skills-full-backup/
```

### 3b. Commit outstanding changes

```bash
cd ~/.claude/skills
git add -A
git commit -m "Commit outstanding changes before identity rewrite"
```

### 3c. Add .gitignore

```bash
cat > .gitignore << 'EOF'
socks-workspace/
*.zip
.claude/
__pycache__/
EOF
git add .gitignore
git commit -m "Add .gitignore for build artifacts and caches"
```

### 3d. Rewrite commit history (identity scrub)

Create a mailmap and rewrite all commits:

```bash
cat > /tmp/mailmap << EOF
<USERNAME> <<USERNAME>@users.noreply.github.com> <OLD_NAME> <<OLD_EMAIL>>
EOF

export PATH="$HOME/.local/bin:$PATH"
git filter-repo --force --mailmap /tmp/mailmap
```

Replace `<USERNAME>`, `<OLD_NAME>`, `<OLD_EMAIL>` with actual values. Discover
old identity with:
```bash
git log --format="%an <%ae>" --all | sort -u
```

Set local identity for future commits:
```bash
git config user.name "<USERNAME>"
git config user.email "<USERNAME>@users.noreply.github.com"
```

### 3e. Verify scrub

```bash
git log --format="%an <%ae>" --all | sort -u
# Should show only the new identity

git for-each-ref --format="%(refname) %(taggername) <%(taggeremail)>" refs/tags
# Should be empty or show only the new identity
```

### 3f. Push

```bash
git remote add origin git@github-skills:<USERNAME>/claude-skills.git
git branch -M main
git push -u origin main
```

### 3g. Restore untracked content

```bash
cp -rn ~/.claude/skills-full-backup/socks-workspace ~/.claude/skills/ 2>/dev/null || true
cd ~/.claude/skills && git status  # verify everything gitignored properly
```

## Step 4: Initialize Private Root Repo

### 4a. Write .gitignore

The private repo ignores everything ephemeral and sensitive. Only config,
hooks, and memory are tracked. Project memory files must be force-added
since `projects/` is fully ignored.

```gitignore
# Credentials & secrets
.credentials.json

# Session & history (ephemeral, large)
history.jsonl
session-env/
sessions/
file-history/
backups/
shell-snapshots/

# Cache & temp
cache/
paste-cache/
stats-cache.json
mcp-needs-auth-cache.json
workspace/

# Debug & telemetry (large, ephemeral)
debug/
telemetry/

# Tasks, plans, todos (conversation-scoped)
tasks/
todos/
plans/

# Downloads (ephemeral)
downloads/

# Plugins (managed by Claude Code)
plugins/

# Hook logs (contain PII, massive)
hooks/fingerprint.log
hooks/blocked.log
hooks/__pycache__/

# Project data — ignore everything, track only memory/ via git add --force
projects/

# Skills backup (temporary, from submodule conversion)
skills-full-backup/
skills-git-backup/
```

### 4b. Write .fingerprint-allowlist

The fingerprint guard will block commits containing paths with the username.
Since this is a private repo, allow these patterns:

```
# Private repo allowlist
# Machine-specific paths (settings.json, memory files)
/media/[a-z]+/Work1/Claude
/home/[a-z]+/\.claude/
# GitHub username in submodule URLs
<USERNAME>
```

Adapt the `/media/` pattern to match the user's actual mount points (or remove
it if they don't have external drives referenced in settings).

### 4c. Init and configure

```bash
cd ~/.claude
git init
git config user.name "<USERNAME>"
git config user.email "<USERNAME>@users.noreply.github.com"
```

Also update global git config to prevent accidental leaks in any repo:
```bash
git config --global user.name "<USERNAME>"
git config --global user.email "<USERNAME>@users.noreply.github.com"
```

### 4d. Convert settings.json paths

Replace absolute home-directory paths in hook commands with `$HOME` for
portability. Claude Code runs hook commands through a shell, so `$HOME`
expands correctly:

```
"command": "$HOME/.claude/hooks/some-hook.sh"
```

Machine-specific permission paths (external drives, etc.) can't be made
portable — leave them as-is and rely on the fingerprint allowlist.

## Step 5: Convert skills/ to a Submodule

```bash
cd ~/.claude

# Move skills out (preserving .git history)
mv skills skills-git-backup

# Add as submodule using SSH alias URL
git submodule add git@github-skills:<USERNAME>/claude-skills.git skills

# Restore untracked content from backup
cp -rn skills-full-backup/socks-workspace skills/ 2>/dev/null || true
```

The backups (`skills-git-backup/`, `skills-full-backup/`) can be removed after
verifying everything works. The safety guard may block `rm -rf` — tell the user
to run it manually in their terminal.

## Step 6: Commit and Push Private Repo

Stage files individually — never use `git add -A` or `git add .` here, as
there's too much ephemeral content that could slip through:

```bash
cd ~/.claude
git add .gitignore .fingerprint-allowlist .gitmodules CLAUDE.md settings.json
git add hooks/bash-safety-guard.sh hooks/fingerprint_engine.py \
    hooks/git-fingerprint-guard.py hooks/git-fingerprint-guard.sh \
    hooks/fingerprint-identity.txt hooks/pre_compact_adr.py

# Force-add memory files (projects/ is gitignored)
git add --force projects/*/memory/

git status  # Review before committing
```

If the fingerprint guard blocks the commit, check which patterns triggered and
add appropriate entries to `.fingerprint-allowlist`.

```bash
git commit -m "Initial commit: claude config with skills submodule"
git remote add origin git@github-config:<USERNAME>/claude-config.git
git branch -M main
git push -u origin main
```

## Verification Checklist

Run these after setup to confirm everything is clean:

```bash
# 1. Identity scrub — no real name in public repo
cd ~/.claude/skills
git log --format="%an <%ae>" --all | sort -u

# 2. No credentials committed
cd ~/.claude
git log --all --diff-filter=A -- .credentials.json

# 3. No session data committed
git ls-files | grep -c "\.jsonl$"  # should be 0

# 4. Fingerprint scan on public repo
python3 ~/.claude/skills/fingerprint/fingerprint_scan.py --scan-dir ~/.claude/skills

# 5. Hooks still work (run any benign bash command)
ls /tmp > /dev/null
```

## Day-to-Day Workflow

### Push skills changes (public)
```bash
cd ~/.claude/skills
git add <files>
git commit -m "description"
git push
```

### Update submodule pointer (private)
After pushing skills, update the private repo to record the new commit:
```bash
cd ~/.claude
git add skills
git commit -m "Update skills submodule"
git push
```

### Push config changes (private)
```bash
cd ~/.claude
git add settings.json  # or whatever changed
git commit -m "description"
git push
```

### Push new memory files (private)
Memory files live under `projects/*/memory/` which is gitignored. Force-add:
```bash
cd ~/.claude
git add --force projects/*/memory/
git commit -m "Update project memory"
git push
```

## New Machine Setup

Copy SSH keys and config first, then clone:

```bash
# 1. Place deploy keys in ~/.ssh/ (from backup or password manager)
mkdir -p ~/.ssh && chmod 700 ~/.ssh
# Copy claude-skills-deploy, claude-config-deploy (and .pub files)
chmod 600 ~/.ssh/claude-*-deploy

# 2. Add SSH config entries (same Host aliases as Step 1)

# 3. Clone with submodules
git clone --recursive git@github-config:<USERNAME>/claude-config.git ~/.claude
```

**Alternative** if SSH config isn't set up yet (use HTTPS):
```bash
git clone https://github.com/<USERNAME>/claude-config.git ~/.claude
cd ~/.claude
git submodule set-url skills https://github.com/<USERNAME>/claude-skills.git
git submodule update --init
```

## Gotchas (lessons from real deployments)

### Initial commit quirks
Before the first commit, there's no HEAD. This means `git rm --cached` and
`git reset HEAD` won't work. If you staged the wrong files and need to start
over, delete the index directly:
```bash
rm .git/index
```
Then re-add only the files you want.

### $HOME vs ~ in settings.json
Hook commands in `settings.json` are run via `bash -c`, so `$HOME` expands
correctly. But `~` does NOT expand in JSON strings — always use `$HOME`.

### Fingerprint false positives
The fingerprint guard checks content, not context. Common false positives:
- The word "author" in prose (matches the author/copyright pattern)
- The GitHub username in `.gitmodules` or URLs
- Absolute paths in memory files referencing home directory

Fix: add patterns to `.fingerprint-allowlist` (one regex per line).

### projects/ gitignore complexity
Nested un-ignore patterns like `!projects/*/memory/**` don't work reliably
because project directories contain UUID-named session files at multiple
depths. The reliable approach is to fully ignore `projects/` and use
`git add --force projects/*/memory/` to track only memory files.

## Troubleshooting

### Fingerprint guard blocks commit
Check which patterns triggered, then either:
- Fix the content (replace absolute paths with `$HOME`)
- Add a regex to `.fingerprint-allowlist` for the private repo

### Safety guard blocks rm -rf
The bash-safety-guard blocks `rm -rf` from Claude. Run cleanup commands
manually in your terminal.

### SSH "Permission denied"
Verify the deploy key is added to the correct repo with write access:
```bash
ssh -T github-skills 2>&1
ssh -T github-config 2>&1
```

### Submodule URL fails on new machine
The `.gitmodules` contains SSH alias URLs (`git@github-skills:...`). Either
set up the SSH config first, or override with HTTPS:
```bash
git submodule set-url skills https://github.com/<USERNAME>/claude-skills.git
git submodule update --init
```
