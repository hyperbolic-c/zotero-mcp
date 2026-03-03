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

    def ingest_data(
        self,
        force_rebuild: bool = False,
        limit: int | None = None,
        extract_fulltext: bool = False,
    ) -> dict[str, Any]:
        """Ingest data into the vector database.

        This method delegates to Ingestor.ingest() for the actual implementation.
        Passes self._build_item_chunks to maintain test monkeypatch compatibility.
        """
        del extract_fulltext
        # Delegate to Ingestor, passing self._build_item_chunks for test compatibility
        return self._ingestor.ingest(
            force_rebuild=force_rebuild,
            limit=limit,
            build_chunks_fn=self._build_item_chunks,
        )

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
