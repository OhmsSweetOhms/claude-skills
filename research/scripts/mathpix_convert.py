#!/usr/bin/env python3
"""mathpix_convert.py — Mathpix Convert API wrapper for /research skill.

Converts a PDF to Mathpix Markdown via the v3/pdf endpoint. Used by
fetch_and_save.py as the primary extractor; pymupdf is the fallback.

Env vars (required):
    MATHPIX_APP_ID
    MATHPIX_APP_KEY

API docs:  https://docs.mathpix.com/reference/post_v3-pdf
Cost:      $0.005/page (v3/pdf).
Caveat:    v3/pdf does NOT support PDFs with handwritten content.
           For those, rasterize per-page and submit via v3/text instead
           (not implemented here).
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import requests


MATHPIX_BASE = "https://api.mathpix.com/v3"
DEFAULT_POLL_INTERVAL = 3.0
DEFAULT_TIMEOUT_S = 900.0   # 15 minutes — generous for long papers


# ---------------------------------------------------------------------------
# Exceptions — caller (fetch_and_save.py) catches MathpixError to fall back
# ---------------------------------------------------------------------------

class MathpixError(Exception):
    """Base — any failure that should trigger pymupdf fallback."""

class MathpixAuthError(MathpixError):
    """Missing or invalid credentials."""

class MathpixTimeoutError(MathpixError):
    """Server did not complete conversion within the timeout window."""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def _headers() -> dict:
    app_id = os.environ.get("MATHPIX_APP_ID")
    app_key = os.environ.get("MATHPIX_APP_KEY")
    if not (app_id and app_key):
        raise MathpixAuthError(
            "MATHPIX_APP_ID and MATHPIX_APP_KEY must be set."
        )
    return {"app_id": app_id, "app_key": app_key}


def is_available() -> bool:
    """Cheap pre-flight: env vars present? Does not validate against API."""
    try:
        _headers()
        return True
    except MathpixAuthError:
        return False


def convert_pdf(
    pdf_path: str | Path,
    poll_interval: float = DEFAULT_POLL_INTERVAL,
    timeout: float = DEFAULT_TIMEOUT_S,
) -> tuple[str, dict]:
    """Submit a PDF; return (markdown_text, provenance_dict).

    Provenance keys:
        backend:          "mathpix"
        pages:            int (from server response, may be 0 if unknown)
        duration_s:       float, end-to-end wall time
        pdf_id:           Mathpix's id for this submission

    Raises:
        MathpixAuthError      — env vars missing
        MathpixTimeoutError   — server polling exceeded `timeout`
        MathpixError          — submit/poll/fetch failed
    """
    pdf_path = Path(pdf_path)
    if not pdf_path.exists():
        raise FileNotFoundError(pdf_path)

    options = {
        "conversion_formats": {"md": True},
        "math_inline_delimiters": ["$", "$"],
        "math_display_delimiters": ["$$", "$$"],
        "rm_spaces": True,
    }

    t0 = time.monotonic()

    # 1. Submit
    try:
        with pdf_path.open("rb") as fh:
            r = requests.post(
                f"{MATHPIX_BASE}/pdf",
                headers=_headers(),
                data={"options_json": json.dumps(options)},
                files={"file": fh},
                timeout=120,
            )
        r.raise_for_status()
    except requests.RequestException as e:
        raise MathpixError(f"submit failed: {e}") from e

    pdf_id = r.json().get("pdf_id")
    if not pdf_id:
        raise MathpixError(f"no pdf_id in response: {r.text[:200]}")

    # 2. Poll
    pages = 0
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            s = requests.get(
                f"{MATHPIX_BASE}/pdf/{pdf_id}",
                headers=_headers(),
                timeout=30,
            )
            s.raise_for_status()
        except requests.RequestException as e:
            raise MathpixError(f"poll failed: {e}") from e

        body = s.json()
        status = body.get("status")
        pages = body.get("num_pages", pages)
        if status == "completed":
            break
        if status == "error":
            raise MathpixError(f"server error: {body}")
        time.sleep(poll_interval)
    else:
        raise MathpixTimeoutError(
            f"conversion did not complete within {timeout}s (pdf_id={pdf_id})"
        )

    # 3. Fetch markdown
    try:
        md = requests.get(
            f"{MATHPIX_BASE}/pdf/{pdf_id}.md",
            headers=_headers(),
            timeout=60,
        )
        md.raise_for_status()
    except requests.RequestException as e:
        raise MathpixError(f"markdown fetch failed: {e}") from e

    provenance = {
        "backend": "mathpix",
        "pages": pages,
        "duration_s": round(time.monotonic() - t0, 2),
        "pdf_id": pdf_id,
    }
    return md.text, provenance


# ---------------------------------------------------------------------------
# CLI — for quick testing outside fetch_and_save.py
# ---------------------------------------------------------------------------

def main() -> int:
    import argparse

    ap = argparse.ArgumentParser(description="Convert a PDF via Mathpix.")
    ap.add_argument("pdf", type=Path)
    ap.add_argument("-o", "--output", type=Path,
                    help="Output .md path (default: stdout)")
    ap.add_argument("--provenance", type=Path,
                    help="Optional path for provenance .json sidecar")
    args = ap.parse_args()

    try:
        md, prov = convert_pdf(args.pdf)
    except MathpixError as e:
        print(f"error: {type(e).__name__}: {e}", file=sys.stderr)
        return 2

    if args.output:
        args.output.write_text(md, encoding="utf-8")
        print(f"wrote {args.output} ({prov['pages']}p, {prov['duration_s']}s)",
              file=sys.stderr)
    else:
        sys.stdout.write(md)

    if args.provenance:
        args.provenance.write_text(json.dumps(prov, indent=2))

    return 0


if __name__ == "__main__":
    sys.exit(main())
