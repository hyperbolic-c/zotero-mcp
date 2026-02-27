import sys

import pytest

if sys.version_info >= (3, 14):
    pytest.skip(
        "chromadb currently relies on pydantic v1 paths that are incompatible with Python 3.14+",
        allow_module_level=True,
    )

from zotero_mcp import semantic_search


class DummyRetriever:
    def __init__(self):
        self.ingest_called = None

    def ingest_data(self, force_rebuild=False, limit=None, extract_fulltext=False):
        self.ingest_called = (force_rebuild, limit, extract_fulltext)
        return {"ok": True}

    def search(self, query, limit=10, filters=None):
        return {"query": query, "limit": limit, "filters": filters, "results": []}

    def get_database_status(self):
        return {"collection_info": {"name": "x", "count": 0}}


class FakeChromaClient:
    def get_existing_ids(self, ids):
        return set()

    def upsert_documents(self, documents, metadatas, ids):
        return None


def test_semantic_search_delegates_to_retriever(monkeypatch):
    retriever = DummyRetriever()

    monkeypatch.setattr(semantic_search, "get_zotero_client", lambda: object())
    monkeypatch.setattr(semantic_search, "create_retriever", lambda mode, engine: retriever)

    search = semantic_search.ZoteroSemanticSearch(chroma_client=FakeChromaClient())

    stats = search.update_database(force_full_rebuild=True, limit=5, extract_fulltext=True)
    assert stats["ok"] is True
    assert retriever.ingest_called == (True, 5, True)

    results = search.search("hello", limit=3, filters={"item_type": "note"})
    assert results["query"] == "hello"
    assert results["limit"] == 3

    status = search.get_database_status()
    assert status["collection_info"]["name"] == "x"
    assert status["retriever_mode"] == "legacy_metadata"
