#!/usr/bin/env python3
"""
fetch_and_save.py — Single entry point for saving all research content to session directory.

Modes:
  PDF/URL download:
    python3 fetch_and_save.py fetch <url> <session_dir> [--name <name>]

  Save WebSearch results (pipe search results as text via stdin):
    echo "<content>" | python3 fetch_and_save.py search-log <session_dir> --role <role> --query "<query>"

  Save WebFetch extraction (pipe extracted content via stdin):
    echo "<content>" | python3 fetch_and_save.py webfetch <session_dir> --name <name> --url <source_url> --type <content_type>
    Content types: blog_post, tutorial, forum_thread → blogs/
                   app_note, trade_article          → app-notes/
                   anything else                    → html/

  Save gh API JSON (pipe JSON via stdin):
    gh api ... | python3 fetch_and_save.py gh-json <session_dir> --name <name>

  Clone a git repo:
    python3 fetch_and_save.py clone <repo_url> <session_dir> [--name <name>]

All modes auto-create required directories. All output goes to stdout for confirmation.
"""

import argparse
import json
import os
import re
import ssl
import subprocess
import sys
import urllib.request
import urllib.error
from datetime import datetime


def sanitize_name(text: str) -> str:
    """Generate a sanitized filename from a URL or text."""
    # Take the last path component if it looks like a URL
    if "/" in text:
        path = text.rstrip("/").split("/")[-1]
        path = path.split("?")[0]
        base, ext = os.path.splitext(path)
        if not base or base == "download":
            parts = text.rstrip("/").split("/")
            for part in reversed(parts):
                if part and part != "download" and "." not in part[:5]:
                    base = part
                    break
            else:
                base = "document"
    else:
        base = text
    # Sanitize: lowercase, replace non-alphanumeric with hyphens
    base = re.sub(r"[^a-z0-9]+", "-", base.lower()).strip("-")
    return base[:80]


def ensure_dirs(session_dir: str):
    """Create all session subdirectories."""
    for subdir in ["results", "pdfs", "blogs", "app-notes", "html", "repos"]:
        os.makedirs(os.path.join(session_dir, subdir), exist_ok=True)


# Content type → subdirectory mapping
CONTENT_TYPE_DIRS = {
    "blog_post": "blogs",
    "tutorial": "blogs",
    "forum_thread": "blogs",
    "app_note": "app-notes",
    "trade_article": "app-notes",
}


def content_type_dir(content_type: str) -> str:
    """Map a content type string to its session subdirectory."""
    return CONTENT_TYPE_DIRS.get(content_type, "html")


def detect_pdf(url: str, headers: dict) -> bool:
    """Detect if a URL points to a PDF."""
    url_lower = url.lower()
    if url_lower.endswith(".pdf"):
        return True
    content_type = headers.get("Content-Type", "")
    if "application/pdf" in content_type:
        return True
    pdf_hosts = ["digitalcommons", "arxiv.org/pdf", "ntrs.nasa.gov/api/citations",
                 "yorkspace.library", "etd.auburn.edu"]
    for host in pdf_hosts:
        if host in url_lower:
            return True
    return False


def download(url: str) -> tuple:
    """Download URL, return (data, headers, final_url). SSL fallback for gov/ESA sites."""
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (research-skill fetch_and_save.py)"
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = resp.read()
            headers = dict(resp.headers)
            final_url = resp.url
    except urllib.error.URLError as e:
        if "CERTIFICATE_VERIFY_FAILED" in str(e):
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            with urllib.request.urlopen(req, timeout=30, context=ctx) as resp:
                data = resp.read()
                headers = dict(resp.headers)
                final_url = resp.url
        else:
            raise
    return data, headers, final_url


def extract_pdf_text(pdf_path: str) -> str:
    """Extract text from PDF using pymupdf."""
    try:
        import pymupdf
    except ImportError:
        return "[pymupdf not installed — run: python3 -m pip install pymupdf]\n"

    doc = pymupdf.open(pdf_path)
    lines = []
    for i, page in enumerate(doc):
        text = page.get_text()
        if text.strip():
            lines.append(f"--- Page {i + 1} ---")
            lines.append(text)
    return "\n".join(lines)


# ── Mode: fetch (PDF/URL download) ──────────────────────────────────────────

def cmd_fetch(args):
    url = args.url
    session_dir = args.session_dir
    name = args.name or sanitize_name(url)
    ensure_dirs(session_dir)

    try:
        data, headers, final_url = download(url)
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
        print(f"ERROR: Failed to download {url}: {e}", file=sys.stderr)
        sys.exit(1)

    if detect_pdf(final_url, headers):
        pdf_path = os.path.join(session_dir, "pdfs", f"{name}.pdf")
        with open(pdf_path, "wb") as f:
            f.write(data)
        text = extract_pdf_text(pdf_path)
        text_path = os.path.join(session_dir, "pdfs", f"{name}.md")
        with open(text_path, "w") as f:
            f.write(f"# {name}\n# Source: {url}\n\n{text}")
        size_kb = len(data) / 1024
        print(f"PDF: {pdf_path} ({size_kb:.0f} KB)")
        print(f"Text: {text_path} ({text.count(chr(10))} lines)")
    else:
        raw_path = os.path.join(session_dir, "fetched", f"{name}.html")
        with open(raw_path, "wb") as f:
            f.write(data)
        print(f"HTML: {raw_path} ({len(data) / 1024:.0f} KB)")
        print("NOTE: Use WebFetch for HTML content extraction, not this script")


