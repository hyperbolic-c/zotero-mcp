import sys

import pytest

if sys.version_info >= (3, 14):
    pytest.skip(
        "chromadb currently relies on pydantic v1 paths that are incompatible with Python 3.14+",
        allow_module_level=True,
    )

from zotero_mcp.indexer import ZoteroIndexer


class FakeChromaClient:
    def __init__(self):
        self.upserted_ids = []

    def get_existing_ids(self, ids):
        # Pretend item A already exists and item B is new.
        return {"ITEMA001"} & set(ids)

    def upsert_documents(self, documents, metadatas, ids):
        self.upserted_ids.extend(ids)


def test_process_item_batch_tracks_added_vs_updated(monkeypatch):
    class DummyRetriever:
        def ingest_data(self, **_kwargs):
            return {}

        def search(self, **_kwargs):
            return {}

        def get_database_status(self):
            return {}

    indexer = ZoteroIndexer(
        chroma_client=FakeChromaClient(),
        zotero_client=object(),
        retriever_factory=lambda _mode, _engine: DummyRetriever(),
    )

    items = [
        {
            "key": "ITEMA001",
            "data": {
                "title": "Existing Item",
                "itemType": "journalArticle",
                "abstractNote": "A",
                "creators": [],
            },
        },
        {
            "key": "ITEMB002",
            "data": {
                "title": "New Item",
                "itemType": "journalArticle",
                "abstractNote": "B",
                "creators": [],
            },
        },
    ]

    stats = indexer._process_item_batch(items, force_rebuild=False)

    assert stats["processed"] == 2
    assert stats["updated"] == 1
    assert stats["added"] == 1
