# Mathpix PDF Conversion

High-fidelity PDF → Markdown conversion via the Mathpix Convert API.
Used as the primary extractor in `scripts/fetch_and_save.py`, with
pymupdf as the fallback.

## When Mathpix runs

It's the default. The wrapper (`scripts/mathpix_convert.py`) is called
first in `extract_pdf_text`. It produces substantially better output
than pymupdf for:

- Math equations (LaTeX preserved; pymupdf flattens to broken prose)
- Tables (real GFM tables; pymupdf concatenates cells)
- Two-column journal layouts
- Section structure (real headings; pymupdf produces flat text)

## When it falls back to pymupdf

`fetch_and_save.py` catches `MathpixError` (and subclasses) and falls
through to pymupdf so research sessions never hard-fail. Causes:

- `MathpixAuthError` — `MATHPIX_APP_ID` or `MATHPIX_APP_KEY` not set
- `MathpixTimeoutError` — 15-minute server timeout exceeded
- `MathpixError` — network failure, HTTP 4xx/5xx, server reports
  `status: "error"` mid-processing

Provenance is recorded either way so the user can see which backend
produced any given `.md`.

## Setup

```bash
export MATHPIX_APP_ID="..."
export MATHPIX_APP_KEY="..."
```

Verify with the CLI before running a session:

```bash
python scripts/mathpix_convert.py path/to/test.pdf -o /tmp/test.md
```

## Cost

- v3/pdf endpoint: $0.005 per page
- Free tier: ~20 pages/month
- Paid: $19.99 one-time setup → $29 starter credit (~5,800 pages)
- Server retains submitted PDFs for 30 days by default

Per-PDF cost is implicit in the page count recorded in the provenance
sidecar. Sum across a session for total spend.

## Provenance sidecar

Every extracted PDF gets `<stem>.extraction.json` next to `<stem>.md`:

```json
{
  "backend": "mathpix",
  "pages": 14,
  "duration_s": 23.4,
  "pdf_id": "2024_01_15_abc123def",
  "fallback_reason": null
}
```

When fallback fires, the shape is:

```json
{
  "backend": "pymupdf",
  "pages": 14,
  "duration_s": 0.8,
  "pdf_id": null,
  "fallback_reason": "MathpixAuthError: MATHPIX_APP_ID and MATHPIX_APP_KEY must be set."
}
```

Downstream consumers (the manifest writer, RAG ingestion, future
re-extraction loops) read the sidecar to decide whether the markdown
is high-fidelity or needs upgrading.

## Known limitations

**Handwritten content.** The v3/pdf endpoint does not handle handwritten
PDFs. For scanned notebooks or whiteboard photos, the correct path is
to rasterize each page and submit through v3/text — not implemented in
this wrapper. If a `/research` session pulls handwritten material, the
sidebar's `pages` field will likely be very low and the markdown will
look wrong; that's a signal to handle it manually.

**Strange layouts.** Magazine-style pages with sidebars, image-heavy
infographics, or unusual two-column variations may produce poor results.
The `.extraction.json` sidecar is the user's escape hatch — if Mathpix
output looks wrong, re-run with `MATHPIX_APP_ID=""` to force the
pymupdf path, or fetch the PDF manually.

## API reference

- `POST /v3/pdf` — submit, returns `pdf_id`
- `GET /v3/pdf/{id}` — poll status
- `GET /v3/pdf/{id}.md` — fetch rendered markdown
- Full docs: https://docs.mathpix.com/reference/post_v3-pdf
