"""
fingerprint_engine.py -- scanning engine: barebones content scanning + git
author/committer persona identity check.

Two copies share this same logic, intentionally de-symlinked so the security
guard does not depend on a public submodule being checked out:
  - hooks/fingerprint_engine.py             (private; used by git-fingerprint-guard.py)
  - skills/fingerprint/fingerprint_engine.py (public; used by the /fingerprint skill)
The persona definitions below are the PUBLIC alias (a GitHub-noreply and a
ProtonMail address), not real identity, so they are safe in the public copy.
Real identity tokens never live in either engine source -- they are read at
runtime from the private fingerprint-identity.txt.

Scope:
  - Identity tokens: read from the private fingerprint-identity.txt
    (case-insensitive substring; a '=' prefix marks word-boundary matching).
    No $USER/hostname/git-config derivation -- the set is exactly the file.
  - Content tripwires (SCAN_RULES): three secret patterns (private key, AWS
    access key, quoted credential assignment) + the absolute-path mount
    prefixes (/home, /Users, /media, C:\\Users). The latter are mandated by
    global Core Rule 15 and trip independently of the identity tokens. The
    broad PII/vendor-key battery was removed (false-positive churn).
  - Git author/committer persona check (scan_commit_identities /
    check_pending_commit_identity): the metadata surface content/message
    scans cannot see -- a commit made with a non-persona name/email is the
    real leak vector and is blocked here.

Shared by:
  - git-fingerprint-guard.py  (PreToolUse hook)
  - fingerprint_scan.py       (CLI audit tool / skill)

All findings are BLOCK. No warn tier.

Config:
  ~/.claude/hooks/fingerprint-identity.txt       (identity tokens; PRIVATE)
  ~/.claude/hooks/fingerprint-allowlist          (global line-content regex allowlist)
  .fingerprint-allowlist                         (per-project line-content regex allowlist)
  ~/.claude/hooks/fingerprint-path-allowlist     (global path-glob allowlist; whole files skipped)
  .fingerprint-path-allowlist                    (per-project path-glob allowlist)
"""

# PEP 563: defer annotation evaluation so new-style hints (list[str],
# tuple[set, set], X | None) don't crash at import time under Python 3.8 —
# the interpreter that runs the PreToolUse hook (/usr/bin/python3). Without
# this the engine fails to import and the guard silently no-ops.
from __future__ import annotations

import fnmatch
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Config paths
# ---------------------------------------------------------------------------
HOME = Path.home()
LOG_FILE = HOME / ".claude" / "hooks" / "fingerprint.log"
IDENTITY_FILE = HOME / ".claude" / "hooks" / "fingerprint-identity.txt"
GLOBAL_ALLOWLIST = HOME / ".claude" / "hooks" / "fingerprint-allowlist"
GLOBAL_PATH_ALLOWLIST = HOME / ".claude" / "hooks" / "fingerprint-path-allowlist"
GIT_IDENTITY_ALLOWLIST = HOME / ".claude" / "hooks" / "fingerprint-git-identity-allowlist"

# The engine's own config files DEFINE the tokens/patterns it scans for, so
# scanning them for those very tokens is a definitional false positive (the
# guard would block any push that touches fingerprint-identity.txt). Skip them.
SELF_CONFIG_BASENAMES = frozenset({
    IDENTITY_FILE.name, GLOBAL_ALLOWLIST.name, GLOBAL_PATH_ALLOWLIST.name,
    GIT_IDENTITY_ALLOWLIST.name,
    ".fingerprint-allowlist", ".fingerprint-path-allowlist",
})

# ---------------------------------------------------------------------------
# Identity auto-detection
# ---------------------------------------------------------------------------
def build_identity_strings() -> list[str]:
    """Read the identity token list from the private fingerprint-identity.txt.

    No system/host/git-config derivation -- the set is exactly what the file
    declares, so the scan is predictable and blocks only on a real leak. A
    leading '=' on a token marks it for word-boundary matching (see
    build_identity_pattern); it is preserved here and interpreted there.
    """
    ids = []
    if IDENTITY_FILE.is_file():
        for line in IDENTITY_FILE.read_text().splitlines():
            line = line.split("#")[0].strip()
            if line:
                ids.append(line)

    # Deduplicate, min length 3 (measured on the token without the '=' marker)
    seen = set()
    result = []
    for s in ids:
        low = s.lstrip("=").lower()
        if len(low) >= 3 and low not in seen:
            seen.add(low)
            result.append(s)
    return result


