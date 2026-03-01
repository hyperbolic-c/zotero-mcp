import sys
from datetime import datetime, timedelta

import pytest

if sys.version_info >= (3, 14):
    pytest.skip(
        "chromadb currently relies on pydantic v1 paths that are incompatible with Python 3.14+",
        allow_module_level=True,
    )

from zotero_mcp.indexer import ZoteroIndexer


class DummyRetriever:
    def ingest_data(self, **_kwargs):
        return {}

    def search(self, **_kwargs):
        return {}

    def get_database_status(self):
        return {}


def _make_indexer() -> ZoteroIndexer:
    return ZoteroIndexer(
        chroma_client=object(),
        zotero_client=object(),
        retriever_factory=lambda _mode, **_kwargs: DummyRetriever(),
    )


def test_get_update_status_contains_compat_fields():
    idx = _make_indexer()
    status = idx.get_update_status()

    assert "update_config" in status
    assert "should_update" in status
    assert "last_update" in status
    assert status["retriever_mode"] == "legacy_metadata"


def test_should_update_database_daily_true_when_last_update_old():
    idx = _make_indexer()
    idx.update_config["auto_update"] = True
    idx.update_config["update_frequency"] = "daily"
    idx.update_config["last_update"] = (datetime.now() - timedelta(days=2)).isoformat()

    assert idx.should_update_database() is True


def test_should_update_database_every_n_false_when_recent():
    idx = _make_indexer()
    idx.update_config["auto_update"] = True
    idx.update_config["update_frequency"] = "every_7"
    idx.update_config["last_update"] = (datetime.now() - timedelta(days=1)).isoformat()

    assert idx.should_update_database() is False
