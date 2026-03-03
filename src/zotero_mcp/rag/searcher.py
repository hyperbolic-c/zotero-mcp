"""Searcher module for advanced RAG."""

import logging
from typing import Any

from zotero_mcp.chroma_client import CHROMA_GET_MAX_BATCH
from zotero_mcp.utils import format_creators

# Import compat module (not individual attributes) so monkeypatching propagates
from . import compat

from .reference_parser import extract_numeric_citation_ids
from .reranker import CandidateChunk
from .utils import parse_creators_string

logger = logging.getLogger(__name__)


class Searcher:
    """Handles semantic search for advanced RAG."""

    def __init__(
        self,
        chroma_client,
        refs_client,
        config: dict,
        reranker,
        get_item_by_key_fn,
        parse_creators_fn,
        db_path,
    ):
        self.chroma_client = chroma_client
        self.refs_client = refs_client
        self.config = config
        self.reranker = reranker
        self.get_item_by_key_fn = get_item_by_key_fn
        self.parse_creators_fn = parse_creators_fn or parse_creators_string
        self.db_path = db_path
        self.retrieve_cfg = config.get("retrieve", {})

    def _refs_get_by_ids(self, ids: list[str]) -> dict[str, list[Any]]:
        """Fetch reference documents by IDs in batches."""
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
        """Resolve numeric citation IDs to reference text."""
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

    def _hydrate_items(self, item_keys: list[str]) -> dict[str, dict[str, Any]]:
        """Hydrate item metadata from local DB or API."""
        hydrated: dict[str, dict[str, Any]] = {}

        if compat.is_local_mode():
            try:
                with compat.LocalZoteroReader(db_path=self.db_path) as reader:
                    local_items = reader.get_items_by_keys(item_keys)
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
                            "creators": self.parse_creators_fn(local_item.creators or ""),
                        }
                    }
                return hydrated
            except Exception:
                pass

        for item_key in item_keys:
            try:
                if self.get_item_by_key_fn is None:
                    continue
                hydrated[item_key] = self.get_item_by_key_fn(item_key)
            except Exception:
                continue

        return hydrated

    def search(
        self,
        query: str,
        limit: int = 10,
        filters: dict[str, Any] | None = None,
        include_citation_references: bool = True,
        citation_max_items_per_result: int = 8,
    ) -> dict[str, Any]:
        """Execute semantic search with reranking and citation resolution."""
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

        # Use reranker if available
        candidates = self.reranker.rerank(query, candidates)

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
