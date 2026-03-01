from __future__ import annotations

from typing import Any

from .base import BaseRetriever


class LegacyFulltextRetriever(BaseRetriever):
    """Legacy retrieval path with optional local fulltext extraction."""

    def __init__(self, engine: Any):
        self.engine = engine
        self.chroma_client = engine.chroma_client

    def ingest_data(
        self,
        force_rebuild: bool = False,
        limit: int | None = None,
        extract_fulltext: bool = False,
    ) -> dict[str, Any]:
        return self.engine._legacy_update_database(
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
        return self.engine._legacy_search(query=query, limit=limit, filters=filters)

    def get_database_status(self) -> dict[str, Any]:
        return self.engine._legacy_get_database_status()
