from zotero_mcp import server


class DummyContext:
    def info(self, *_args, **_kwargs):
        return None

    def error(self, *_args, **_kwargs):
        return None

    def warn(self, *_args, **_kwargs):
        return None


class FakeSearch:
    def __init__(self, payload):
        self.payload = payload
        self.last_query = None
        self.last_limit = None
        self.last_filters = None

    def search(self, query, limit, filters):
        self.last_query = query
        self.last_limit = limit
        self.last_filters = filters
        return self.payload


def _make_payload(abstract_text: str, matched_text: str):
    return {
        "results": [
            {
                "item_key": "ITEM1234",
                "similarity_score": 0.88,
                "matched_text": matched_text,
                "metadata": {},
                "zotero_item": {
                    "data": {
                        "title": "Example Title",
                        "itemType": "journalArticle",
                        "creators": [{"firstName": "Ada", "lastName": "Lovelace"}],
                        "date": "2025",
                        "abstractNote": abstract_text,
                        "tags": [{"tag": "ai"}],
                    }
                },
            }
        ]
    }


def test_semantic_search_default_does_not_truncate(monkeypatch):
    abstract_text = "A" * 260
    matched_text = "B" * 420
    fake_search = FakeSearch(_make_payload(abstract_text, matched_text))
    monkeypatch.setattr("zotero_mcp.semantic_search.create_semantic_search", lambda *_args, **_kwargs: fake_search)

    result = server.semantic_search(query="test query", ctx=DummyContext())

    assert f"**Abstract:** {abstract_text}" in result
    assert f"**Matched Content:** {matched_text}" in result


def test_semantic_search_optional_truncate_for_abstract(monkeypatch):
    abstract_text = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    matched_text = "unchanged matched text"
    fake_search = FakeSearch(_make_payload(abstract_text, matched_text))
    monkeypatch.setattr("zotero_mcp.semantic_search.create_semantic_search", lambda *_args, **_kwargs: fake_search)

    result = server.semantic_search(
        query="test query",
        abstract_max_chars=10,
        ctx=DummyContext(),
    )

    assert "**Abstract:** ABCDEFGHIJ..." in result
    assert f"**Matched Content:** {matched_text}" in result


def test_semantic_search_optional_truncate_for_matched_content(monkeypatch):
    abstract_text = "short abstract"
    matched_text = "0123456789abcdefghij"
    fake_search = FakeSearch(_make_payload(abstract_text, matched_text))
    monkeypatch.setattr("zotero_mcp.semantic_search.create_semantic_search", lambda *_args, **_kwargs: fake_search)

    result = server.semantic_search(
        query="test query",
        matched_content_max_chars=8,
        ctx=DummyContext(),
    )

    assert f"**Abstract:** {abstract_text}" in result
    assert "**Matched Content:** 01234567..." in result


def test_semantic_search_rejects_non_positive_limits():
    result = server.semantic_search(query="test", abstract_max_chars=0, ctx=DummyContext())
    assert "abstract_max_chars must be a positive integer" in result

    result = server.semantic_search(query="test", matched_content_max_chars=-1, ctx=DummyContext())
    assert "matched_content_max_chars must be a positive integer" in result


def test_semantic_search_parses_and_translates_filters(monkeypatch):
    fake_search = FakeSearch(_make_payload("x", "y"))
    monkeypatch.setattr("zotero_mcp.semantic_search.create_semantic_search", lambda *_args, **_kwargs: fake_search)

    server.semantic_search(
        query="test query",
        filters='{"itemType":"note"}',
        ctx=DummyContext(),
    )

    assert fake_search.last_filters == {"item_type": "note"}