def build_identity_pattern(ids: list[str]) -> Optional[re.Pattern]:
    """Build a compiled case-insensitive regex from identity tokens.

    Plain tokens match as substrings. A token written with a leading '='
    matches only when not flanked by ASCII letters, so "=smith" catches
    "smith.jones" / "Smith_Jones" / "smith123" but not "blacksmith" or
    "smithson".
    """
    if not ids:
        return None
    parts = []
    for s in ids:
        if s.startswith("="):
            esc = re.escape(s[1:])
            parts.append(rf"(?<![a-zA-Z]){esc}(?![a-zA-Z])")
        else:
            parts.append(re.escape(s))
    return re.compile("|".join(parts), re.IGNORECASE)


# ---------------------------------------------------------------------------
# Allowlist
# ---------------------------------------------------------------------------
BUILTIN_ALLOWLIST = [
    r"example\.com",
    r"test@test\.com",
    r"user@example",
    r"foo@bar",
    r"password123",
    r"changeme",
    r"your[_\-]?(api[_\-]?key|token|password|secret)",
    r"xxx+|\.\.\.|\*{3,}",
    r"placeholder",
    r"TODO|FIXME|HACK",
    r"XPAR_[A-Z_]+_BASEADDR",
    r"Co-Authored-By:\s*Claude",
]


def load_allowlist(project_dir: str = ".") -> list[re.Pattern]:
    """Load allowlist patterns from global + project files + builtins."""
    raw: list[str] = list(BUILTIN_ALLOWLIST)

    for path in [GLOBAL_ALLOWLIST, Path(project_dir) / ".fingerprint-allowlist"]:
        if Path(path).is_file():
            for line in Path(path).read_text().splitlines():
                line = line.split("#")[0].strip()
                if line:
                    raw.append(line)

    compiled = []
    for pat in raw:
        try:
            compiled.append(re.compile(pat, re.IGNORECASE))
        except re.error:
            pass
    return compiled


def is_allowlisted(line: str, allowlist: list[re.Pattern]) -> bool:
    return any(p.search(line) for p in allowlist)


def load_path_allowlist(project_dir: str = ".") -> list[str]:
    """Load path-glob patterns from global + project path-allowlist files.

    Patterns are fnmatch-style globs matched against the relative filepath
    (e.g. ``.research/session-*/repos/**``). Matching paths are skipped
    entirely during scans.
    """
    patterns: list[str] = []
    for path in [GLOBAL_PATH_ALLOWLIST, Path(project_dir) / ".fingerprint-path-allowlist"]:
        if Path(path).is_file():
            for line in Path(path).read_text().splitlines():
                line = line.split("#")[0].strip()
                if line:
                    patterns.append(line)
    return patterns


def is_path_allowlisted(filepath: str, path_allowlist: list[str]) -> bool:
    """Check if filepath matches any path-allowlist glob."""
    if not path_allowlist:
        return False
    # Normalize: strip leading ./ and use forward slashes
    norm = filepath.replace("\\", "/").lstrip("./")
    for pat in path_allowlist:
        if fnmatch.fnmatch(norm, pat) or fnmatch.fnmatch(filepath, pat):
            return True
    return False


# ---------------------------------------------------------------------------
# Scan rules -- compiled once at import time
# ---------------------------------------------------------------------------
SCAN_RULES: list[tuple[re.Pattern, str, str]] = []

