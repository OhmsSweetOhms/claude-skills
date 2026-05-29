"""
fingerprint_engine.py -- Core scanning engine for PII, secrets, and digital
fingerprint material detection.

Shared by:
  - git-fingerprint-guard.py  (PreToolUse hook)
  - fingerprint_scan.py       (CLI audit tool / skill)

All findings are BLOCK. No warn tier.

Config:
  ~/.claude/hooks/fingerprint-identity.txt       (additional identity strings)
  ~/.claude/hooks/fingerprint-allowlist          (global line-content regex allowlist)
  .fingerprint-allowlist                         (per-project line-content regex allowlist)
  ~/.claude/hooks/fingerprint-path-allowlist     (global path-glob allowlist; whole files skipped)
  .fingerprint-path-allowlist                    (per-project path-glob allowlist)
"""

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

# ---------------------------------------------------------------------------
# Identity auto-detection
# ---------------------------------------------------------------------------
def build_identity_strings() -> list[str]:
    """Gather identity strings from system + config file."""
    ids = []

    user = os.environ.get("USER", "")
    if user:
        ids.append(user)

    try:
        hostname = subprocess.run(
            ["hostname"], capture_output=True, text=True, timeout=5
        ).stdout.strip()
        if hostname:
            ids.append(hostname)
            short = hostname.split(".")[0]
            if short and short != hostname:
                ids.append(short)
    except Exception:
        pass

    home_base = Path.home().name
    if home_base and home_base != user:
        ids.append(home_base)

    for key in ("user.name", "user.email"):
        try:
            val = subprocess.run(
                ["git", "config", key],
                capture_output=True, text=True, timeout=5
            ).stdout.strip()
            if val:
                ids.append(val)
                if key == "user.email" and "@" in val:
                    ids.append(val.split("@")[0])
        except Exception:
            pass

    # Load additional identity strings
    if IDENTITY_FILE.is_file():
        for line in IDENTITY_FILE.read_text().splitlines():
            line = line.split("#")[0].strip()
            if line:
                ids.append(line)

    # Deduplicate, min length 3
    seen = set()
    result = []
    for s in ids:
        low = s.lower()
        if len(low) >= 3 and low not in seen:
            seen.add(low)
            result.append(s)
    return result


