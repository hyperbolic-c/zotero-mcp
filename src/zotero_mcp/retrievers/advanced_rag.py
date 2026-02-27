from __future__ import annotations

import glob
import logging
import os
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from zotero_mcp.chroma_client import ChromaClient
from zotero_mcp.local_db import LocalZoteroReader
from zotero_mcp.utils import format_creators, is_local_mode

from .base import BaseRetriever

logger = logging.getLogger(__name__)


@dataclass
class CandidateChunk:
    item_key: str
    attachment_key: str
    text: str
    metadata: dict[str, Any]
    similarity_score: float
    rank_score: float


class AdvancedRAGRetriever(BaseRetriever):
    def __init__(self, engine: Any, chroma_client: ChromaClient):
        self.engine = engine
        self.chroma_client = chroma_client
        self.config = engine.semantic_config.get("advanced_rag", {})
        self.chunk_cfg = self.config.get("chunk", {})
        self.retrieve_cfg = self.config.get("retrieve", {})
        self.ingest_cfg = self.config.get("ingest", {})
        self.reranker_cfg = self.config.get("reranker", {})
        self._ranker = None
        self._reranker_status = self._init_reranker()

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

    def _split_sections(self, text: str) -> list[tuple[str, str]]:
        sections: list[tuple[str, str]] = []
        current_title = ""
        current_lines: list[str] = []
        for line in text.splitlines():
            if re.match(r"^#{1,3}\s+", line.strip()):
                if current_lines:
                    sections.append((current_title, "\n".join(current_lines).strip()))
                    current_lines = []
                current_title = re.sub(r"^#{1,3}\s+", "", line.strip())
                continue
            current_lines.append(line)
        if current_lines:
            sections.append((current_title, "\n".join(current_lines).strip()))
        return [sec for sec in sections if sec[1]]

    def _chunk_text(self, text: str) -> list[str]:
        max_chars = int(self.chunk_cfg.get("max_chars", 1600))
        overlap_chars = int(self.chunk_cfg.get("overlap_chars", 200))
        min_chunk_chars = int(self.chunk_cfg.get("min_chunk_chars", 120))
        if len(text) <= max_chars:
            return [text] if len(text) >= min_chunk_chars else []

        chunks: list[str] = []
        start = 0
        text_len = len(text)
        while start < text_len:
            end = min(start + max_chars, text_len)
            chunk = text[start:end].strip()
            if len(chunk) >= min_chunk_chars:
                chunks.append(chunk)
            if end >= text_len:
                break
            start = max(0, end - overlap_chars)
        return chunks

    def _build_item_chunks(
        self,
        item: dict[str, Any],
        attachment_keys: list[str],
    ) -> tuple[list[str], list[dict[str, Any]], list[str]]:
        item_key = item.get("key", "")
        base_meta = self._base_metadata(item)

        docs: list[str] = []
        metas: list[dict[str, Any]] = []
        ids: list[str] = []

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

            chunk_idx = 0
            for section_title, section_text in self._split_sections(merged_text):
                for chunk in self._chunk_text(section_text):
                    docs.append(chunk)
                    chunk_meta = dict(base_meta)
                    chunk_meta.update(
                        {
                            "attachment_key": attachment_key,
                            "chunk_kind": "content",
                            "section_title": section_title,
                            "source_type": "mineru_md",
                            "has_md_source": True,
                        }
                    )
                    metas.append(chunk_meta)
                    ids.append(f"{item_key}:{attachment_key}:{chunk_idx}")
                    chunk_idx += 1

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

        return docs, metas, ids

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

    def _flush_batch(
        self,
        batch_docs: list[str],
        batch_metas: list[dict[str, Any]],
        batch_ids: list[str],
        stats: dict[str, Any],
    ) -> None:
        existing_ids = self.chroma_client.get_existing_ids(batch_ids)
        self.chroma_client.upsert_documents(batch_docs, batch_metas, batch_ids)
        for doc_id in batch_ids:
            if doc_id in existing_ids:
                stats["updated_chunks"] += 1
            else:
                stats["added_chunks"] += 1

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

        items = self.engine._get_items_from_source(limit=limit, extract_fulltext=False)
        stats["total_items"] = len(items)
        attachment_map = self._collect_attachment_map(limit=limit)
        try:
            sys.stderr.write(f"Total items to index: {stats['total_items']}\n")
        except Exception:
            pass

        batch_docs: list[str] = []
        batch_metas: list[dict[str, Any]] = []
        batch_ids: list[str] = []
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

                docs, metas, ids = self._build_item_chunks(item, attachment_map.get(item_key, []))
                if not docs:
                    stats["skipped_items"] += 1
                    continue

                batch_docs.extend(docs)
                batch_metas.extend(metas)
                batch_ids.extend(ids)
                stats["processed_items"] += 1

                if len(batch_ids) >= batch_size:
                    self._flush_batch(batch_docs, batch_metas, batch_ids, stats)
                    batch_docs.clear()
                    batch_metas.clear()
                    batch_ids.clear()
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

    def search(
        self,
        query: str,
        limit: int = 10,
        filters: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        candidate_k = int(self.retrieve_cfg.get("candidate_k", 30))
        evidence_per_item = int(self.retrieve_cfg.get("evidence_per_item", 2))
        meta_weight = float(self.retrieve_cfg.get("meta_weight", 0.85))

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
            results.append(
                {
                    "item_key": row["item_key"],
                    "similarity_score": row["similarity_score"],
                    "matched_text": top_match,
                    "metadata": top_meta,
                    "zotero_item": hydrated.get(row["item_key"], {}),
                    "query": query,
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
            "meta_weight": self.retrieve_cfg.get("meta_weight", 0.85),
        }
        return {
            "collection_info": self.chroma_client.get_collection_info(),
            "retriever_mode": "advanced_rag",
            "advanced_rag": summary,
            "reranker_status": self._reranker_status,
        }
