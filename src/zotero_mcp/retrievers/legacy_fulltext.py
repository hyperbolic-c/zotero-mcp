from __future__ import annotations

from typing import Any, Callable

from .base import BaseRetriever


class LegacyFulltextRetriever(BaseRetriever):
    """Legacy retrieval path with optional local fulltext extraction."""

    def __init__(
        self,
        *,
        ingest_fn: Callable[..., dict[str, Any]] | None = None,
        search_fn: Callable[..., dict[str, Any]] | None = None,
        status_fn: Callable[[], dict[str, Any]] | None = None,
        chroma_client: Any = None,
    ):
        self._ingest_fn = ingest_fn
        self._search_fn = search_fn
        self._status_fn = status_fn
        self.chroma_client = chroma_client

    def ingest_data(
        self,
        force_rebuild: bool = False,
        limit: int | None = None,
        extract_fulltext: bool = False,
    ) -> dict[str, Any]:
        if self._ingest_fn is None:
            return {"error": "Ingest service is not available for legacy_fulltext retriever"}
        return self._ingest_fn(
            force_full_rebuild=force_rebuild,
            limit=limit,
            extract_fulltext=extract_fulltext,
        )

    def search(
        self,
        query: str,
        limit: int = 10,
        filters: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        del kwargs
        if self._search_fn is None:
            return {
                "query": query,
                "limit": limit,
                "filters": filters,
                "results": [],
                "total_found": 0,
                "error": "Search service is not available for legacy_fulltext retriever",
            }
        return self._search_fn(query=query, limit=limit, filters=filters)

    def get_database_status(self) -> dict[str, Any]:
        if self._status_fn is None:
            return {"collection_info": {"error": "Status service is not available for legacy_fulltext retriever"}}
        return self._status_fn()