def build_identity_pattern(ids: list[str]) -> Optional[re.Pattern]:
    """Build compiled case-insensitive regex from identity strings."""
    if not ids:
        return None
    escaped = [re.escape(s) for s in ids]
    return re.compile("|".join(escaped), re.IGNORECASE)


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
    # --- Hard secrets ---
    (r"-----BEGIN\s*(RSA|DSA|EC|OPENSSH|PGP|PRIVATE)\s*(PRIVATE\s*)?KEY-----",
     "PRIVATE_KEY", "Private key detected"),
    (r"AKIA[0-9A-Z]{16}",
     "AWS_KEY", "AWS access key ID detected"),
    (r"(?i)(aws_secret_access_key|aws_session_token)\s*[:=]",
     "AWS_SECRET", "AWS secret key assignment detected"),
    (r"AIza[0-9A-Za-z_\-]{35}",
     "GCP_KEY", "Google Cloud API key detected"),
    (r"(?i)(api[_\-]?key|apikey)\s*[:=]\s*[\"'][a-zA-Z0-9]{16,}",
     "API_KEY", "API key assignment detected"),
    (r"(?i)(password|passwd|pwd)\s*[:=]\s*[\"'][^\"']{4,}",
     "PASSWORD", "Password assignment detected"),
    (r"(?i)(secret|token|auth_token|access_token)\s*[:=]\s*[\"'][^\"']{8,}",
     "SECRET", "Secret/token assignment detected"),
    (r"(mysql|postgres|postgresql|mongodb|redis|amqp|mssql)://[^\s]+@[^\s]+",
     "DB_URL", "Database connection string with credentials detected"),
    (r"eyJ[a-zA-Z0-9_\-]{10,}\.eyJ[a-zA-Z0-9_\-]{10,}\.[a-zA-Z0-9_\-]{10,}",
     "JWT", "JWT token detected"),
    (r"ghp_[a-zA-Z0-9]{36}",
     "GITHUB_TOKEN", "GitHub personal access token detected"),
    (r"gho_[a-zA-Z0-9]{36}",
     "GITHUB_OAUTH", "GitHub OAuth token detected"),
    (r"glpat-[a-zA-Z0-9_\-]{20,}",
     "GITLAB_TOKEN", "GitLab personal access token detected"),
    (r"xox[bpas]-[a-zA-Z0-9\-]+",
     "SLACK_TOKEN", "Slack token detected"),
    (r"sk-[a-zA-Z0-9]{20,}",
     "OPENAI_KEY", "OpenAI API key detected"),
    (r"ya29\.[0-9A-Za-z_\-]+",
     "GOOGLE_OAUTH", "Google OAuth token detected"),

    # --- PII ---
    (r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}",
     "EMAIL", "Email address detected"),
    (r"([0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}",
     "MAC_ADDRESS", "MAC address detected"),
    # STREET_ADDRESS regex removed -- too many false positives on
    # legitimate technical writing (e.g., "plan-04 of PL.INTERPOLATOR",
    # "plan-04 in place", "Plan-02 of PL.DECIMATOR", "2026-05-11
    # reframing PL.INTERPOLATOR" all matched against PL.* block names
    # and numbered-plan adjacencies). Specific street addresses are
    # caught via fingerprint-identity.txt substring entries instead.
    (r"(?i)(ssh-rsa|ssh-ed25519|ssh-dss|ecdsa-sha2)\s+[A-Za-z0-9+/=]{20,}",
     "SSH_PUBKEY", "SSH public key detected (may reveal identity)"),
    (r"(?i)machine\s+\S+\s+login\s+\S+\s+password\s+\S+",
     "NETRC", ".netrc credential entry detected"),

    # --- Absolute paths (fingerprint material) ---
    (r"/home/[a-zA-Z0-9_\-]+/",
     "ABS_PATH_HOME", "Absolute home directory path detected"),
    (r"/Users/[a-zA-Z0-9_\-]+/",
     "ABS_PATH_MACOS", "Absolute macOS user path detected"),
    (r"/media/[a-zA-Z0-9_\-]+/",
     "ABS_PATH_MEDIA", "Absolute media mount path detected"),
    (r"C:\\\\Users\\\\[a-zA-Z0-9_\-]+",
     "ABS_PATH_WIN", "Absolute Windows user path detected"),

    # --- Author attribution (must be OhmsSweetOhms only) ---
    (r"(?i:author|maintainer|copyright\s*(?:\(c\)|©)?)\s*[:=]?\s*[\"']?(?!.*OhmsSweetOhms)[A-Z][a-z]+\s+[A-Z][a-z]+",
     "AUTHOR_NAME", "Author/copyright with real name (must use OhmsSweetOhms)"),
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

            if category == "EMAIL":
                if re.search(r"Co-Authored-By.*Claude|noreply@anthropic", line, re.I):
                    continue

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

    def scan_commit_identities(self, repo_dir: str, rev_ranges: list[str]):
        """Scan the commit author/committer *identity* metadata surface.

        Commit messages are scanned via scan_line(); this covers the
        separate surface that message scanning never sees -- the author and
        committer name + email baked into each commit object. Synthesized
        "author/committer" lines are fed through scan_line() so the identity
        pattern, SCAN_RULES (incl. the Co-Authored-By/noreply email
        exemption), allowlists, and finding format are all handled
        identically to every other scan path.
        """
        # NUL-separated fields per commit, one commit per line.
        fmt = "%H%x00%an%x00%ae%x00%cn%x00%ce"
        for rev_range in rev_ranges:
            try:
                result = subprocess.run(
                    ["git", "log", "--no-color", f"--format={fmt}", rev_range],
                    capture_output=True, text=True, timeout=30,
                    cwd=repo_dir,
                )
            except Exception:
                continue
            if result.returncode != 0:
                continue
            for entry in result.stdout.split("\n"):
                if not entry.strip():
                    continue
                parts = entry.split("\x00")
                if len(parts) != 5:
                    continue
                sha, an, ae, cn, ce = parts
                label = f"commit-identity:{sha[:7]}"
                self.scan_line(label, 1, f"author {an} <{ae}>")
                self.scan_line(label, 2, f"committer {cn} <{ce}>")

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
