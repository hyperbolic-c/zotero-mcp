from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class BaseRetriever(ABC):
    """Common interface for all retriever strategies."""

    @abstractmethod
    def ingest_data(
        self,
        force_rebuild: bool = False,
        limit: int | None = None,
        extract_fulltext: bool = False,
    ) -> dict[str, Any]:
        ...

    @abstractmethod
    def search(
        self,
        query: str,
        limit: int = 10,
        filters: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        ...

    @abstractmethod
    def get_database_status(self) -> dict[str, Any]:
        ...
