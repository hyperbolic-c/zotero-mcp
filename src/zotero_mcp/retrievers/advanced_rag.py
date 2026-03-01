from __future__ import annotations

import glob
import json
import logging
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from zotero_mcp.chroma_client import CHROMA_GET_MAX_BATCH, ChromaClient
from zotero_mcp.local_db import LocalZoteroReader
from zotero_mcp.utils import format_creators, is_local_mode

from .base import BaseRetriever
from .chunkers import ChunkingBackend, get_chunking_backend
from .reference_parser import extract_numeric_citation_ids

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


@dataclass
class CandidateChunk:
    item_key: str
    attachment_key: str
    text: str
    metadata: dict[str, Any]
    similarity_score: float
    rank_score: float


class AdvancedRAGRetriever(BaseRetriever):
    def __init__(self, engine: Any, chroma_client: ChromaClient, refs_client: ChromaClient | None = None):
        self.engine = engine
        self.chroma_client = chroma_client
        self.refs_client = refs_client
        self.config = engine.semantic_config.get("advanced_rag", {})
        self.chunk_cfg = self.config.get("chunk", {})
        self.retrieve_cfg = self.config.get("retrieve", {})
        self.ingest_cfg = self.config.get("ingest", {})
        self.reranker_cfg = self.config.get("reranker", {})
        self._ranker = None
        self._reranker_status = self._init_reranker()
        self._chunking_backend: ChunkingBackend = get_chunking_backend(self.chunk_cfg)
        if self.refs_client is None:
            try:
                self.refs_client = ChromaClient(
                    collection_name="zotero_rag_refs_v1",
                    persist_directory=self.chroma_client.persist_directory,
                    embedding_model=self.chroma_client.embedding_model,
                    embedding_config=self.chroma_client.embedding_config,
                )
            except Exception as exc:
                logger.warning("Unable to initialize dedicated refs collection, using primary client: %s", exc)
                self.refs_client = self.chroma_client

    def _init_reranker(self) -> str:
        if not self.reranker_cfg.get("enabled", True):
            return "disabled"
        if self.reranker_cfg.get("backend", "flashrank") != "flashrank":
            return "degraded:unsupported_backend"
        try:
            from flashrank import Ranker

            model_name = self.reranker_cfg.get("model_name", "ms-marco-MiniLM-L-12-v2")
            local_model_path = self.reranker_cfg.get("local_model_path")
            kwargs = {"model_name": model_name}
            if local_model_path and os.path.exists(local_model_path):
                kwargs["cache_dir"] = local_model_path
            self._ranker = Ranker(**kwargs)
            return f"enabled:flashrank/{model_name}"
        except ImportError:
            self._ranker = None
            return "degraded:package_missing"
        except Exception as exc:
            self._ranker = None
            return f"degraded:init_error:{type(exc).__name__}"

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
        if not is_local_mode():
            return {}
        item_to_attachments: dict[str, list[str]] = {}
        try:
            with LocalZoteroReader(db_path=self.engine.db_path) as reader:
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
            of dicts in the same format produced by
            ``engine._get_items_from_source()`` and *attachment_map* maps each
            parent item key to a list of attachment keys.

        Raises:
            FileNotFoundError: If the Zotero database cannot be located.
            Exception: On any unexpected SQLite / IO error (caller should
            fall back to the API path).
        """
        # --- resolve db_path / pdf_max_pages from config (same as legacy) ---
        zotero_db_path = self.engine.db_path
        pdf_max_pages = None
        config_path = self.engine.config_path
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

        with LocalZoteroReader(db_path=zotero_db_path, pdf_max_pages=pdf_max_pages) as reader:
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
                            self.engine._parse_creators_string(item.creators)
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
        # ChromaDB has a hard per-call limit (SQLITE_LIMIT_VARIABLE_NUMBER).
        # Sub-slice so no single upsert call ever exceeds CHROMA_MAX_BATCH items.
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

    def ingest_data(
        self,
        force_rebuild: bool = False,
        limit: int | None = None,
        extract_fulltext: bool = False,
    ) -> dict[str, Any]:
        del extract_fulltext
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
            items = self.engine._get_items_from_source(limit=limit, extract_fulltext=False)
            attachment_map = self._collect_attachment_map(limit=limit)

        stats["total_items"] = len(items)
        try:
            sys.stderr.write(f"Total items to index: {stats['total_items']}\n")
        except Exception:
            pass

        batch_docs: list[str] = []
        batch_metas: list[dict[str, Any]] = []
        batch_ids: list[str] = []
        batch_ref_docs: list[str] = []
        batch_ref_metas: list[dict[str, Any]] = []
        batch_ref_ids: list[str] = []
        batch_size = int(self.ingest_cfg.get("batch_size", 500))
        sleep_seconds = float(self.ingest_cfg.get("sleep_between_batches", 1.0))
        next_milestone = 10 if stats["total_items"] >= 10 else stats["total_items"]
        seen_items = 0

        for item in items:
            try:
                item_key = item.get("key", "")
                if not item_key:
                    stats["skipped_items"] += 1
                    continue

                docs, metas, ids, ref_docs, ref_metas, ref_ids = self._build_item_chunks(
                    item, attachment_map.get(item_key, [])
                )
                if not docs:
                    stats["skipped_items"] += 1
                    continue

                batch_docs.extend(docs)
                batch_metas.extend(metas)
                batch_ids.extend(ids)
                batch_ref_docs.extend(ref_docs)
                batch_ref_metas.extend(ref_metas)
                batch_ref_ids.extend(ref_ids)
                stats["processed_items"] += 1

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
            finally:
                seen_items += 1
                try:
                    while seen_items >= next_milestone and next_milestone > 0:
                        sys.stderr.write(
                            f"Processed: {next_milestone}/{stats['total_items']} "
                            f"indexed_items:{stats['processed_items']} "
                            f"skipped:{stats['skipped_items']} "
                            f"errors:{stats['errors']}\n"
                        )
                        next_milestone += 10
                        if next_milestone > stats["total_items"]:
                            next_milestone = stats["total_items"]
                            break
                except Exception:
                    pass

        if batch_ids:
            self._flush_batch(batch_docs, batch_metas, batch_ids, stats)
            self._flush_refs_batch(batch_ref_docs, batch_ref_metas, batch_ref_ids)

        end_time = datetime.now()
        stats["duration"] = str(end_time - start_time)
        stats["start_time"] = start_time.isoformat()
        stats["end_time"] = end_time.isoformat()

        return stats

    def _rerank(self, query: str, candidates: list[CandidateChunk]) -> list[CandidateChunk]:
        if not candidates or self._ranker is None:
            return candidates

        top_n = int(self.reranker_cfg.get("top_n", 8))
        try:
            from flashrank import RerankRequest

            passages = [{"id": str(i), "text": c.text} for i, c in enumerate(candidates)]
            results = self._ranker.rank(RerankRequest(query=query, passages=passages), top_n=top_n)
            reranked: list[CandidateChunk] = []
            for result in results:
                idx = int(result.get("id", -1))
                if idx < 0 or idx >= len(candidates):
                    continue
                candidate = candidates[idx]
                score = float(result.get("score", candidate.similarity_score))
                reranked.append(
                    CandidateChunk(
                        item_key=candidate.item_key,
                        attachment_key=candidate.attachment_key,
                        text=candidate.text,
                        metadata=candidate.metadata,
                        similarity_score=candidate.similarity_score,
                        rank_score=score,
                    )
                )
            return reranked if reranked else candidates
        except Exception:
            return candidates

    def _hydrate_items(self, item_keys: list[str]) -> dict[str, dict[str, Any]]:
        hydrated: dict[str, dict[str, Any]] = {}

        if is_local_mode():
            try:
                with LocalZoteroReader(db_path=self.engine.db_path) as reader:
                    local_items = reader.get_items_with_text(include_fulltext=False)
                local_by_key = {item.key: item for item in local_items}
                for item_key in item_keys:
                    local_item = local_by_key.get(item_key)
                    if not local_item:
                        continue
                    hydrated[item_key] = {
                        "data": {
                            "key": item_key,
                            "itemType": local_item.item_type or "journalArticle",
                            "title": local_item.title or "",
                            "abstractNote": local_item.abstract or "",
                            "date": "",
                            "creators": self.engine._parse_creators_string(local_item.creators or ""),
                        }
                    }
                return hydrated
            except Exception:
                pass

        for item_key in item_keys:
            try:
                hydrated[item_key] = self.engine.zotero_client.item(item_key)
            except Exception:
                continue

        return hydrated

    def _refs_get_by_ids(self, ids: list[str]) -> dict[str, list[Any]]:
        if not ids:
            return {"ids": [], "documents": [], "metadatas": []}
        out_ids: list[str] = []
        out_docs: list[str] = []
        out_metas: list[dict[str, Any]] = []
        for start in range(0, len(ids), CHROMA_GET_MAX_BATCH):
            batch = ids[start : start + CHROMA_GET_MAX_BATCH]
            try:
                result = self.refs_client.collection.get(
                    ids=batch, include=["documents", "metadatas"]
                )
            except Exception:
                continue
            out_ids.extend(result.get("ids", []) or [])
            out_docs.extend(result.get("documents", []) or [])
            out_metas.extend(result.get("metadatas", []) or [])
        return {"ids": out_ids, "documents": out_docs, "metadatas": out_metas}

    def _resolve_citations(
        self,
        item_key: str,
        attachment_key: str,
        citation_ids: list[int],
    ) -> list[dict[str, Any]]:
        if not citation_ids:
            return []
        resolved: list[dict[str, Any]] = []
        att_key = attachment_key

        # Bounded fallback for metadata-only hits.
        if att_key == "meta":
            try:
                fallback = self.refs_client.collection.get(
                    where={"item_key": item_key},
                    include=["ids"],
                    limit=1,
                )
                fallback_ids = fallback.get("ids", []) or []
                if fallback_ids:
                    found_id = fallback_ids[0]
                    if ":ref:" in found_id:
                        att_key = found_id.split(":", 2)[1]
                        logger.debug(
                            "Meta fallback resolved attachment_key=%s for item_key=%s",
                            att_key,
                            item_key,
                        )
            except Exception:
                pass

        ref_ids = [f"{item_key}:{att_key}:ref:{n}" for n in citation_ids]
        ref_results = self._refs_get_by_ids(ref_ids)
        for ret_id, doc in zip(ref_results.get("ids", []), ref_results.get("documents", [])):
            try:
                ref_num = int(str(ret_id).rsplit(":ref:", 1)[-1])
            except Exception:
                continue
            resolved.append({"ref_num": ref_num, "ref_text": doc})
        resolved.sort(key=lambda x: x["ref_num"])
        return resolved

    def search(
        self,
        query: str,
        limit: int = 10,
        filters: dict[str, Any] | None = None,
        include_citation_references: bool = True,
        citation_max_items_per_result: int = 8,
    ) -> dict[str, Any]:
        candidate_k = int(self.retrieve_cfg.get("candidate_k", 30))
        evidence_per_item = int(self.retrieve_cfg.get("evidence_per_item", 2))
        meta_weight = float(self.retrieve_cfg.get("meta_weight", 0.70))

        raw_results = self.chroma_client.search(
            query_texts=[query],
            n_results=max(candidate_k, limit),
            where=filters,
        )

        ids = raw_results.get("ids", [[]])[0]
        docs = raw_results.get("documents", [[]])[0]
        metas = raw_results.get("metadatas", [[]])[0]
        distances = raw_results.get("distances", [[]])[0]

        candidates: list[CandidateChunk] = []
        for i, doc_id in enumerate(ids):
            metadata = metas[i] if i < len(metas) and metas[i] else {}
            item_key = metadata.get("item_key") or (doc_id.split(":", 1)[0] if ":" in doc_id else doc_id)
            attachment_key = metadata.get("attachment_key", "")
            distance = distances[i] if i < len(distances) else 1.0
            similarity = 1.0 - float(distance)
            candidates.append(
                CandidateChunk(
                    item_key=item_key,
                    attachment_key=attachment_key,
                    text=docs[i] if i < len(docs) else "",
                    metadata=metadata,
                    similarity_score=similarity,
                    rank_score=similarity,
                )
            )

        if self._ranker is not None:
            candidates = self._rerank(query, candidates)

        by_item: dict[str, dict[str, Any]] = {}
        for candidate in candidates:
            record = by_item.setdefault(
                candidate.item_key,
                {
                    "item_key": candidate.item_key,
                    "content_scores": [],
                    "meta_scores": [],
                    "content_evidence": [],
                    "meta_evidence": [],
                },
            )
            chunk_kind = candidate.metadata.get("chunk_kind", "content")
            if chunk_kind == "meta":
                record["meta_scores"].append(candidate.rank_score)
                record["meta_evidence"].append(candidate)
            else:
                record["content_scores"].append(candidate.rank_score)
                record["content_evidence"].append(candidate)

        aggregated = []
        for item_key, record in by_item.items():
            best_content = max(record["content_scores"]) if record["content_scores"] else 0.0
            best_meta = max(record["meta_scores"]) if record["meta_scores"] else 0.0
            item_score = max(best_content, meta_weight * best_meta)
            evidence = sorted(record["content_evidence"], key=lambda c: c.rank_score, reverse=True)[:evidence_per_item]
            if record["meta_evidence"]:
                evidence.extend(sorted(record["meta_evidence"], key=lambda c: c.rank_score, reverse=True)[:1])

            aggregated.append(
                {
                    "item_key": item_key,
                    "similarity_score": item_score,
                    "evidence": evidence,
                }
            )

        aggregated.sort(key=lambda r: r["similarity_score"], reverse=True)
        aggregated = aggregated[:limit]

        hydrated = self._hydrate_items([row["item_key"] for row in aggregated])

        results = []
        for row in aggregated:
            evidence = row["evidence"]
            top_match = evidence[0].text if evidence else ""
            top_meta = evidence[0].metadata if evidence else {}
            resolved_citations: list[dict[str, Any]] = []
            if include_citation_references and top_match:
                cite_ids = extract_numeric_citation_ids(top_match)
                resolved_citations = self._resolve_citations(
                    item_key=row["item_key"],
                    attachment_key=top_meta.get("attachment_key", ""),
                    citation_ids=cite_ids,
                )
                if citation_max_items_per_result > 0:
                    resolved_citations = resolved_citations[:citation_max_items_per_result]
            results.append(
                {
                    "item_key": row["item_key"],
                    "similarity_score": row["similarity_score"],
                    "matched_text": top_match,
                    "metadata": top_meta,
                    "zotero_item": hydrated.get(row["item_key"], {}),
                    "query": query,
                    "resolved_citations": resolved_citations,
                    "evidence": [
                        {
                            "text": ev.text,
                            "score": ev.rank_score,
                            "chunk_kind": ev.metadata.get("chunk_kind", "content"),
                            "attachment_key": ev.metadata.get("attachment_key", ""),
                            "section_title": ev.metadata.get("section_title", ""),
                        }
                        for ev in evidence
                    ],
                }
            )

        return {
            "query": query,
            "limit": limit,
            "filters": filters,
            "results": results,
            "total_found": len(results),
            "retriever_mode": "advanced_rag",
        }

    def get_database_status(self) -> dict[str, Any]:
        summary = {
            "md_root": self.config.get("md_root", ""),
            "candidate_k": self.retrieve_cfg.get("candidate_k", 30),
            "meta_weight": self.retrieve_cfg.get("meta_weight", 0.70),
            "chunker_backend": self.chunk_cfg.get("backend", "langchain"),
            "chunker_strategy": self.chunk_cfg.get("strategy", "markdown_recursive_v1"),
        }
        return {
            "collection_info": self.chroma_client.get_collection_info(),
            "refs_collection_info": self.refs_client.get_collection_info(),
            "retriever_mode": "advanced_rag",
            "advanced_rag": summary,
            "reranker_status": self._reranker_status,
        }