_RAW_RULES = [
    # Barebones content tripwires for the GUARD -- high signal, near-zero
    # false positive. The noisy PII/vendor-key battery (emails, MAC, JWT,
    # GCP/GitHub/GitLab/Slack/OpenAI prefixes, DB URLs, SSH pubkeys, .netrc,
    # the author-name heuristic) was removed: its false positives on
    # technical writing cost more than the rare real catch. The git
    # author/committer leak it half-covered is now caught properly by the
    # persona identity check (scan_commit_identities / check_pending_commit_identity).
    #
    # Two classes are kept here because they are high-signal AND mandated by
    # global Core Rule 15 (no absolute paths or usernames in anything written):
    # secret material, and the absolute-path mount prefixes -- the latter
    # caught independently of the identity tokens so a path with any username
    # (not just ours) trips.
    (r"-----BEGIN\s*(RSA|DSA|EC|OPENSSH|PGP|PRIVATE)\s*(PRIVATE\s*)?KEY-----",
     "PRIVATE_KEY", "Private key detected"),
    (r"AKIA[0-9A-Z]{16}",
     "AWS_KEY", "AWS access key ID detected"),
    (r"(?i)(secret|token|auth_token|access_token|password|passwd|pwd|api[_\-]?key|apikey)\s*[:=]\s*[\"'][^\"']{4,}",
     "SECRET", "Quoted secret/token/password assignment detected"),

    # Absolute paths (Core Rule 15 -- mount prefix trips independently)
    (r"/home/[a-zA-Z0-9_\-]+/",
     "ABS_PATH_HOME", "Absolute home directory path detected"),
    (r"/Users/[a-zA-Z0-9_\-]+/",
     "ABS_PATH_MACOS", "Absolute macOS user path detected"),
    (r"/media/[a-zA-Z0-9_\-]+/",
     "ABS_PATH_MEDIA", "Absolute media mount path detected"),
    (r"C:\\\\Users\\\\[a-zA-Z0-9_\-]+",
     "ABS_PATH_WIN", "Absolute Windows user path detected"),
]

for _pat_str, _cat, _msg in _RAW_RULES:
    try:
        SCAN_RULES.append((re.compile(_pat_str), _cat, _msg))
    except re.error as e:
        print(f"WARNING: Bad regex in rule {_cat}: {e}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
def log_finding(mode: str, category: str, location: str, detail: str):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_FILE, "a") as f:
        f.write(f"[{ts}] [BLOCK] [{mode}] {category}\n")
        f.write(f"  file: {location}\n")
        f.write(f"  detail: {detail}\n")


# ---------------------------------------------------------------------------
# Masking
# ---------------------------------------------------------------------------
def mask_value(val: str) -> str:
    if len(val) > 8:
        return val[:4] + "****" + val[-4:]
    return "****"


# ---------------------------------------------------------------------------
# File helpers
# ---------------------------------------------------------------------------
def is_binary(filepath: str) -> bool:
    """Quick heuristic: read first 8KB and check for null bytes."""
    try:
        with open(filepath, "rb") as f:
            chunk = f.read(8192)
        return b"\x00" in chunk
    except OSError:
        return True


def git_ls_files(repo_dir: str) -> list[str]:
    """Get tracked files from a git repo."""
    try:
        result = subprocess.run(
            ["git", "ls-files"],
            capture_output=True, text=True, timeout=30,
            cwd=repo_dir
        )
        return [f for f in result.stdout.splitlines() if f]
    except Exception:
        return []


def is_git_repo(path: str) -> bool:
    return (Path(path) / ".git").exists()


def in_git_work_tree(path: str) -> bool:
    """True if path is inside a git work tree (itself or any ancestor).

    Handles monorepo subdirectories where .git lives several levels up.
    """
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            capture_output=True, text=True, timeout=10,
            cwd=path,
        )
        return result.returncode == 0 and result.stdout.strip() == "true"
    except Exception:
        return False


def git_repo_status(repo_dir: str) -> dict:
    """Get git working tree status for a repo."""
    info = {
        "branch": "",
        "modified": 0,
        "staged": 0,
        "untracked": 0,
        "stashes": 0,
        "clean": True,
        "has_remote": False,
        "unpushed": 0,
    }

    try:
        r = subprocess.run(
            ["git", "branch", "--show-current"],
            capture_output=True, text=True, timeout=5, cwd=repo_dir
        )
        info["branch"] = r.stdout.strip() or "(detached)"

        r = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True, text=True, timeout=10, cwd=repo_dir
        )
        for line in r.stdout.splitlines():
            if not line or len(line) < 2:
                continue
            idx, wt = line[0], line[1]
            if line.startswith("??"):
                info["untracked"] += 1
            elif idx in "MADRC":
                info["staged"] += 1
            if wt in "MD":
                info["modified"] += 1

        r = subprocess.run(
            ["git", "stash", "list"],
            capture_output=True, text=True, timeout=5, cwd=repo_dir
        )
        info["stashes"] = len([l for l in r.stdout.splitlines() if l])

        r = subprocess.run(
            ["git", "remote"],
            capture_output=True, text=True, timeout=5, cwd=repo_dir
        )
        info["has_remote"] = bool(r.stdout.strip())

        if info["has_remote"]:
            r = subprocess.run(
                ["git", "rev-list", "--count", "@{upstream}..HEAD"],
                capture_output=True, text=True, timeout=5, cwd=repo_dir
            )
            if r.returncode == 0:
                info["unpushed"] = int(r.stdout.strip() or "0")

        info["clean"] = (
            info["modified"] == 0 and
            info["staged"] == 0 and
            info["untracked"] == 0
        )

    except Exception:
        pass

    return info


