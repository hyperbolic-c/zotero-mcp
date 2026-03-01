"""Download missing PDFs for Zotero items from open-access sources.

Usage:
    download-pdfs --library-id 1234567 --email your@email.com [options]

Attempts to find and download open-access PDFs for Zotero items that lack a
PDF attachment, then uploads the PDFs back to Zotero as child attachments.

Sources tried in order (configurable via --sources):
    1. Unpaywall  — best_oa_location.url_for_pdf
    2. Semantic Scholar — openAccessPdf.url
    3. Direct URL — item data.url field (validated as PDF)
    4. arXiv — extracts arXiv ID from extra field
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import sys
import tempfile
import time
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from threading import Lock, Semaphore
from typing import Any

import httpx

from pyzotero import Zotero
from pyzotero.errors import PyZoteroError

try:
    from tqdm import tqdm

    TQDM_AVAILABLE = True
except ImportError:
    TQDM_AVAILABLE = False


UNPAYWALL_BASE = "https://api.unpaywall.org/v2"
SEMANTIC_SCHOLAR_BASE = "https://api.semanticscholar.org/graph/v1"
PDF_CONTENT_TYPE = "application/pdf"
PDF_MAGIC = b"%PDF"

# Rate-limit state for Unpaywall (1 req/s)
_unpaywall_lock: Lock = Lock()
_last_unpaywall_call: list[float] = [0.0]

# Serialise all Zotero write operations
_zotero_write_lock: Lock = Lock()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Download missing PDFs for Zotero items from open-access sources.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--library-id",
        default=os.environ.get("ZOTERO_LIBRARY_ID"),
        required=not os.environ.get("ZOTERO_LIBRARY_ID"),
        help="Zotero library ID (or set ZOTERO_LIBRARY_ID env var)",
    )
    parser.add_argument(
        "--library-type",
        default="user",
        choices=["user", "group"],
        help="Zotero library type (default: user)",
    )
    parser.add_argument(
        "--collection",
        default=None,
        help="Collection name or key to process (default: entire library)",
    )
    parser.add_argument(
        "--email",
        default=os.environ.get("UNPAYWALL_EMAIL"),
        required=not os.environ.get("UNPAYWALL_EMAIL"),
        help="Email for Unpaywall API (or set UNPAYWALL_EMAIL env var)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Maximum number of items to process",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview mode: detect items needing PDFs without downloading",
    )
    parser.add_argument(
        "--sources",
        default="unpaywall,semantic_scholar,direct_url,arxiv",
        help="Comma-separated list of sources to try in order (default: unpaywall,semantic_scholar,direct_url,arxiv)",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=3,
        help="Number of concurrent downloads (default: 3)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=60,
        help="PDF download timeout in seconds (default: 60)",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable verbose/debug logging",
    )
    return parser.parse_args()


# ---------------------------------------------------------------------------
# Zotero helpers
# ---------------------------------------------------------------------------


def resolve_collection(zot: Zotero, name_or_key: str) -> str | None:
    """Resolve a collection name or key to its Zotero key."""
    try:
        coll = zot.collection(name_or_key)
        return coll["data"]["key"]
    except Exception:
        pass
    for coll in zot.everything(zot.collections()):
        if coll["data"].get("name", "").lower() == name_or_key.lower():
            return coll["data"]["key"]
    return None


def has_pdf_attachment(zot: Zotero, item: dict[str, Any]) -> bool:
    """Return True if the item already has a PDF child attachment."""
    try:
        children = zot.children(item["key"])
    except PyZoteroError as exc:
        logging.debug("Could not fetch children for %s: %s", item["key"], exc)
        return False
    return any(
        c["data"].get("contentType") == PDF_CONTENT_TYPE
        for c in children
        if c["data"].get("itemType") == "attachment"
    )


def extract_identifiers(item: dict[str, Any]) -> dict[str, str | None]:
    """Extract DOI, arXiv ID, and URL from a Zotero item."""
    data = item.get("data", {})
    doi: str | None = data.get("DOI") or None
    url: str | None = data.get("url") or None
    arxiv_id: str | None = None

    # Try to extract arXiv ID from the extra field
    extra = data.get("extra", "")
    if extra:
        # Match patterns like "arXiv:2301.00001" or "arXiv ID: 2301.00001v2"
        match = re.search(
            r"arxiv[:\s]+(\d{4}\.\d{4,5}(?:v\d+)?)",
            extra,
            re.IGNORECASE,
        )
        if match:
            arxiv_id = match.group(1)

    # Also check URL for arXiv
    if not arxiv_id and url:
        match = re.search(r"arxiv\.org/(?:abs|pdf)/(\d{4}\.\d{4,5}(?:v\d+)?)", url)
        if match:
            arxiv_id = match.group(1)

    return {"doi": doi, "arxiv_id": arxiv_id, "url": url}


# ---------------------------------------------------------------------------
# Download sources
# ---------------------------------------------------------------------------


def _download_pdf(url: str, client: httpx.Client) -> bytes | None:
    """Download bytes from url, validating it is a real PDF.

    Returns raw bytes if content is a PDF, None otherwise.
    """
    try:
        response = client.get(url, follow_redirects=True)
        response.raise_for_status()
        content = response.content
        content_type = response.headers.get("content-type", "")
        # Accept if Content-Type says PDF or if the file starts with PDF magic bytes
        if PDF_CONTENT_TYPE in content_type or content[:4] == PDF_MAGIC:
            return content
        logging.debug(
            "URL did not return a PDF (Content-Type: %s, magic: %s): %s",
            content_type,
            content[:4],
            url,
        )
        return None
    except httpx.HTTPStatusError as exc:
        logging.debug("HTTP error fetching %s: %s", url, exc)
        return None
    except httpx.HTTPError as exc:
        logging.debug("Network error fetching %s: %s", url, exc)
        return None


def fetch_from_unpaywall(
    doi: str,
    email: str,
    client: httpx.Client,
) -> bytes | None:
    """Try to download a PDF via the Unpaywall API."""
    if not doi:
        return None

    # Enforce 1 req/s rate limit across threads
    with _unpaywall_lock:
        elapsed = time.monotonic() - _last_unpaywall_call[0]
        if elapsed < 1.0:
            time.sleep(1.0 - elapsed)
        _last_unpaywall_call[0] = time.monotonic()

    url = f"{UNPAYWALL_BASE}/{doi}?email={email}"
    try:
        response = client.get(url, follow_redirects=True)
        response.raise_for_status()
        data = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        logging.debug("Unpaywall lookup failed for %s: %s", doi, exc)
        return None

    # Try best_oa_location first
    best = data.get("best_oa_location") or {}
    pdf_url = best.get("url_for_pdf")
    if pdf_url:
        logging.debug("Unpaywall best_oa_location PDF: %s", pdf_url)
        result = _download_pdf(pdf_url, client)
        if result is not None:
            return result

    # Fall back to iterating all oa_locations
    for loc in data.get("oa_locations") or []:
        pdf_url = loc.get("url_for_pdf")
        if pdf_url:
            logging.debug("Unpaywall oa_locations PDF: %s", pdf_url)
            result = _download_pdf(pdf_url, client)
            if result is not None:
                return result

    return None


def fetch_from_semantic_scholar(
    doi: str | None,
    arxiv_id: str | None,
    client: httpx.Client,
) -> bytes | None:
    """Try to download a PDF via Semantic Scholar's openAccessPdf field."""
    paper_id: str | None = None
    if doi:
        paper_id = f"DOI:{doi}"
    elif arxiv_id:
        paper_id = f"ARXIV:{arxiv_id}"

    if not paper_id:
        return None

    url = f"{SEMANTIC_SCHOLAR_BASE}/paper/{paper_id}?fields=openAccessPdf"
    try:
        response = client.get(url, follow_redirects=True)
        response.raise_for_status()
        data = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        logging.debug("Semantic Scholar lookup failed for %s: %s", paper_id, exc)
        return None

    oa_pdf = data.get("openAccessPdf") or {}
    pdf_url = oa_pdf.get("url")
    if not pdf_url:
        return None

    logging.debug("Semantic Scholar openAccessPdf: %s", pdf_url)
    return _download_pdf(pdf_url, client)


