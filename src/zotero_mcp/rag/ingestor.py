"""Ingestor module for advanced RAG."""

import glob
import json
import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from zotero_mcp.chroma_client import CHROMA_GET_MAX_BATCH
from zotero_mcp.utils import format_creators, IndexingProgress

# Import from compat to allow test monkeypatching
# Note: We import the module, not the attributes, so they can be dynamically resolved
from . import compat

from .chunkers import get_chunking_backend

logger = logging.getLogger(__name__)


def _compute_chroma_max_batch() -> int:
    """Derive the ChromaDB upsert batch limit from the SQLite compile-time constant.

    ChromaDB's SqlEmbeddingsQueue uses VARIABLES_PER_RECORD=6 and reads
    MAX_VARIABLE_NUMBER from SQLite's compile options (falling back to 999).
    We mirror that logic so our limit stays in sync with the installed SQLite.
    """
    import sqlite3

    _VARIABLES_PER_RECORD = 6  # mirrors chromadb/db/mixins/embeddings_queue.py
    try:
        con = sqlite3.connect(":memory:")
        for row in con.execute("pragma compile_options"):
            if "MAX_VARIABLE_NUMBER" in row[0]:
                return int(row[0].split("=")[1]) // _VARIABLES_PER_RECORD
        # pragma didn't surface it — fall back to runtime limit
        return con.getlimit(9) // _VARIABLES_PER_RECORD
    except Exception:
        return 999 // _VARIABLES_PER_RECORD
    finally:
        try:
            con.close()
        except Exception:
            pass


CHROMA_MAX_BATCH: int = _compute_chroma_max_batch()


