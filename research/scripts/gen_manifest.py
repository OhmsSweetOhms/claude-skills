#!/usr/bin/env python3
"""
gen_manifest.py — Generate session-manifest.json from session directory contents.

Usage:
  python3 gen_manifest.py <session_dir> --title "<title>" --query "<query>"

Scans pdfs/, blogs/, app-notes/, html/, repos/ and produces a structured
JSON manifest for vault generators. Reads metadata from file headers and
GitHub API JSON files.

Required args:
  session_dir   Path to .research/session-YYYYMMDD-HHMMSS/
  --title       Report title
  --query       Original user query (verbatim)

Optional args:
  --status      complete | partial (default: complete)
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path


def extract_header(md_path: str) -> dict:
    """Extract name (line 1) and source URL (line 2) from a content .md file.

    Expected format:
      # {name}
      # Source: {url}
    """
    info = {"title": "", "url": ""}
    try:
        with open(md_path, "r") as f:
            for i, line in enumerate(f):
                if i == 0 and line.startswith("# "):
                    info["title"] = line[2:].strip()
                elif i == 1 and line.startswith("# Source:"):
                    info["url"] = line.split(":", 1)[1].strip()
                elif i > 1:
                    break
    except (OSError, UnicodeDecodeError):
        pass
    return info


def scan_pdfs(session_dir: str) -> list:
    """Scan pdfs/ for .md files with companion .pdf."""
    pdf_dir = os.path.join(session_dir, "pdfs")
    if not os.path.isdir(pdf_dir):
        return []

    items = []
    for fname in sorted(os.listdir(pdf_dir)):
        if not fname.endswith(".md"):
            continue
        name = fname[:-3]
        md_path = os.path.join(pdf_dir, fname)
        pdf_path = os.path.join(pdf_dir, f"{name}.pdf")
        header = extract_header(md_path)

        item = {
            "name": name,
            "file": f"pdfs/{fname}",
            "title": header["title"] or name,
        }
        if os.path.isfile(pdf_path):
            item["pdf"] = f"pdfs/{name}.pdf"
        if header["url"]:
            item["url"] = header["url"]
        items.append(item)
    return items


def scan_content_dir(session_dir: str, subdir: str) -> list:
    """Scan a content directory (blogs/, app-notes/, html/) for .md files."""
    content_dir = os.path.join(session_dir, subdir)
    if not os.path.isdir(content_dir):
        return []

    items = []
    for fname in sorted(os.listdir(content_dir)):
        if not fname.endswith(".md"):
            continue
        name = fname[:-3]
        md_path = os.path.join(content_dir, fname)
        header = extract_header(md_path)

        items.append({
            "name": name,
            "file": f"{subdir}/{fname}",
            "url": header["url"] or "",
            "title": header["title"] or name,
        })
    return items


def scan_repos(session_dir: str) -> list:
    """Scan repos/ for cloned repos and gh-*.json metadata files."""
    repos_dir = os.path.join(session_dir, "repos")
    if not os.path.isdir(repos_dir):
        return []

    # Collect metadata from all gh-*.json files
    gh_items = {}  # owner/repo -> item dict
    json_files = {}  # owner/repo -> json filename

    for fname in sorted(os.listdir(repos_dir)):
        if not fname.startswith("gh-") or not fname.endswith(".json"):
            continue
        fpath = os.path.join(repos_dir, fname)
        try:
            with open(fpath, "r") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            continue

        # GitHub search API returns {items: [...]}
        repo_list = data.get("items", []) if isinstance(data, dict) else []
        for repo in repo_list:
            full_name = repo.get("full_name", "")
            if not full_name:
                continue
            gh_items[full_name] = {
                "name": full_name,
                "github_url": repo.get("html_url", f"https://github.com/{full_name}"),
                "description": repo.get("description") or "",
                "language": repo.get("language") or "",
                "stars": repo.get("stargazers_count"),
                "license": (repo.get("license") or {}).get("spdx_id") if isinstance(repo.get("license"), dict) else repo.get("license"),
            }
            json_files[full_name] = fname

    # Check for cloned repos (directories that aren't gh-*.json)
    cloned_dirs = set()
    for entry in os.listdir(repos_dir):
        entry_path = os.path.join(repos_dir, entry)
        if os.path.isdir(entry_path):
            cloned_dirs.add(entry)

    # Build final list: merge gh metadata with clone info
    results = []
    seen_names = set()

    for full_name, item in gh_items.items():
        repo_short = full_name.split("/")[-1] if "/" in full_name else full_name
        item["cloned"] = repo_short in cloned_dirs
        if item["cloned"]:
            item["cloned_to"] = f"repos/{repo_short}"
            cloned_dirs.discard(repo_short)
        item["metadata_file"] = f"repos/{json_files[full_name]}"
        results.append(item)
        seen_names.add(full_name)
        seen_names.add(repo_short)

    # Add cloned repos that weren't in any gh-*.json
    for dirname in sorted(cloned_dirs):
        if dirname in seen_names:
            continue
        results.append({
            "name": dirname,
            "github_url": f"https://github.com/{dirname}",
            "description": "",
            "language": "",
            "stars": None,
            "license": None,
            "cloned": True,
            "cloned_to": f"repos/{dirname}",
        })

    return results


def build_sources(session_dir: str) -> dict:
    """Build sources tracking from results/*.json files.

    Reads all per-role result files, deduplicates by url_or_doi,
    tracks which roles found each source and its download status.
    """
    results_dir = os.path.join(session_dir, "results")
    if not os.path.isdir(results_dir):
        return {"items": [], "summary": {}}

    # Collect all results across roles, keyed by url_or_doi
    sources = {}  # url_or_doi -> merged source dict

    for fname in sorted(os.listdir(results_dir)):
        if not fname.endswith(".json"):
            continue
        fpath = os.path.join(results_dir, fname)
        try:
            with open(fpath, "r") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            continue

        role = data.get("role", fname[:-5])  # fallback to filename without .json
        for result in data.get("results", []):
            url = result.get("url_or_doi", "")
            if not url:
                continue

            if url in sources:
                # Merge: add this role to found_by
                sources[url]["found_by"].append(role)
                # Upgrade status if this role has a better one
                new_status = result.get("download_status", "not_attempted")
                if new_status in ("saved", "cloned") and sources[url]["status"] not in ("saved", "cloned"):
                    sources[url]["status"] = new_status
                    sources[url]["local_paths"] = result.get("local_paths", [])
                    sources[url]["reason"] = None
            else:
                # Infer source type from role and result metadata
                source_type = _infer_source_type(role, result)
                sources[url] = {
                    "title": result.get("title", ""),
                    "url_or_doi": url,
                    "type": source_type,
                    "tier": None,  # Set during Stage 3 ranking, not here
                    "status": result.get("download_status", "not_attempted"),
                    "local_paths": result.get("local_paths", []),
                    "found_by": [role],
                    "reason": result.get("download_note"),
                }

    # Verify local paths actually exist on disk
    for source in sources.values():
        verified_paths = []
        for lp in source.get("local_paths", []):
            full_path = os.path.join(session_dir, lp)
            if os.path.exists(full_path):
                verified_paths.append(lp)
        source["local_paths"] = verified_paths
        # If paths claimed but none exist, downgrade status
        if source["status"] in ("saved", "cloned") and not verified_paths:
            source["status"] = "not_attempted"
            source["reason"] = "local_paths listed but files not found on disk"

    items = sorted(sources.values(), key=lambda s: (s["status"] != "saved", s["title"]))

    # Build summary counts
    summary = {}
    for item in items:
        s = item["status"]
        summary[s] = summary.get(s, 0) + 1
    summary["total"] = len(items)

    return {"items": items, "summary": summary}


def _infer_source_type(role: str, result: dict) -> str:
    """Infer source type from role and result metadata."""
    action = result.get("recommended_action", "")
    if action == "clone_repo":
        return "repo"
    if role == "ieee_searcher":
        return "paper"
    if role == "citation_tracer":
        return "paper"
    if role == "code_searcher":
        return "repo"
    # web_searcher — look at tags or URL for hints
    tags = result.get("tags", [])
    if any(t in tags for t in ["thesis", "dissertation"]):
        return "thesis"
    if any(t in tags for t in ["app_note", "vendor"]):
        return "app_note"
    if any(t in tags for t in ["blog", "tutorial"]):
        return "blog_post"
    if any(t in tags for t in ["trade_press", "trade_article"]):
        return "trade_article"
    if any(t in tags for t in ["presentation"]):
        return "presentation"
    return "webpage"


def generate_manifest(session_dir: str, title: str, query: str, status: str) -> dict:
    """Generate the full session manifest from directory contents."""
    # Extract session ID from path
    session_id = os.path.basename(os.path.normpath(session_dir))

    # Extract date from session ID (session-YYYYMMDD-HHMMSS)
    date_match = re.search(r"(\d{4})(\d{2})(\d{2})", session_id)
    date = f"{date_match.group(1)}-{date_match.group(2)}-{date_match.group(3)}" if date_match else ""

    content = {}

    pdfs = scan_pdfs(session_dir)
    if pdfs:
        content["pdfs"] = pdfs

    blogs = scan_content_dir(session_dir, "blogs")
    if blogs:
        content["blogs"] = blogs

    app_notes = scan_content_dir(session_dir, "app-notes")
    if app_notes:
        content["app-notes"] = app_notes

    html = scan_content_dir(session_dir, "html")
    if html:
        content["html"] = html

    repos = scan_repos(session_dir)
    if repos:
        content["repos"] = repos

    sources = build_sources(session_dir)

    return {
        "session_id": session_id,
        "title": title,
        "date": date,
        "query": query,
        "status": status,
        "sources": sources,
        "content": content,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Generate session-manifest.json from session directory contents"
    )
    parser.add_argument("session_dir", help="Path to session directory")
    parser.add_argument("--title", required=True, help="Report title")
    parser.add_argument("--query", required=True, help="Original user query")
    parser.add_argument("--status", default="complete", choices=["complete", "partial"])

    args = parser.parse_args()

    if not os.path.isdir(args.session_dir):
        print(f"ERROR: {args.session_dir} is not a directory", file=sys.stderr)
        sys.exit(1)

    manifest = generate_manifest(args.session_dir, args.title, args.query, args.status)

    out_path = os.path.join(args.session_dir, "session-manifest.json")
    with open(out_path, "w") as f:
        json.dump(manifest, f, indent=2)

    # Summary
    content = manifest["content"]
    counts = {k: len(v) for k, v in content.items()}
    total = sum(counts.values())
    parts = ", ".join(f"{v} {k}" for k, v in counts.items())
    print(f"Manifest: {out_path} ({total} content items: {parts})")

    # Sources summary
    src_summary = manifest.get("sources", {}).get("summary", {})
    if src_summary:
        src_total = src_summary.get("total", 0)
        saved = src_summary.get("saved", 0) + src_summary.get("cloned", 0)
        missing = src_total - saved
        print(f"Sources: {src_total} total, {saved} saved locally, {missing} need manual retrieval")
        for status, count in sorted(src_summary.items()):
            if status != "total" and count > 0:
                print(f"  {status}: {count}")


if __name__ == "__main__":
    main()