# ── Mode: search-log (append WebSearch results) ─────────────────────────────

def cmd_search_log(args):
    session_dir = args.session_dir
    role = args.role or "unknown"
    query = args.query or "unknown"
    ensure_dirs(session_dir)

    content = sys.stdin.read()
    log_path = os.path.join(session_dir, "search-log.md")

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(log_path, "a") as f:
        f.write(f"\n## {role} — {query}\n")
        f.write(f"Date: {timestamp}\n\n")
        f.write(content)
        f.write("\n---\n")

    print(f"Search log: {log_path} (appended {len(content)} chars)")


# ── Mode: webfetch (save WebFetch extracted content) ─────────────────────────

def cmd_webfetch(args):
    session_dir = args.session_dir
    name = args.name or "webfetch-content"
    url = args.url or "unknown"
    ctype = args.type or "html"
    ensure_dirs(session_dir)

    subdir = content_type_dir(ctype)
    content = sys.stdin.read()
    out_path = os.path.join(session_dir, subdir, f"{name}.md")
    with open(out_path, "w") as f:
        f.write(f"# {name}\n# Source: {url}\n\n{content}")

    print(f"WebFetch ({subdir}): {out_path} ({content.count(chr(10))} lines)")


# ── Mode: gh-json (save GitHub API results) ──────────────────────────────────

def cmd_gh_json(args):
    session_dir = args.session_dir
    name = args.name or "gh-results"
    ensure_dirs(session_dir)

    content = sys.stdin.read()
    out_path = os.path.join(session_dir, "repos", f"gh-{name}.json")
    # Validate it's JSON, pretty-print if so
    try:
        parsed = json.loads(content)
        content = json.dumps(parsed, indent=2)
    except json.JSONDecodeError:
        pass  # Save raw if not valid JSON

    with open(out_path, "w") as f:
        f.write(content)

    print(f"GitHub JSON: {out_path} ({len(content)} chars)")


# ── Mode: clone (shallow clone a git repo) ───────────────────────────────────

def cmd_clone(args):
    repo_url = args.repo_url
    session_dir = args.session_dir
    ensure_dirs(session_dir)

    # Derive repo name from URL
    if args.name:
        name = args.name
    else:
        name = repo_url.rstrip("/").split("/")[-1]
        if name.endswith(".git"):
            name = name[:-4]

    dest = os.path.join(session_dir, "repos", name)

    if os.path.exists(dest):
        print(f"SKIP: {dest} already exists")
        return

    result = subprocess.run(
        ["git", "clone", "--depth", "1", repo_url, dest],
        capture_output=True, text=True
    )

    if result.returncode == 0:
        # Count files
        file_count = sum(len(files) for _, _, files in os.walk(dest))
        print(f"Cloned: {dest} ({file_count} files)")
    else:
        print(f"ERROR: git clone failed: {result.stderr.strip()}", file=sys.stderr)
        sys.exit(1)


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Save research content to session directory",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # fetch
    p_fetch = subparsers.add_parser("fetch", help="Download URL (PDF auto-detected)")
    p_fetch.add_argument("url", help="URL to download")
    p_fetch.add_argument("session_dir", help="Session directory path")
    p_fetch.add_argument("--name", help="Sanitized filename (auto-generated if omitted)")

    # search-log
    p_search = subparsers.add_parser("search-log", help="Append WebSearch results to log (stdin)")
    p_search.add_argument("session_dir", help="Session directory path")
    p_search.add_argument("--role", help="Role name (e.g., ieee_searcher)")
    p_search.add_argument("--query", help="Search query used")

    # webfetch
    p_wf = subparsers.add_parser("webfetch", help="Save WebFetch content (stdin)")
    p_wf.add_argument("session_dir", help="Session directory path")
    p_wf.add_argument("--name", help="Sanitized filename")
    p_wf.add_argument("--url", help="Source URL")
    p_wf.add_argument("--type", help="Content type (blog_post, tutorial, forum_thread, app_note, trade_article, etc.)", default="html")

    # gh-json
    p_gh = subparsers.add_parser("gh-json", help="Save GitHub API JSON (stdin)")
    p_gh.add_argument("session_dir", help="Session directory path")
    p_gh.add_argument("--name", help="Query summary for filename")

    # clone
    p_clone = subparsers.add_parser("clone", help="Shallow-clone a git repo")
    p_clone.add_argument("repo_url", help="Repository URL to clone")
    p_clone.add_argument("session_dir", help="Session directory path")
    p_clone.add_argument("--name", help="Directory name (default: repo name)")

    args = parser.parse_args()

    commands = {
        "fetch": cmd_fetch,
        "search-log": cmd_search_log,
        "webfetch": cmd_webfetch,
        "gh-json": cmd_gh_json,
        "clone": cmd_clone,
    }
    commands[args.command](args)


if __name__ == "__main__":
    main()
