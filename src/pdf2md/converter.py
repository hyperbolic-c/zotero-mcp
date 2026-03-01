"""Convert Zotero PDF attachments to Markdown using a local MinerU API.

Usage:
    python -m pdf2md --library-id 1234567 [options]

PDF files are retrieved via the Zotero local API (localhost:23119), so Zotero 7
must be running with the local API enabled. The Markdown output mirrors Zotero's
storage layout:
    <output_dir>/<ATTACHMENT_KEY>/<stem>.md
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from pathlib import Path
from threading import Semaphore
from typing import Any

import httpx

from pyzotero import Zotero
from pyzotero.errors import PyZoteroError

try:
    from tqdm import tqdm

    TQDM_AVAILABLE = True
except ImportError:
    TQDM_AVAILABLE = False


DEFAULT_API_URL = "http://localhost:8000"
DEFAULT_OUTPUT_DIR = "./zotero_md_output"
DEFAULT_BACKEND = "pipeline"
PDF_CONTENT_TYPE = "application/pdf"
# Only these linkModes have a local file that can be fetched via /file
LOCAL_FILE_LINK_MODES = {"imported_file", "imported_url"}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Convert Zotero PDF attachments to Markdown via a local MinerU API.",
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
        help="Collection key or name to process (default: entire library)",
    )
    parser.add_argument(
        "--output-dir",
        default=DEFAULT_OUTPUT_DIR,
        help=f"Directory to save Markdown files (default: {DEFAULT_OUTPUT_DIR})",
    )
    parser.add_argument(
        "--api-url",
        default=os.environ.get("MINERU_API_URL", DEFAULT_API_URL),
        help=f"MinerU API base URL (default: {DEFAULT_API_URL}, or set MINERU_API_URL env var)",
    )
    parser.add_argument(
        "--lang",
        default="auto",
        help="Language hint for MinerU: 'en', 'ch', 'auto', etc. (default: auto)",
    )
    parser.add_argument(
        "--backend",
        default=DEFAULT_BACKEND,
        choices=["pipeline", "vlm-vllm-async-engine"],
        help=f"MinerU processing backend (default: {DEFAULT_BACKEND})",
    )
    parser.add_argument(
        "--skip-existing",
        action="store_true",
        help="Skip items whose output .md file already exists",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Maximum number of parent items to process",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable verbose/debug logging",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=4,
        help="Number of concurrent MinerU API requests (default: 4)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=300,
        help="MinerU API read timeout in seconds (default: 300)",
    )
    return parser.parse_args()


# ---------------------------------------------------------------------------
# Zotero helpers
# ---------------------------------------------------------------------------


def get_pdf_attachments(zot: Zotero, item: dict[str, Any]) -> list[dict[str, Any]]:
    """Return PDF child attachments for a parent item."""
    if item["data"].get("itemType") == "attachment":
        return []
    try:
        children = zot.children(item["key"])
    except PyZoteroError as exc:
        logging.debug("Could not fetch children for %s: %s", item["key"], exc)
        return []
    return [
        c for c in children
        if c["data"].get("contentType") == PDF_CONTENT_TYPE
        and c["data"].get("linkMode") in LOCAL_FILE_LINK_MODES
    ]


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


def fetch_pdf_bytes(zot: Zotero, attachment_key: str) -> bytes | None:
    """Fetch PDF file bytes via the Zotero local API."""
    try:
        return zot.file(attachment_key)
    except PyZoteroError as exc:
        logging.error("Failed to fetch file %s from Zotero: %s", attachment_key, exc)
        return None


# ---------------------------------------------------------------------------
# File helpers
# ---------------------------------------------------------------------------


def get_output_path(output_dir: Path, attachment: dict[str, Any]) -> Path:
    """Return output .md path mirroring Zotero storage layout."""
    key = attachment["key"]
    filename = attachment["data"].get("filename", f"{key}.pdf")
    stem = Path(filename).stem
    return output_dir / key / f"{stem}.md"


# ---------------------------------------------------------------------------
# MinerU API
# ---------------------------------------------------------------------------


def check_mineru_api(api_url: str, client: httpx.Client) -> bool:
    """Return True if the MinerU API is reachable."""
    try:
        client.get(api_url.rstrip("/") + "/docs")
        return True
    except httpx.HTTPError:
        return False


def submit_to_mineru(
    pdf_bytes: bytes,
    filename: str,
    api_url: str,
    lang: str,
    backend: str,
    client: httpx.Client,
) -> str | None:
    """POST PDF bytes to MinerU and return the Markdown string, or None on failure.

    Uses the legacy /file_parse synchronous API.
    """
    endpoint = api_url.rstrip("/") + "/file_parse"

    try:
        response = client.post(
            endpoint,
            files={"files": (filename, pdf_bytes, "application/pdf")},
            data={
                "lang_list": lang,
                "backend": backend,
                "return_md": "true",
            },
        )
        response.raise_for_status()
        result = response.json()
        results = result.get("results", {})
        if not results:
            logging.error("MinerU returned empty results for %s", filename)
            return None
        first_key = next(iter(results))
        md_content = results[first_key].get("md_content")
        if not md_content:
            logging.error("No md_content in MinerU response for %s", filename)
        return md_content
    except httpx.ConnectError:
        logging.error("Cannot connect to MinerU API at %s", api_url)
    except httpx.TimeoutException:
        logging.error("MinerU API timed out processing %s", filename)
    except httpx.HTTPStatusError as exc:
        logging.error("MinerU API HTTP error for %s: %s", filename, exc)
    except (ValueError, KeyError, StopIteration) as exc:
        logging.error("Unexpected MinerU response for %s: %s", filename, exc)
    return None


# ---------------------------------------------------------------------------
# Per-item processing
# ---------------------------------------------------------------------------


def process_item(
    zot: Zotero,
    item: dict[str, Any],
    args: argparse.Namespace,
    output_dir: Path,
    client: httpx.Client,
) -> list[str]:
    """Process a single item and return a list of result strings.

    Each result is one of: "success" | "skipped" | "no_pdf" | "fetch_errors" |
    "api_errors" | "write_errors".  A list is returned because one item may
    have multiple PDF attachments.
    """
    title = item["data"].get("title", "Untitled")
    item_key = item["key"]

    pdfs = get_pdf_attachments(zot, item)
    if not pdfs:
        return ["no_pdf"]

    results: list[str] = []
    for attachment in pdfs:
        output_path = get_output_path(output_dir, attachment)

        if args.skip_existing and output_path.exists():
            logging.info("Skipping (exists): %s", output_path)
            results.append("skipped")
            continue

        logging.info("Processing: '%s' [%s]", title, item_key)

        pdf_bytes = fetch_pdf_bytes(zot, attachment["key"])
        if pdf_bytes is None:
            results.append("fetch_errors")
            continue

        filename = attachment["data"].get("filename", f"{attachment['key']}.pdf")
        md_content = submit_to_mineru(
            pdf_bytes, filename, args.api_url, args.lang, args.backend, client
        )

        if md_content is None:
            results.append("api_errors")
            continue

        try:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(md_content, encoding="utf-8")
            logging.info("Saved: %s", output_path)
            results.append("success")
        except OSError as exc:
            logging.error("Failed to write %s: %s", output_path, exc)
            results.append("write_errors")

    return results


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

    # Shared httpx client: connection pool reused across all threads.
    # connect timeout = 5 s; read timeout = --timeout (default 300 s).
    http_client = httpx.Client(
        timeout=httpx.Timeout(5.0, read=float(args.timeout)),
    )

    # Pre-flight: verify MinerU is reachable
    if not check_mineru_api(args.api_url, http_client):
        logging.error(
            "MinerU API is not reachable at %s.\n"
            "Start it with: cd /path/to/MinerU/docker && docker compose --profile api up -d",
            args.api_url,
        )
        sys.exit(1)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

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
        "no_pdf": 0,
        "fetch_errors": 0,
        "api_errors": 0,
        "write_errors": 0,
    }

    pbar = tqdm(total=len(items), desc="Processing", unit="item") if TQDM_AVAILABLE else None

    # Semaphore limits in-flight futures to `--concurrency`, so we never load
    # the entire items list into the executor queue at once.
    sem = Semaphore(args.concurrency)

    with ThreadPoolExecutor(max_workers=args.concurrency) as executor:

        def submit_one(item: dict[str, Any]) -> Future[list[str]]:
            sem.acquire()
            f: Future[list[str]] = executor.submit(
                process_item, zot, item, args, output_dir, http_client
            )
            f.add_done_callback(lambda _: sem.release())
            return f

        futures = {submit_one(item): item for item in items}

        completed = 0
        for future in as_completed(futures):
            item = futures[future]
            completed += 1
            try:
                results = future.result()
            except Exception as exc:
                logging.error(
                    "Unexpected error processing %s: %s", item.get("key"), exc
                )
                results = ["api_errors"]
            for result in results:
                stats[result] += 1
            if pbar is not None:
                pbar.update(1)
            elif not TQDM_AVAILABLE:
                title = item["data"].get("title", "Untitled")
                print(f"[{completed}/{len(items)}] {title[:70]}", flush=True)

    if pbar is not None:
        pbar.close()

    http_client.close()

    # Summary
    logging.info("=" * 50)
    logging.info("Done.")
    logging.info("  Successful:     %d", stats["success"])
    logging.info("  Skipped:        %d", stats["skipped"])
    logging.info("  No PDF:         %d", stats["no_pdf"])
    logging.info("  Fetch errors:   %d", stats["fetch_errors"])
    logging.info("  API errors:     %d", stats["api_errors"])
    logging.info("  Write errors:   %d", stats["write_errors"])
    logging.info("Output: %s", output_dir.resolve())
