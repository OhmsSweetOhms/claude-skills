#!/usr/bin/env python3
"""
fetch_and_save.py — Download a URL and save content to research session directory.

Auto-detects PDF vs HTML:
  - PDF: saves to pdfs/, extracts text via pymupdf to fetched/
  - HTML: saves raw HTML, not used (WebFetch handles HTML extraction)

Usage:
  python3 fetch_and_save.py <url> <session_dir> [--name <sanitized-name>]

Examples:
  python3 fetch_and_save.py "https://example.edu/thesis.pdf" .research/session-20260322-142449
  python3 fetch_and_save.py "https://example.edu/thesis.pdf" .research/session-20260322-142449 --name smith-gps-thesis-2020
"""

import argparse
import os
import re
import ssl
import sys
import urllib.request
import urllib.error


def sanitize_name(url: str) -> str:
    """Generate a sanitized filename from a URL."""
    # Take the last path component
    path = url.rstrip("/").split("/")[-1]
    # Remove query strings
    path = path.split("?")[0]
    # Remove extension for now
    base, ext = os.path.splitext(path)
    if not base or base == "download":
        # Fallback: use second-to-last path component
        parts = url.rstrip("/").split("/")
        for part in reversed(parts):
            if part and part != "download" and "." not in part[:5]:
                base = part
                break
        else:
            base = "document"
    # Sanitize: lowercase, replace non-alphanumeric with hyphens
    base = re.sub(r"[^a-z0-9]+", "-", base.lower()).strip("-")
    # Truncate to reasonable length
    base = base[:80]
    return base


def detect_pdf(url: str, headers: dict) -> bool:
    """Detect if a URL points to a PDF based on URL and response headers."""
    url_lower = url.lower()
    if url_lower.endswith(".pdf"):
        return True
    content_type = headers.get("Content-Type", "")
    if "application/pdf" in content_type:
        return True
    # Known PDF hosts
    pdf_hosts = ["digitalcommons", "arxiv.org/pdf", "ntrs.nasa.gov/api/citations",
                 "yorkspace.library", "etd.auburn.edu"]
    for host in pdf_hosts:
        if host in url_lower:
            return True
    return False


def download(url: str) -> tuple:
    """Download URL, return (data, headers, final_url)."""
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (research-skill fetch_and_save.py)"
    })
    # Try with SSL verification first, fall back to unverified if cert fails
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
        return f"[pymupdf not installed — run: python3 -m pip install pymupdf]\n"

    doc = pymupdf.open(pdf_path)
    lines = []
    for i, page in enumerate(doc):
        text = page.get_text()
        if text.strip():
            lines.append(f"--- Page {i + 1} ---")
            lines.append(text)
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Fetch URL and save to research session")
    parser.add_argument("url", help="URL to fetch")
    parser.add_argument("session_dir", help="Session directory (e.g., .research/session-20260322-142449)")
    parser.add_argument("--name", help="Sanitized name (auto-generated if omitted)")
    args = parser.parse_args()

    url = args.url
    session_dir = args.session_dir
    name = args.name or sanitize_name(url)

    # Ensure directories exist
    pdfs_dir = os.path.join(session_dir, "pdfs")
    fetched_dir = os.path.join(session_dir, "fetched")
    os.makedirs(pdfs_dir, exist_ok=True)
    os.makedirs(fetched_dir, exist_ok=True)

    # Download
    try:
        data, headers, final_url = download(url)
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
        print(f"ERROR: Failed to download {url}: {e}", file=sys.stderr)
        sys.exit(1)

    is_pdf = detect_pdf(final_url, headers)

    if is_pdf:
        # Save PDF
        pdf_path = os.path.join(pdfs_dir, f"{name}.pdf")
        with open(pdf_path, "wb") as f:
            f.write(data)

        # Extract text
        text = extract_pdf_text(pdf_path)
        text_path = os.path.join(fetched_dir, f"{name}.md")
        with open(text_path, "w") as f:
            f.write(f"# {name}\n")
            f.write(f"# Source: {url}\n\n")
            f.write(text)

        size_kb = len(data) / 1024
        lines = text.count("\n")
        print(f"PDF: {pdf_path} ({size_kb:.0f} KB)")
        print(f"Text: {text_path} ({lines} lines)")
    else:
        # Not a PDF — save raw content but note it
        # (WebFetch should be used for HTML extraction, not this script)
        raw_path = os.path.join(fetched_dir, f"{name}.html")
        with open(raw_path, "wb") as f:
            f.write(data)
        size_kb = len(data) / 1024
        print(f"HTML: {raw_path} ({size_kb:.0f} KB)")
        print(f"NOTE: Use WebFetch for HTML content extraction, not this script")


if __name__ == "__main__":
    main()