def fetch_from_direct_url(url: str | None, client: httpx.Client) -> bytes | None:
    """Try to download a PDF from the item's direct URL."""
    if not url:
        return None
    logging.debug("Trying direct URL: %s", url)
    return _download_pdf(url, client)


def fetch_from_arxiv(arxiv_id: str | None, client: httpx.Client) -> bytes | None:
    """Try to download a PDF from arXiv."""
    if not arxiv_id:
        return None
    pdf_url = f"https://arxiv.org/pdf/{arxiv_id}"
    logging.debug("Trying arXiv PDF: %s", pdf_url)
    return _download_pdf(pdf_url, client)


# ---------------------------------------------------------------------------
# Per-item processing
# ---------------------------------------------------------------------------


def process_item(
    zot: Zotero,
    item: dict[str, Any],
    args: argparse.Namespace,
    client: httpx.Client,
    sources: list[str],
) -> str:
    """Process a single item; return one of the stats keys."""
    data = item.get("data", {})
    item_type = data.get("itemType", "")
    item_key = item["key"]
    title = data.get("title", "Untitled")

    # Skip attachments and notes — they are not parent items
    if item_type in ("attachment", "note"):
        return "skipped"

    # Skip items that already have a PDF
    if has_pdf_attachment(zot, item):
        logging.debug("Already has PDF: '%s' [%s]", title, item_key)
        return "skipped"

    # Extract identifiers
    ids = extract_identifiers(item)
    doi = ids["doi"]
    arxiv_id = ids["arxiv_id"]
    url = ids["url"]

    if args.dry_run:
        logging.info(
            "[DRY-RUN] Would download PDF for: '%s' [%s] doi=%s arxiv=%s",
            title,
            item_key,
            doi,
            arxiv_id,
        )
        return "skipped"

    if not doi and not arxiv_id and not url:
        logging.debug("No identifiers for: '%s' [%s]", title, item_key)
        return "no_doi"

    logging.info("Searching for PDF: '%s' [%s]", title, item_key)

    # Try each source in order
    pdf_bytes: bytes | None = None
    for source in sources:
        if source == "unpaywall":
            pdf_bytes = fetch_from_unpaywall(doi or "", args.email, client)
        elif source == "semantic_scholar":
            pdf_bytes = fetch_from_semantic_scholar(doi, arxiv_id, client)
        elif source == "direct_url":
            pdf_bytes = fetch_from_direct_url(url, client)
        elif source == "arxiv":
            pdf_bytes = fetch_from_arxiv(arxiv_id, client)

        if pdf_bytes is not None:
            logging.info("Found PDF via %s for: '%s'", source, title)
            break

    if pdf_bytes is None:
        logging.info("No OA PDF found for: '%s' [%s]", title, item_key)
        return "not_found"

    # Build a clean filename from the title or DOI
    safe_title = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", title[:80]).strip()
    filename = f"{safe_title}.pdf" if safe_title else f"{item_key}.pdf"

    # Write to a temporary file, then upload to Zotero
    tmp_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(pdf_bytes)
            tmp_path = tmp.name

        with _zotero_write_lock:
            zot.attachment_both([(filename, tmp_path)], parentid=item_key)

        logging.info("Uploaded PDF for: '%s' [%s]", title, item_key)
        return "success"
    except OSError as exc:
        logging.error("Failed to write temp file for '%s': %s", title, exc)
        return "fetch_error"
    except PyZoteroError as exc:
        logging.error("Failed to upload PDF for '%s' [%s]: %s", title, item_key, exc)
        return "upload_error"
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    args = parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    sources = [s.strip() for s in args.sources.split(",") if s.strip()]
    valid_sources = {"unpaywall", "semantic_scholar", "direct_url", "arxiv"}
    invalid = set(sources) - valid_sources
    if invalid:
        logging.error("Unknown sources: %s. Valid: %s", invalid, valid_sources)
        sys.exit(1)

    logging.info("Sources to try (in order): %s", sources)

    # Shared httpx client: connection pool reused across all threads.
    http_client = httpx.Client(
        timeout=httpx.Timeout(5.0, read=float(args.timeout)),
        follow_redirects=True,
    )

    # Initialise Zotero client (local=True connects to Zotero 7 at localhost:23119)
    try:
        zot = Zotero(
            library_id=args.library_id,
            library_type=args.library_type,
            local=True,
        )
    except Exception as exc:
        logging.error("Failed to initialise Zotero client: %s", exc)
        sys.exit(1)

    # Resolve collection if specified
    collection_id: str | None = None
    if args.collection:
        collection_id = resolve_collection(zot, args.collection)
        if collection_id is None:
            logging.error("Collection '%s' not found.", args.collection)
            sys.exit(1)
        logging.info("Scoped to collection: %s (%s)", args.collection, collection_id)

    # Fetch items
    logging.info("Fetching items from Zotero...")
    try:
        if collection_id:
            items = zot.everything(zot.collection_items_top(collection_id))
        else:
            items = zot.everything(zot.top())
    except PyZoteroError as exc:
        logging.error("Failed to fetch items: %s", exc)
        sys.exit(1)

    if args.limit:
        items = items[: args.limit]

    logging.info("Found %d top-level items to process.", len(items))

    stats: dict[str, int] = {
        "success": 0,
        "skipped": 0,
        "no_doi": 0,
        "not_found": 0,
        "fetch_error": 0,
        "upload_error": 0,
    }

    pbar = tqdm(total=len(items), desc="Processing", unit="item") if TQDM_AVAILABLE else None

    # Semaphore limits in-flight futures to `--concurrency`, so we never load
    # the entire items list into the executor queue at once.
    sem = Semaphore(args.concurrency)

    with ThreadPoolExecutor(max_workers=args.concurrency) as executor:

        def submit_one(item: dict[str, Any]) -> Future[str]:
            sem.acquire()
            f: Future[str] = executor.submit(
                process_item, zot, item, args, http_client, sources
            )
            f.add_done_callback(lambda _: sem.release())
            return f

        futures = {submit_one(item): item for item in items}

        completed = 0
        for future in as_completed(futures):
            item = futures[future]
            completed += 1
            try:
                result = future.result()
            except Exception as exc:
                logging.error(
                    "Unexpected error processing %s: %s", item.get("key"), exc
                )
                result = "upload_error"
            stats[result] += 1
            if pbar is not None:
                pbar.update(1)
            else:
                title = item["data"].get("title", "Untitled")
                print(f"[{completed}/{len(items)}] {title[:70]}", flush=True)

    if pbar is not None:
        pbar.close()

    http_client.close()

    # Summary
    logging.info("=" * 50)
    logging.info("Done.")
    logging.info("  Downloaded & uploaded: %d", stats["success"])
    logging.info("  Skipped (has PDF / dry-run): %d", stats["skipped"])
    logging.info("  No identifiers:        %d", stats["no_doi"])
    logging.info("  OA PDF not found:      %d", stats["not_found"])
    logging.info("  Fetch/write errors:    %d", stats["fetch_error"])
    logging.info("  Upload errors:         %d", stats["upload_error"])
