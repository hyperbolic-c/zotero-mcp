"""Advanced RAG Retriever - Facade module.

This module provides the AdvancedRAGRetriever class which serves as a facade,
combining Reranker, Ingestor, and Searcher submodules.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from zotero_mcp.chroma_client import ChromaClient

from .base import BaseRetriever
from .compat import is_local_mode, LocalZoteroReader

# Re-export for backward compatibility with tests
from .ingestor import CHROMA_MAX_BATCH, Ingestor
from .reranker import CandidateChunk, Reranker
from .searcher import Searcher

logger = logging.getLogger(__name__)


class AdvancedRAGRetriever(BaseRetriever):
    """Advanced RAG Retriever - Facade combining Reranker, Ingestor, and Searcher."""

    def __init__(
        self,
        *,
        chroma_client: ChromaClient,
        config: dict[str, Any] | None = None,
        refs_client: ChromaClient | None = None,
        db_path: str | None = None,
        config_path: str | None = None,
        get_items_from_source_fn: Any | None = None,
        parse_creators_fn: Any | None = None,
        get_item_by_key_fn: Any | None = None,
        role: str = "search",
    ):
        self.chroma_client = chroma_client
        self.config = config or {}
        self.db_path = db_path
        self.config_path = config_path
        self.get_items_from_source_fn = get_items_from_source_fn
        self.parse_creators_fn = parse_creators_fn
        self.get_item_by_key_fn = get_item_by_key_fn
        self.role = role

        # Initialize submodules
        self.reranker = Reranker(self.config.get("reranker", {}))
        self._reranker_status = self.reranker.status()

        # Set up refs_client
        if refs_client is None:
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
        else:
            self.refs_client = refs_client

        # Initialize Ingestor and Searcher
        self._ingestor = Ingestor(
            chroma_client=self.chroma_client,
            refs_client=self.refs_client,
            config=self.config,
            db_path=db_path,
            config_path=config_path,
            get_items_from_source_fn=get_items_from_source_fn,
            parse_creators_fn=parse_creators_fn,
        )

        self._searcher = Searcher(
            chroma_client=self.chroma_client,
            refs_client=self.refs_client,
            config=self.config,
            reranker=self.reranker,
            get_item_by_key_fn=get_item_by_key_fn,
            parse_creators_fn=parse_creators_fn,
            db_path=db_path,
        )

        # Backward compatibility: expose reranker for tests
        self._ranker = self.reranker._ranker

        # Cache config for backward compatibility
        self.chunk_cfg = self.config.get("chunk", {})
        self.retrieve_cfg = self.config.get("retrieve", {})
        self.ingest_cfg = self.config.get("ingest", {})
        self.reranker_cfg = self.config.get("reranker", {})

        # Expose chunking backend for _build_item_chunks method
        from .chunkers import get_chunking_backend
        self._chunking_backend = get_chunking_backend(self.chunk_cfg)

    def ingest_data(
        self,
        force_rebuild: bool = False,
        limit: int | None = None,
        extract_fulltext: bool = False,
    ) -> dict[str, Any]:
        """Ingest data into the vector database.

        This method is implemented in AdvancedRAGRetriever to maintain backward
        compatibility with tests that monkeypatch _build_item_chunks.
        """
        del extract_fulltext
        from datetime import datetime

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

        # Primary path: read items + attachments from local SQLite
        try:
            items, attachment_map = self._ingestor._fetch_items_and_attachments_from_local_db(limit=limit)
        except Exception as exc:
            logger.warning(
                "Local Zotero DB unavailable (%s), falling back to HTTP API",
                exc,
            )
            if self.get_items_from_source_fn is None:
                raise
            items = self.get_items_from_source_fn(limit=limit, extract_fulltext=False)
            attachment_map = self._ingestor._collect_attachment_map(limit=limit)

        stats["total_items"] = len(items)

        batch_docs: list[str] = []
        batch_metas: list[dict[str, Any]] = []
        batch_ids: list[str] = []
        batch_ref_docs: list[str] = []
        batch_ref_metas: list[dict[str, Any]] = []
        batch_ref_ids: list[str] = []
        batch_size = int(self.ingest_cfg.get("batch_size", 500))
        sleep_seconds = float(self.ingest_cfg.get("sleep_between_batches", 1.0))

        # Import IndexingProgress locally to avoid import cycle
        from zotero_mcp.utils import IndexingProgress

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

                    # Clean up old chunks for this item
                    if not force_rebuild:
                        try:
                            self.chroma_client.delete_by_item_key(item_key)
                            if self.refs_client is not self.chroma_client:
                                self.refs_client.delete_by_metadata({"item_key": item_key})
                        except Exception as exc:
                            logger.warning("Error cleaning up old chunks for item %s: %s", item_key, exc)

                    # Build new chunks - use self._build_item_chunks for test monkeypatch compatibility
                    # Note: Call through self._build_item_chunks so test monkeypatch works
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
                        self._ingestor._flush_refs_batch(batch_ref_docs, batch_ref_metas, batch_ref_ids)
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

        if batch_docs:
            self._flush_batch(batch_docs, batch_metas, batch_ids, stats)
            self._ingestor._flush_refs_batch(batch_ref_docs, batch_ref_metas, batch_ref_ids)

        end_time = datetime.now()
        stats["duration"] = str(end_time - start_time)
        stats["start_time"] = start_time.isoformat()
        stats["end_time"] = end_time.isoformat()

        return stats

    def search(
        self,
        query: str,
        limit: int = 10,
        filters: dict[str, Any] | None = None,
        include_citation_references: bool = True,
        citation_max_items_per_result: int = 8,
    ) -> dict[str, Any]:
        """Execute semantic search."""
        return self._searcher.search(
            query=query,
            limit=limit,
            filters=filters,
            include_citation_references=include_citation_references,
            citation_max_items_per_result=citation_max_items_per_result,
        )

    def get_database_status(self) -> dict[str, Any]:
        """Get database status."""
        summary = {
            "md_root": self.config.get("md_root", ""),
            "candidate_k": self.config.get("retrieve", {}).get("candidate_k", 30),
            "meta_weight": self.config.get("retrieve", {}).get("meta_weight", 0.70),
            "chunker_backend": self.config.get("chunk", {}).get("backend", "langchain"),
            "chunker_strategy": self.config.get("chunk", {}).get("strategy", "markdown_recursive_v1"),
        }
        return {
            "collection_info": self.chroma_client.get_collection_info(),
            "refs_collection_info": self.refs_client.get_collection_info(),
            "retriever_mode": "advanced_rag",
            "advanced_rag": summary,
            "reranker_status": self._reranker_status,
        }

    # Backward compatibility methods for tests
    def _rerank(self, query: str, candidates: list[CandidateChunk]) -> list[CandidateChunk]:
        """Rerank candidates (backward compatibility for tests)."""
        return self.reranker.rerank(query, candidates)

    def _build_item_chunks(
        self,
        item: dict[str, Any],
        attachment_keys: list[str],
    ) -> tuple[list[str], list[dict[str, Any]], list[str], list[str], list[dict[str, Any]], list[str]]:
        """Build chunks from item and its attachments (backward compatibility for tests).

        This method delegates to ingestor for actual implementation, but uses
        self._build_item_chunks to allow test monkeypatching to work.
        """
        # Use self._ingestor to access the actual implementation
        # But we need to call through self so monkeypatch works
        return self._ingestor._build_item_chunks(item, attachment_keys)

    def _base_metadata(self, item: dict[str, Any]) -> dict[str, Any]:
        """Build base metadata for an item."""
        return self._ingestor._base_metadata(item)

    def _iter_markdown_files(self, attachment_key: str) -> list:
        """Iterate markdown files for an attachment."""
        return self._ingestor._iter_markdown_files(attachment_key)

    def _strip_images(self, text: str) -> str:
        """Strip image references from text."""
        return self._ingestor._strip_images(text)

    def _build_meta_text(self, item: dict[str, Any]) -> str:
        """Build metadata text for an item."""
        return self._ingestor._build_meta_text(item)

    def _flush_batch(
        self,
        batch_docs: list[str],
        batch_metas: list[dict[str, Any]],
        batch_ids: list[str],
        stats: dict[str, Any],
    ) -> None:
        """Flush batch to ChromaDB (backward compatibility for tests).

        Uses module-level CHROMA_MAX_BATCH imported from ingestor.
        """
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