class Ingestor:
    """Handles data ingestion for advanced RAG."""

    def __init__(
        self,
        chroma_client,
        refs_client,
        config: dict,
        db_path,
        config_path,
        get_items_from_source_fn,
        parse_creators_fn,
    ):
        self.chroma_client = chroma_client
        self.refs_client = refs_client
        self.config = config
        self.db_path = db_path
        self.config_path = config_path
        self.get_items_from_source_fn = get_items_from_source_fn
        self.parse_creators_fn = parse_creators_fn or self._default_parse_creators_string
        self.chunk_cfg = config.get("chunk", {})
        self.ingest_cfg = config.get("ingest", {})
        self._chunking_backend = get_chunking_backend(self.chunk_cfg)

    @staticmethod
    def _default_parse_creators_string(creators_str: str) -> list[dict[str, str]]:
        if not creators_str:
            return []
        creators: list[dict[str, str]] = []
        for creator in creators_str.split(";"):
            creator = creator.strip()
            if not creator:
                continue
            if "," in creator:
                last, first = creator.split(",", 1)
                creators.append(
                    {
                        "creatorType": "author",
                        "firstName": first.strip(),
                        "lastName": last.strip(),
                    }
                )
            else:
                creators.append({"creatorType": "author", "name": creator})
        return creators

    def _build_meta_text(self, item: dict[str, Any]) -> str:
        data = item.get("data", {})
        creators = format_creators(data.get("creators", []))
        parts = [
            data.get("title", ""),
            creators,
            data.get("abstractNote", ""),
            data.get("extra", ""),
        ]
        return "\n\n".join([part for part in parts if part]).strip()

    def _base_metadata(self, item: dict[str, Any]) -> dict[str, Any]:
        data = item.get("data", {})
        return {
            "item_key": item.get("key", ""),
            "item_type": data.get("itemType", ""),
            "title": data.get("title", ""),
            "date": data.get("date", ""),
            "creators": format_creators(data.get("creators", [])),
            "source_type": "abstract",
            "has_md_source": False,
        }

    def _iter_markdown_files(self, attachment_key: str) -> list[Path]:
        md_root = self.config.get("md_root", "")
        if not md_root:
            return []
        pattern = str(Path(md_root) / attachment_key / "*.md")
        paths = [Path(p) for p in glob.glob(pattern)]
        return sorted(paths)

    def _strip_images(self, text: str) -> str:
        if not self.ingest_cfg.get("strip_images", True):
            return text
        return "\n".join(line for line in text.splitlines() if not line.strip().startswith("!["))

    def _build_item_chunks(
        self,
        item: dict[str, Any],
        attachment_keys: list[str],
    ) -> tuple[
        list[str],
        list[dict[str, Any]],
        list[str],
        list[str],
        list[dict[str, Any]],
        list[str],
    ]:
        """Build chunks from item and its attachments."""
        item_key = item.get("key", "")
        base_meta = self._base_metadata(item)

        docs: list[str] = []
        metas: list[dict[str, Any]] = []
        ids: list[str] = []
        ref_docs: list[str] = []
        ref_metas: list[dict[str, Any]] = []
        ref_ids: list[str] = []

        for attachment_key in sorted(set(attachment_keys)):
            md_files = self._iter_markdown_files(attachment_key)
            if not md_files:
                continue

            merged_text_parts = []
            for md_file in md_files:
                try:
                    merged_text_parts.append(md_file.read_text(encoding="utf-8", errors="ignore"))
                except Exception:
                    continue
            merged_text = self._strip_images("\n\n".join(merged_text_parts)).strip()
            if not merged_text:
                continue

            records = self._chunking_backend.chunk(merged_text)
            body_records = [r for r in records if r.chunk_kind != "references"]
            reference_records = [r for r in records if r.chunk_kind == "references"]

            for record in body_records:
                docs.append(record.text)
                chunk_meta = dict(base_meta)
                chunk_meta.update(
                    {
                        "attachment_key": attachment_key,
                        "chunk_kind": "content",
                        "section_title": record.section_title,
                        "source_type": "mineru_md",
                        "has_md_source": True,
                    }
                )
                chunk_meta.update(record.extra_metadata)
                metas.append(chunk_meta)
                ids.append(f"{item_key}:{attachment_key}:{record.chunk_index}")

            for record in reference_records:
                ref_num = int(record.extra_metadata.get("ref_num", -1))
                if ref_num < 0:
                    continue
                ref_docs.append(record.text)
                ref_meta = {
                    "item_key": item_key,
                    "attachment_key": attachment_key,
                    "ref_num": ref_num,
                    "section_title": "references",
                    "chunk_kind": "references",
                }
                ref_metas.append(ref_meta)
                ref_ids.append(f"{item_key}:{attachment_key}:ref:{ref_num}")

        meta_text = self._build_meta_text(item)
        if meta_text:
            meta_meta = dict(base_meta)
            meta_meta.update(
                {
                    "attachment_key": "meta",
                    "chunk_kind": "meta",
                    "section_title": "metadata",
                    "source_type": "abstract",
                    "has_md_source": len(docs) > 0,
                }
            )
            docs.append(meta_text)
            metas.append(meta_meta)
            ids.append(f"{item_key}:meta:0")

        return docs, metas, ids, ref_docs, ref_metas, ref_ids

    def _collect_attachment_map(self, limit: int | None = None) -> dict[str, list[str]]:
        if not compat.is_local_mode():
            return {}
        item_to_attachments: dict[str, list[str]] = {}
        try:
            with compat.LocalZoteroReader(db_path=self.db_path) as reader:
                local_items = reader.get_items_with_text(limit=limit, include_fulltext=False)
                for local_item in local_items:
                    keys = [
                        attachment_key
                        for attachment_key, _, _ in reader._iter_parent_attachments(local_item.item_id)
                        if attachment_key
                    ]
                    item_to_attachments[local_item.key] = keys
        except Exception:
            return {}
        return item_to_attachments

    def _fetch_items_and_attachments_from_local_db(
        self, limit: int | None = None,
    ) -> tuple[list[dict[str, Any]], dict[str, list[str]]]:
        """Fetch items and their attachment map from local SQLite in one pass.

        This is the primary data-loading path for advanced_rag mode.
        It avoids the Zotero HTTP API entirely — only the on-disk
        ``zotero.sqlite`` is required (Zotero does not need to be running).

        Returns:
            A ``(api_items, attachment_map)`` tuple where *api_items* is a list
            of dicts in the same format produced by the legacy source loader,
            and *attachment_map* maps each
            parent item key to a list of attachment keys.

        Raises:
            FileNotFoundError: If the Zotero database cannot be located.
            Exception: On any unexpected SQLite / IO error (caller should
            fall back to the API path).
        """
        # --- resolve db_path / pdf_max_pages from config (same as legacy) ---
        zotero_db_path = self.db_path
        pdf_max_pages = None
        config_path = self.config_path
        try:
            if config_path and os.path.exists(config_path):
                with open(config_path) as _f:
                    _cfg = json.load(_f)
                    sem_cfg = _cfg.get("semantic_search", {})
                    pdf_max_pages = sem_cfg.get("extraction", {}).get("pdf_max_pages")
                    if not zotero_db_path:
                        zotero_db_path = sem_cfg.get("zotero_db_path")
        except Exception:
            pass

        with compat.LocalZoteroReader(db_path=zotero_db_path, pdf_max_pages=pdf_max_pages) as reader:
            sys.stderr.write("Scanning local Zotero database for items...\n")
            local_items = reader.get_items_with_text(limit=limit, include_fulltext=False)
            sys.stderr.write(f"Found {len(local_items)} candidate items.\n")

            # --- dedup: prefer journalArticle over preprint with same DOI/title ---
            def _norm(s: str | None) -> str | None:
                return "".join(s.lower().split()) if s else None

            key_to_best: dict[tuple[str, str | None], Any] = {}
            for it in local_items:
                doi_key = ("doi", _norm(it.doi)) if it.doi else None
                title_key = ("title", _norm(it.title)) if it.title else None
                prefer_types = {"journalArticle": 2, "preprint": 1}
                for k in (doi_key, title_key):
                    if not k:
                        continue
                    cur = key_to_best.get(k)
                    if cur is None:
                        key_to_best[k] = it
                    else:
                        if prefer_types.get(it.item_type or "", 0) > prefer_types.get(cur.item_type or "", 0):
                            key_to_best[k] = it

            filtered_items = []
            for it in local_items:
                if it.item_type == "preprint":
                    doi_key = ("doi", _norm(it.doi)) if it.doi else None
                    title_key = ("title", _norm(it.title)) if it.title else None
                    drop = False
                    for k in (doi_key, title_key):
                        if not k:
                            continue
                        best = key_to_best.get(k)
                        if best is not None and best is not it and best.item_type == "journalArticle":
                            drop = True
                            break
                    if drop:
                        continue
                filtered_items.append(it)

            if len(filtered_items) != len(local_items):
                sys.stderr.write(
                    f"After dedup: {len(filtered_items)} items "
                    f"(dropped {len(local_items) - len(filtered_items)} preprints).\n"
                )

            # --- build attachment map AND convert items in one pass ---
            api_items: list[dict[str, Any]] = []
            attachment_map: dict[str, list[str]] = {}

            for item in filtered_items:
                # attachment keys
                att_keys = [
                    att_key
                    for att_key, _, _ in reader._iter_parent_attachments(item.item_id)
                    if att_key
                ]
                if att_keys:
                    attachment_map[item.key] = att_keys

                # convert to API-compatible dict
                api_item: dict[str, Any] = {
                    "key": item.key,
                    "version": 0,
                    "data": {
                        "key": item.key,
                        "itemType": item.item_type or "journalArticle",
                        "title": item.title or "",
                        "abstractNote": item.abstract or "",
                        "extra": item.extra or "",
                        "dateAdded": item.date_added,
                        "dateModified": item.date_modified,
                        "creators": (
                            self.parse_creators_fn(item.creators)
                            if item.creators
                            else []
                        ),
                    },
                }
                if item.notes:
                    api_item["data"]["notes"] = item.notes
                api_items.append(api_item)

            logger.info(
                "Local DB: %d items, %d with attachments",
                len(api_items),
                len(attachment_map),
            )
            return api_items, attachment_map

    def _flush_batch(
        self,
        batch_docs: list[str],
        batch_metas: list[dict[str, Any]],
        batch_ids: list[str],
        stats: dict[str, Any],
    ) -> None:
        existing_ids = self.chroma_client.get_existing_ids(batch_ids)
        for start in range(0, len(batch_ids), CHROMA_MAX_BATCH):
            end = start + CHROMA_MAX_BATCH
            self.chroma_client.upsert_documents(
                batch_docs[start:end],
                batch_metas[start:end],
                batch_ids[start:end],
            )
        for doc_id in batch_ids:
            if doc_id in existing_ids:
                stats["updated_chunks"] += 1
            else:
                stats["added_chunks"] += 1

    def _flush_refs_batch(
        self,
        ref_docs: list[str],
        ref_metas: list[dict[str, Any]],
        ref_ids: list[str],
    ) -> None:
        if not ref_ids:
            return
        for start in range(0, len(ref_ids), CHROMA_MAX_BATCH):
            end = start + CHROMA_MAX_BATCH
            if hasattr(self.refs_client, "upsert_raw"):
                self.refs_client.upsert_raw(ref_docs[start:end], ref_metas[start:end], ref_ids[start:end])
            else:
                self.refs_client.upsert_documents(ref_docs[start:end], ref_metas[start:end], ref_ids[start:end])

    def ingest(
        self,
        force_rebuild: bool = False,
        limit: int | None = None,
    ) -> dict[str, Any]:
        """Ingest items into the vector database."""
        start_time = datetime.now()
        stats = {
            "total_items": 0,
            "processed_items": 0,
            "added_chunks": 0,
            "updated_chunks": 0,
            "skipped_items": 0,
            "errors": 0,
            "retriever_mode": "advanced_rag",
        }

        md_root = self.config.get("md_root", "")
        if not md_root:
            logger.warning(
                "advanced_rag md_root is not configured. All items will be indexed as "
                "metadata-only chunks (no full-text content). "
                "Set 'semantic_search.advanced_rag.md_root' in your config file "
                "(e.g. ~/.config/zotero-mcp/config.json)."
            )

        if force_rebuild:
            self.chroma_client.reset_collection()
            if self.refs_client is not self.chroma_client:
                self.refs_client.reset_collection()

        # Primary path: read items + attachments from local SQLite (no
        # Zotero HTTP API needed).  Fall back to the legacy API path if
        # the local database is unavailable.
        try:
            items, attachment_map = self._fetch_items_and_attachments_from_local_db(limit=limit)
        except Exception as exc:
            logger.warning(
                "Local Zotero DB unavailable (%s), falling back to HTTP API",
                exc,
            )
            if self.get_items_from_source_fn is None:
                raise
            items = self.get_items_from_source_fn(limit=limit, extract_fulltext=False)
            attachment_map = self._collect_attachment_map(limit=limit)

        stats["total_items"] = len(items)

        batch_docs: list[str] = []
        batch_metas: list[dict[str, Any]] = []
        batch_ids: list[str] = []
        batch_ref_docs: list[str] = []
        batch_ref_metas: list[dict[str, Any]] = []
        batch_ref_ids: list[str] = []
        batch_size = int(self.ingest_cfg.get("batch_size", 500))
        sleep_seconds = float(self.ingest_cfg.get("sleep_between_batches", 1.0))

        # Use IndexingProgress for cleaner display
        with IndexingProgress(
            description=f"Indexing {stats['total_items']} items",
            total=stats["total_items"],
        ) as progress:
            for item in items:
                try:
                    item_key = item.get("key", "")
                    if not item_key:
                        stats["skipped_items"] += 1
                        progress.update(
                            indexed=stats["processed_items"],
                            skipped=stats["skipped_items"],
                            errors=stats["errors"]
                        )
                        continue

                    # 1. Clean up old chunks for this item to prevent residuals
                    # (A key:meta:0 might remain if upserted, but older indexed chunks like
                    # A:att:5 from a previous chunking run would stay forever without this).
                    if not force_rebuild:
                        try:
                            self.chroma_client.delete_by_item_key(item_key)
                            if self.refs_client is not self.chroma_client:
                                self.refs_client.delete_by_metadata({"item_key": item_key})
                        except Exception as exc:
                            logger.warning("Error cleaning up old chunks for item %s: %s", item_key, exc)

                    # 2. Build new chunks
                    docs, metas, ids, ref_docs, ref_metas, ref_ids = self._build_item_chunks(
                        item, attachment_map.get(item_key, [])
                    )
                    if not docs:
                        stats["skipped_items"] += 1
                        progress.update(
                            indexed=stats["processed_items"],
                            skipped=stats["skipped_items"],
                            errors=stats["errors"]
                        )
                        continue

                    batch_docs.extend(docs)
                    batch_metas.extend(metas)
                    batch_ids.extend(ids)
                    batch_ref_docs.extend(ref_docs)
                    batch_ref_metas.extend(ref_metas)
                    batch_ref_ids.extend(ref_ids)
                    stats["processed_items"] += 1

                    progress.update(
                        indexed=stats["processed_items"],
                        skipped=stats["skipped_items"],
                        errors=stats["errors"]
                    )

                    if len(batch_ids) >= batch_size:
                        self._flush_batch(batch_docs, batch_metas, batch_ids, stats)
                        self._flush_refs_batch(batch_ref_docs, batch_ref_metas, batch_ref_ids)
                        batch_docs.clear()
                        batch_metas.clear()
                        batch_ids.clear()
                        batch_ref_docs.clear()
                        batch_ref_metas.clear()
                        batch_ref_ids.clear()
                        if sleep_seconds > 0:
                            time.sleep(sleep_seconds)
                except Exception as exc:
                    stats["errors"] += 1
                    logger.warning("Error processing item %s: %s", item.get("key", "?"), exc, exc_info=True)
                    progress.update(
                        indexed=stats["processed_items"],
                        skipped=stats["skipped_items"],
                        errors=stats["errors"]
                    )

        if batch_ids:
            self._flush_batch(batch_docs, batch_metas, batch_ids, stats)
            self._flush_refs_batch(batch_ref_docs, batch_ref_metas, batch_ref_ids)

        end_time = datetime.now()
        stats["duration"] = str(end_time - start_time)
        stats["start_time"] = start_time.isoformat()
        stats["end_time"] = end_time.isoformat()

        return stats
