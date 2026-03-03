"""Semantic search functionality for Zotero MCP."""

from __future__ import annotations

import logging
from typing import Any

from .chroma_client import ChromaClient, create_chroma_client
from .client import get_zotero_client
from .indexer import get_advanced_rag_defaults, load_semantic_config
from .rag.factory import create_retriever
from .utils import parse_creators_string

logger = logging.getLogger(__name__)


class ZoteroSemanticSearch:
    """Semantic search interface for Zotero libraries using ChromaDB."""

    def __init__(
        self,
        chroma_client: ChromaClient | None = None,
        config_path: str | None = None,
        db_path: str | None = None,
    ):
        self.config_path = config_path
        self.db_path = db_path
        self.zotero_client = get_zotero_client()
        self.semantic_config = load_semantic_config(config_path)
        self.retriever_mode = self.semantic_config.get("retriever_mode", "legacy_metadata")
        self.chroma_client = chroma_client or self._create_search_chroma_client()

        self.retriever = create_retriever(
            self.retriever_mode,
            role="search",
            chroma_client=self.chroma_client,
            config={
                "advanced_rag": self.semantic_config.get("advanced_rag", {}),
                "config_path": self.config_path,
                "db_path": self.db_path,
            },
            services={
                "search_fn": self._legacy_search,
                "status_fn": self._legacy_get_database_status,
                "ingest_fn": None,
                "get_item_by_key_fn": getattr(self.zotero_client, "item", None),
                "parse_creators_fn": parse_creators_string,
                "get_items_from_source_fn": None,
            },
        )

    @staticmethod
    def _get_advanced_rag_defaults() -> dict[str, Any]:
        return get_advanced_rag_defaults()

    def _create_search_chroma_client(self) -> ChromaClient:
        if self.retriever_mode == "advanced_rag":
            base_client = create_chroma_client(config_path=self.config_path)
            return ChromaClient(
                collection_name="zotero_rag_chunks_v1",
                persist_directory=base_client.persist_directory,
                embedding_model=base_client.embedding_model,
                embedding_config=base_client.embedding_config,
            )
        return create_chroma_client(config_path=self.config_path)

    def _enrich_search_results(self, chroma_results: dict[str, Any], query: str) -> list[dict[str, Any]]:
        enriched: list[dict[str, Any]] = []
        if not chroma_results.get("ids") or not chroma_results["ids"][0]:
            return enriched

        ids = chroma_results["ids"][0]
        distances = chroma_results.get("distances", [[]])[0]
        documents = chroma_results.get("documents", [[]])[0]
        metadatas = chroma_results.get("metadatas", [[]])[0]

        for i, item_key in enumerate(ids):
            try:
                zotero_item = self.zotero_client.item(item_key)
                enriched.append(
                    {
                        "item_key": item_key,
                        "similarity_score": 1 - distances[i] if i < len(distances) else 0,
                        "matched_text": documents[i] if i < len(documents) else "",
                        "metadata": metadatas[i] if i < len(metadatas) else {},
                        "zotero_item": zotero_item,
                        "query": query,
                    }
                )
            except Exception as exc:
                enriched.append(
                    {
                        "item_key": item_key,
                        "similarity_score": 1 - distances[i] if i < len(distances) else 0,
                        "matched_text": documents[i] if i < len(documents) else "",
                        "metadata": metadatas[i] if i < len(metadatas) else {},
                        "query": query,
                        "error": f"Could not fetch full item data: {exc}",
                    }
                )
        return enriched

    def _legacy_search(
        self, query: str, limit: int = 10, filters: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        try:
            results = self.chroma_client.search(query_texts=[query], n_results=limit, where=filters)
            enriched_results = self._enrich_search_results(results, query)
            return {
                "query": query,
                "limit": limit,
                "filters": filters,
                "results": enriched_results,
                "total_found": len(enriched_results),
            }
        except Exception as exc:
            logger.error("Error performing semantic search: %s", exc)
            return {
                "query": query,
                "limit": limit,
                "filters": filters,
                "results": [],
                "total_found": 0,
                "error": str(exc),
            }

    def _legacy_get_database_status(self) -> dict[str, Any]:
        return {
            "collection_info": self.chroma_client.get_collection_info(),
            "retriever_mode": self.retriever_mode,
        }

    def search(self, query: str, limit: int = 10, **kwargs: Any) -> dict[str, Any]:
        if not query or not query.strip():
            return {"query": query, "limit": limit, "results": [], "total_found": 0}

        logger.info("Performing semantic search for: '%s' (limit: %s)", query, limit)
        return self.retriever.search(query, limit=limit, **kwargs)

    def get_database_status(self) -> dict[str, Any]:
        try:
            status = self.retriever.get_database_status()
            status["retriever_mode"] = status.get("retriever_mode", self.retriever_mode)
            return status
        except Exception as exc:
            logger.error("Error getting database status: %s", exc)
            return {"error": str(exc)}


def create_semantic_search(config_path: str | None = None, db_path: str | None = None) -> ZoteroSemanticSearch:
    return ZoteroSemanticSearch(config_path=config_path, db_path=db_path)