# ---------------------------------------------------------------------------
# Gitignore filtering
# ---------------------------------------------------------------------------
def filter_gitignored(files: list[str], project_dir: str) -> list[str]:
    """Remove files matching .gitignore patterns from the file list.

    For git repos, uses ``git check-ignore --no-index`` for accurate matching.
    For non-git dirs, falls back to fnmatch against .gitignore patterns.
    """
    gitignore_path = Path(project_dir) / ".gitignore"
    if not gitignore_path.is_file() or not files:
        return files

    # Git repo (or subdir of one): use git check-ignore for accurate matching
    if in_git_work_tree(project_dir):
        try:
            result = subprocess.run(
                ["git", "check-ignore", "--stdin", "--no-index"],
                input="\n".join(files),
                capture_output=True, text=True, timeout=30,
                cwd=project_dir,
            )
            # exit 0 = all ignored, 1 = some not ignored, 128 = error
            if result.returncode in (0, 1):
                ignored = set(result.stdout.splitlines())
                return [f for f in files if f not in ignored]
        except Exception:
            pass

    # Non-git fallback: parse .gitignore and use fnmatch
    patterns = []
    for line in gitignore_path.read_text(errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("!"):
            continue
        patterns.append(line.rstrip("/"))

    if not patterns:
        return files

    def matches(filepath: str) -> bool:
        name = Path(filepath).name
        for pat in patterns:
            if "/" in pat:
                # Path pattern: match against full relative path
                if fnmatch.fnmatch(filepath, pat):
                    return True
            else:
                # Bare pattern: match against basename at any level
                if fnmatch.fnmatch(name, pat):
                    return True
        return False

    return [f for f in files if not matches(f)]


# ---------------------------------------------------------------------------
# Core scanner
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Git author/committer identity (the metadata surface)
# ---------------------------------------------------------------------------
# Content and message scans never see who git stamps as the author/committer
# of a commit (%an/%ae/%cn/%ce). That metadata leaks real authorship just as
# surely as a username in a file. Every NEW / unpushed commit's author AND
# committer must match the public persona, or it's blocked. Extend for
# collaborators via GIT_IDENTITY_ALLOWLIST: one "Name <email>" pair or a bare
# email/name per line (# comments allowed).
_PERSONA_NAME = "OhmsSweetOhms"
# The persona legitimately uses more than one non-leaking email (a GitHub
# noreply address and a ProtonMail address) -- both are the same public
# identity, neither exposes the real name. A commit's email must be one of
# these (or an entry in GIT_IDENTITY_ALLOWLIST) AND its name must be the persona.
_PERSONA_EMAILS = {
    "ohmssweetohms@users.noreply.github.com",
    "ohmssweetohms@pm.me",
}


def load_allowed_git_identities() -> tuple[set, set]:
    """Return (allowed_names, allowed_emails), lowercased, persona-seeded."""
    names = {_PERSONA_NAME.lower()}
    emails = set(_PERSONA_EMAILS)
    if GIT_IDENTITY_ALLOWLIST.is_file():
        for line in GIT_IDENTITY_ALLOWLIST.read_text().splitlines():
            line = line.split("#")[0].strip()
            if not line:
                continue
            m = re.match(r"^(.*?)\s*<([^>]+)>$", line)
            if m:
                names.add(m.group(1).strip().lower())
                emails.add(m.group(2).strip().lower())
            elif "@" in line:
                emails.add(line.lower())
            else:
                names.add(line.lower())
    return names, emails


def git_identity_ok(name: str, email: str, allowed=None) -> bool:
    """True iff BOTH the name and email are in the allowed-persona sets."""
    names, emails = allowed if allowed is not None else load_allowed_git_identities()
    return name.strip().lower() in names and email.strip().lower() in emails


class Scanner:
    """Stateful scanner: accumulates findings."""

    def __init__(self, mode: str, project_dir: str = "."):
        self.mode = mode
        self.findings: list[str] = []
        self.identity_pattern = build_identity_pattern(build_identity_strings())
        self.allowlist = load_allowlist(project_dir)
        self.path_allowlist = load_path_allowlist(project_dir)

    @property
    def found_count(self) -> int:
        return len(self.findings)

    def scan_line(self, filepath: str, lineno: int, line: str):
        """Scan a single line. Stops at first finding per line.

        Path-allowlisted files still get identity-pattern scanning (so
        identity leaks into vendored third-party dirs are still caught),
        but skip the generic SCAN_RULES regex loop (avoids false positives
        on paper author emails, FSF address, array-index numbers, etc.).
        """
        if not line.strip():
            return

        # Never scan the engine's own config files -- they hold the token and
        # allowlist definitions, so a match there is circular, not a leak.
        if os.path.basename(filepath) in SELF_CONFIG_BASENAMES:
            return

        path_allowed = is_path_allowlisted(filepath, self.path_allowlist)

        # Identity patterns always run, even in allowlisted paths
        if self.identity_pattern:
            m = self.identity_pattern.search(line)
            if m and not is_allowlisted(line, self.allowlist):
                matched = m.group()
                self.findings.append(
                    f"BLOCKED: Personal identifier '{matched}' found in {filepath}:{lineno}"
                )
                log_finding(self.mode, "IDENTITY", f"{filepath}:{lineno}",
                            f"Matched: {matched}")
                return

        if path_allowed:
            return

        # Check scan rules
        for pattern, category, message in SCAN_RULES:
            m = pattern.search(line)
            if not m:
                continue

            matched = m.group()

            if is_allowlisted(line, self.allowlist):
                continue

            masked = mask_value(matched)
            self.findings.append(
                f"BLOCKED: {message} -- {filepath}:{lineno} ({masked})"
            )
            log_finding(self.mode, category, f"{filepath}:{lineno}",
                        f"{message}: {masked}")
            return  # one finding per line

    def scan_file(self, filepath: str, content: Optional[str] = None):
        """Scan a file's contents line by line.

        Allowlisted paths are NOT short-circuited here — the per-line
        scan still needs to run the identity check. SCAN_RULES skipping
        is handled inside scan_line().
        """
        if content is None:
            try:
                content = Path(filepath).read_text(errors="replace")
            except (OSError, UnicodeDecodeError):
                return
        for lineno, line in enumerate(content.splitlines(), 1):
            self.scan_line(filepath, lineno, line)

    def scan_diff(self, diff_text: str):
        """Scan unified diff, only added lines."""
        current_file = "unknown"
        lineno = 0
        for line in diff_text.splitlines():
            if line.startswith("+++ b/"):
                current_file = line[6:]
                continue
            hunk = re.match(r"^@@.*\+(\d+)", line)
            if hunk:
                lineno = int(hunk.group(1))
                continue
            if line.startswith("+"):
                self.scan_line(current_file, lineno, line[1:])
                lineno += 1
            elif not line.startswith("-"):
                lineno += 1

    def scan_commit_identities(self, repo_dir: str, rev_args: list):
        """Block commits in the given rev range whose author OR committer
        identity is not the allowed persona. This is the metadata surface
        (%an/%ae/%cn/%ce) that scan_line / scan_diff never inspect.

        rev_args is a list of git-log revision arguments, e.g.
        ["origin/main..HEAD"] or ["HEAD", "--not", "--remotes"].
        """
        fmt = "%H%x00%an%x00%ae%x00%cn%x00%ce%x1e"
        try:
            r = subprocess.run(
                ["git", "log", *rev_args, f"--format={fmt}"],
                capture_output=True, text=True, cwd=repo_dir, timeout=60
            )
        except (OSError, subprocess.TimeoutExpired):
            return
        if r.returncode != 0:
            return
        allowed = load_allowed_git_identities()
        for rec in r.stdout.split("\x1e"):
            rec = rec.lstrip("\n")
            if not rec:
                continue
            parts = rec.split("\x00")
            if len(parts) < 5:
                continue
            sha, an, ae, cn, ce = parts[:5]
            for role, name, email in (("author", an, ae), ("committer", cn, ce)):
                if not git_identity_ok(name, email, allowed):
                    self.findings.append(
                        f"BLOCKED: commit {sha[:7]} {role} '{name} <{email}>' "
                        f"is not the allowed persona ({_PERSONA_NAME})"
                    )
                    log_finding(self.mode, "GIT_IDENTITY", f"commit:{sha[:7]}",
                                f"{role}: {name} <{email}>")

    def check_pending_commit_identity(self, repo_dir: str = "."):
        """Block the next commit if the identity git WILL stamp (git var
        GIT_AUTHOR_IDENT / GIT_COMMITTER_IDENT) is not the allowed persona.
        Catches a misconfigured user.name/user.email before the commit exists.
        Degrades to a no-op outside a git repo (git var returns non-zero)."""
        allowed = load_allowed_git_identities()
        for role, var in (("author", "GIT_AUTHOR_IDENT"),
                          ("committer", "GIT_COMMITTER_IDENT")):
            try:
                r = subprocess.run(["git", "var", var], capture_output=True,
                                   text=True, cwd=repo_dir, timeout=5)
            except (OSError, subprocess.TimeoutExpired):
                continue
            if r.returncode != 0:
                continue
            m = re.match(r"^(.*?)\s+<([^>]*)>", r.stdout.strip())
            if not m:
                continue
            name, email = m.group(1), m.group(2)
            if not git_identity_ok(name, email, allowed):
                self.findings.append(
                    f"BLOCKED: pending commit {role} '{name} <{email}>' is not "
                    f"the allowed persona ({_PERSONA_NAME}) "
                    f"-- fix with: git config user.name / user.email"
                )
                log_finding(self.mode, "GIT_IDENTITY", "pending-commit",
                            f"{role}: {name} <{email}>")

    def report(self, stream=sys.stderr) -> int:
        """Print findings and return exit code (0=clean, 2=findings)."""
        if self.findings:
            print(f"\nBLOCKED: {self.found_count} finding(s)", file=stream)
            for f in self.findings:
                print(f"  {f}", file=stream)
            print("\nFix these issues before committing.", file=stream)
            return 2
        return 0


# ---------------------------------------------------------------------------
# Multi-repo helpers
# Directories that contain 3rd-party code -- always skip during scans
THIRD_PARTY_DIRS = {"tools", "vendor", "third_party", "thirdparty", "extern",
                    "external", "node_modules", ".venv", "venv"}


# ---------------------------------------------------------------------------
def find_git_repos(root_dir: str) -> list[str]:
    """Find all git repos under root_dir, skipping 3rd-party directories."""
    repos = []
    for dirpath, dirnames, _files in os.walk(root_dir):
        dirnames[:] = [d for d in dirnames if d not in THIRD_PARTY_DIRS]
        if ".git" in dirnames:
            repos.append(dirpath)
            dirnames.remove(".git")
            dirnames[:] = [d for d in dirnames
                           if not is_git_repo(os.path.join(dirpath, d))]
    repos.sort()
    return repos


def find_loose_files(root_dir: str, repos: list[str]) -> list[str]:
    """Find files in root_dir not under any git repo."""
    loose = []
    for item in sorted(Path(root_dir).iterdir()):
        if item.is_file():
            loose.append(str(item))
        elif item.is_dir() and str(item) not in repos:
            is_parent = any(r.startswith(str(item)) for r in repos)
            if not is_parent:
                loose.append(str(item) + "/")
    return loose


def scan_single_repo(repo_path: str) -> list[str]:
    """Scan a single repo, return list of finding strings."""
    scanner = Scanner("scan", repo_path)

    if in_git_work_tree(repo_path):
        files = git_ls_files(repo_path)
    else:
        files = []
        for root, _dirs, fnames in os.walk(repo_path):
            for fname in fnames:
                files.append(os.path.relpath(
                    os.path.join(root, fname), repo_path))

    files = filter_gitignored(files, repo_path)

    for f in files:
        full = os.path.join(repo_path, f)
        if not os.path.isfile(full) or is_binary(full):
            continue
        scanner.scan_file(f, Path(full).read_text(errors="replace"))

    return scanner.findings
