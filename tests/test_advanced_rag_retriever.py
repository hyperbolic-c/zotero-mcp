from pathlib import Path

import pytest

from zotero_mcp.retrievers.advanced_rag import AdvancedRAGRetriever, CandidateChunk


class FakeChromaClient:
    def __init__(self):
        self.docs = []
        self.metas = []
        self.ids = []

    def reset_collection(self):
        self.docs = []
        self.metas = []
        self.ids = []

    def get_existing_ids(self, ids):
        return set()

    def upsert_documents(self, documents, metadatas, ids):
        self.docs = list(documents)
        self.metas = list(metadatas)
        self.ids = list(ids)

    def search(self, query_texts, n_results, where=None):
        return {
            "ids": [["I1:ATT1:0", "I1:meta:0", "I2:meta:0"]],
            "documents": [["content evidence", "meta evidence", "other item"]],
            "metadatas": [[
                {"item_key": "I1", "attachment_key": "ATT1", "chunk_kind": "content", "section_title": "s1"},
                {"item_key": "I1", "attachment_key": "meta", "chunk_kind": "meta", "section_title": "metadata"},
                {"item_key": "I2", "attachment_key": "meta", "chunk_kind": "meta", "section_title": "metadata"},
            ]],
            "distances": [[0.1, 0.2, 0.6]],
        }

    def get_collection_info(self):
        return {"name": "zotero_rag_chunks_v1", "count": 3, "embedding_model": "default", "persist_directory": "/tmp"}


class FakeEngine:
    def __init__(self, md_root):
        self.semantic_config = {
            "advanced_rag": {
                "md_root": str(md_root),
                "chunk": {"max_chars": 40, "overlap_chars": 5, "min_chunk_chars": 5},
                "ingest": {"strip_images": True},
                "reranker": {"enabled": False, "backend": "flashrank", "model_name": "ms-marco-MiniLM-L-12-v2"},
                "retrieve": {"candidate_k": 10, "evidence_per_item": 1, "meta_weight": 0.85},
            }
        }
        self.db_path = None
        self.zotero_client = self

    def _get_items_from_source(self, limit=None, extract_fulltext=False):
        return [
            {"key": "I1", "data": {"itemType": "journalArticle", "title": "Title 1", "creators": [], "abstractNote": "Abs"}},
            {"key": "I2", "data": {"itemType": "journalArticle", "title": "Title 2", "creators": [], "abstractNote": "Abs2"}},
        ]

    def _parse_creators_string(self, creators_str):
        return []

    def item(self, item_key):
        return {"data": {"key": item_key, "title": f"Title {item_key}", "creators": []}}


class FakeLocalItem:
    def __init__(self, key, item_id):
        self.key = key
        self.item_id = item_id


class FakeReader:
    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        return None

    def get_items_with_text(self, limit=None, include_fulltext=False):
        return [FakeLocalItem("I1", 1), FakeLocalItem("I2", 2)]

    def _iter_parent_attachments(self, parent_item_id):
        if parent_item_id == 1:
            yield ("ATT1", "storage:a.pdf", "application/pdf")


def _make_retriever(md_root, monkeypatch, reranker_enabled=False):
    from zotero_mcp.retrievers import advanced_rag

    monkeypatch.setattr(advanced_rag, "is_local_mode", lambda: True)
    monkeypatch.setattr(advanced_rag, "LocalZoteroReader", FakeReader)

    engine = FakeEngine(md_root)
    client = FakeChromaClient()
    return AdvancedRAGRetriever(engine=engine, chroma_client=client), client


# --- md_root default / warning tests ---

def test_md_root_default_is_empty_string():
    """The in-code default md_root must be empty, not a developer-specific path."""
    from zotero_mcp import semantic_search as ss
    defaults = ss.ZoteroSemanticSearch._get_advanced_rag_defaults()
    assert defaults["md_root"] == "", (
        f"md_root default must be empty string, got: {defaults['md_root']!r}"
    )


def test_ingest_warns_when_md_root_empty(monkeypatch, caplog, tmp_path):
    """ingest_data warns the user when retriever_mode=advanced_rag but md_root is empty."""
    import logging
    from zotero_mcp.retrievers import advanced_rag

    monkeypatch.setattr(advanced_rag, "is_local_mode", lambda: True)
    monkeypatch.setattr(advanced_rag, "LocalZoteroReader", FakeReader)

    engine = FakeEngine("")  # empty md_root
    client = FakeChromaClient()
    retriever = AdvancedRAGRetriever(engine=engine, chroma_client=client)

    with caplog.at_level(logging.WARNING, logger="zotero_mcp.retrievers.advanced_rag"):
        retriever.ingest_data()

    assert any("md_root" in record.message for record in caplog.records), (
        "Expected a warning mentioning 'md_root' when md_root is empty"
    )


# --- ingest stats count chunks not items ---

def test_ingest_stats_use_chunk_keys(monkeypatch, tmp_path):
    """Stats returned by ingest_data use 'added_chunks'/'updated_chunks', not 'added_items'."""
    md_root = tmp_path / "md"
    (md_root / "ATT1").mkdir(parents=True)
    (md_root / "ATT1" / "a.md").write_text("# Intro\n" + "x " * 100, encoding="utf-8")

    retriever, _ = _make_retriever(md_root, monkeypatch)
    stats = retriever.ingest_data()
    assert "added_chunks" in stats, "Expected 'added_chunks' key in ingest stats"
    assert "updated_chunks" in stats, "Expected 'updated_chunks' key in ingest stats"
    assert "added_items" not in stats, "Unexpected 'added_items' in advanced_rag stats"
    assert "updated_items" not in stats, "Unexpected 'updated_items' in advanced_rag stats"


# --- ingest exception logging ---

def test_ingest_logs_item_errors(monkeypatch, tmp_path, caplog):
    """Errors processing individual items must be logged, not silently counted."""
    import logging
    from zotero_mcp.retrievers import advanced_rag

    monkeypatch.setattr(advanced_rag, "is_local_mode", lambda: True)
    monkeypatch.setattr(advanced_rag, "LocalZoteroReader", FakeReader)

    engine = FakeEngine(str(tmp_path / "md"))
    client = FakeChromaClient()

    # Force _build_item_chunks to raise on item I1
    retriever = AdvancedRAGRetriever(engine=engine, chroma_client=client)
    original_build = retriever._build_item_chunks

    def _broken_build(item, attachment_keys):
        if item.get("key") == "I1":
            raise RuntimeError("simulated parse failure")
        return original_build(item, attachment_keys)

    monkeypatch.setattr(retriever, "_build_item_chunks", _broken_build)

    with caplog.at_level(logging.WARNING, logger="zotero_mcp.retrievers.advanced_rag"):
        stats = retriever.ingest_data()

    assert stats["errors"] == 1
    assert any("I1" in record.message for record in caplog.records), (
        "Expected a log message mentioning item key 'I1'"
    )


# --- reranker error status ---

def test_reranker_init_error_distinguishes_import_vs_runtime(monkeypatch):
    """_init_reranker returns 'degraded:package_missing' for ImportError,
    'degraded:init_error:...' for other exceptions."""
    from zotero_mcp.retrievers import advanced_rag

    engine = FakeEngine("")
    engine.semantic_config["advanced_rag"]["reranker"] = {
        "enabled": True,
        "backend": "flashrank",
        "model_name": "ms-marco-MiniLM-L-12-v2",
    }
    client = FakeChromaClient()

    # Simulate ImportError
    monkeypatch.setitem(
        __import__("sys").modules,
        "flashrank",
        None,  # makes `import flashrank` raise ImportError
    )
    retriever = AdvancedRAGRetriever(engine=engine, chroma_client=client)
    assert retriever._reranker_status == "degraded:package_missing"

    # Simulate runtime error (e.g. bad model name, ONNX fail) by patching Ranker
    import types
    fake_flashrank = types.ModuleType("flashrank")

    class BadRanker:
        def __init__(self, **kwargs):
            raise OSError("model not found")

    fake_flashrank.Ranker = BadRanker
    monkeypatch.setitem(__import__("sys").modules, "flashrank", fake_flashrank)

    retriever2 = AdvancedRAGRetriever(engine=engine, chroma_client=client)
    assert retriever2._reranker_status.startswith("degraded:init_error:"), (
        f"Expected 'degraded:init_error:...', got: {retriever2._reranker_status!r}"
    )


# --- top_n wired into reranker ---

def test_rerank_passes_top_n_to_ranker(monkeypatch, tmp_path):
    """_rerank must pass top_n from config to ranker.rank()."""
    import types
    from zotero_mcp.retrievers import advanced_rag

    engine = FakeEngine(str(tmp_path))
    engine.semantic_config["advanced_rag"]["reranker"] = {
        "enabled": True,
        "backend": "flashrank",
        "model_name": "ms-marco-MiniLM-L-12-v2",
        "top_n": 3,
    }

    captured = {}

    fake_flashrank = types.ModuleType("flashrank")

    class FakeRanker:
        def __init__(self, **kwargs):
            pass

        def rank(self, request, top_n=None):
            captured["top_n"] = top_n
            # Return first top_n passages with mock scores
            passages = request.passages[:top_n] if top_n else request.passages
            return [{"id": str(i), "score": 0.9} for i in range(len(passages))]

    class FakeRerankRequest:
        def __init__(self, query, passages):
            self.query = query
            self.passages = passages

    fake_flashrank.Ranker = FakeRanker
    fake_flashrank.RerankRequest = FakeRerankRequest
    monkeypatch.setitem(__import__("sys").modules, "flashrank", fake_flashrank)

    client = FakeChromaClient()
    retriever = AdvancedRAGRetriever(engine=engine, chroma_client=client)

    # Build some fake candidates
    candidates = [
        CandidateChunk("I1", "ATT1", f"text {i}", {}, 0.9 - i * 0.1, 0.9 - i * 0.1)
        for i in range(5)
    ]
    retriever._rerank("query", candidates)

    assert captured.get("top_n") == 3, (
        f"Expected top_n=3 passed to ranker.rank(), got: {captured.get('top_n')!r}"
    )


# --- delete_item delegates through retriever in advanced_rag mode ---

def test_delete_item_advanced_rag_raises_not_implemented(monkeypatch):
    """delete_item must raise NotImplementedError (not silently fail) for advanced_rag mode."""
    from zotero_mcp import semantic_search as ss

    class StubRetriever:
        def ingest_data(self, **kw): return {}
        def search(self, **kw): return {}
        def get_database_status(self): return {"collection_info": {}}

    monkeypatch.setattr(ss, "get_zotero_client", lambda: object())
    monkeypatch.setattr(ss, "create_retriever", lambda mode, engine: StubRetriever())

    search = ss.ZoteroSemanticSearch(chroma_client=FakeChromaClient())
    search.retriever_mode = "advanced_rag"

    with pytest.raises(NotImplementedError):
        search.delete_item("SOMEKEY")


def test_advanced_rag_ingest_and_item_level_search(monkeypatch, tmp_path):
    md_root = tmp_path / "md"
    att_dir = md_root / "ATT1"
    att_dir.mkdir(parents=True)
    (att_dir / "a.md").write_text("# Intro\nFirst part text.", encoding="utf-8")
    (att_dir / "b.md").write_text("## More\nSecond part text.", encoding="utf-8")

    from zotero_mcp.retrievers import advanced_rag

    monkeypatch.setattr(advanced_rag, "is_local_mode", lambda: True)
    monkeypatch.setattr(advanced_rag, "LocalZoteroReader", FakeReader)

    engine = FakeEngine(md_root)
    client = FakeChromaClient()
    retriever = AdvancedRAGRetriever(engine=engine, chroma_client=client)

    stats = retriever.ingest_data(force_rebuild=False)
    assert stats["total_items"] == 2
    assert any(doc_id.startswith("I1:ATT1:") for doc_id in client.ids)
    assert "I1:meta:0" in client.ids

    result = retriever.search("first text", limit=2)
    assert result["total_found"] == 2
    assert result["results"][0]["item_key"] == "I1"
    assert result["results"][0]["evidence"][0]["chunk_kind"] == "content"


# --- batched ingest tests ---


class BatchTrackingChromaClient(FakeChromaClient):
    """FakeChromaClient that records each upsert call separately."""

    def __init__(self):
        super().__init__()
        self.upsert_calls: list[list[str]] = []  # list of id-lists per call
        self.all_ids: list[str] = []

    def upsert_documents(self, documents, metadatas, ids):
        self.upsert_calls.append(list(ids))
        self.all_ids.extend(ids)
        # keep self.ids pointing to accumulated set for compatibility
        self.ids = self.all_ids


def _make_retriever_with_batch_cfg(md_root, monkeypatch, batch_size, sleep_seconds=0.0):
    from zotero_mcp.retrievers import advanced_rag

    monkeypatch.setattr(advanced_rag, "is_local_mode", lambda: True)
    monkeypatch.setattr(advanced_rag, "LocalZoteroReader", FakeReader)

    engine = FakeEngine(md_root)
    # Override ingest config with batch settings
    engine.semantic_config["advanced_rag"]["ingest"] = {
        "strip_images": True,
        "batch_size": batch_size,
        "sleep_between_batches": sleep_seconds,
    }
    client = BatchTrackingChromaClient()
    return AdvancedRAGRetriever(engine=engine, chroma_client=client), client


def test_ingest_flushes_in_multiple_batches_when_batch_size_exceeded(monkeypatch, tmp_path):
    """When batch_size is smaller than total chunks, upsert_documents is called multiple times."""
    md_root = tmp_path / "md"
    att_dir = md_root / "ATT1"
    att_dir.mkdir(parents=True)
    # Write enough content to produce several chunks (max_chars=40 in FakeEngine)
    (att_dir / "a.md").write_text(
        "# Intro\n" + "word " * 30 + "\n## Body\n" + "word " * 30,
        encoding="utf-8",
    )

    # batch_size=1 forces a flush after every chunk
    retriever, client = _make_retriever_with_batch_cfg(md_root, monkeypatch, batch_size=1)
    stats = retriever.ingest_data()

    total_chunks = stats["added_chunks"] + stats["updated_chunks"]
    assert total_chunks > 0, "Expected at least one chunk to be indexed"
    assert len(client.upsert_calls) > 1, (
        f"Expected multiple upsert calls with batch_size=1, got {len(client.upsert_calls)}"
    )
    # All chunks must still be present in total
    assert len(client.all_ids) == total_chunks


def test_ingest_sleeps_between_batches(monkeypatch, tmp_path):
    """When sleep_between_batches > 0, time.sleep is called between batch flushes."""
    import time
    from zotero_mcp.retrievers import advanced_rag

    md_root = tmp_path / "md"
    att_dir = md_root / "ATT1"
    att_dir.mkdir(parents=True)
    (att_dir / "a.md").write_text(
        "# Intro\n" + "word " * 30 + "\n## Body\n" + "word " * 30,
        encoding="utf-8",
    )

    sleep_calls: list[float] = []
    monkeypatch.setattr("zotero_mcp.retrievers.advanced_rag.time.sleep", lambda s: sleep_calls.append(s))

    retriever, client = _make_retriever_with_batch_cfg(
        md_root, monkeypatch, batch_size=1, sleep_seconds=0.5
    )
    retriever.ingest_data()

    assert len(sleep_calls) > 0, "Expected time.sleep to be called between batches"
    assert all(s == 0.5 for s in sleep_calls), (
        f"Expected all sleep calls to use configured 0.5s, got: {sleep_calls}"
    )


# ---------------------------------------------------------------------------
# chunker backend / strategy metadata in ingested chunks
# ---------------------------------------------------------------------------


def _make_retriever_with_chunk_cfg(md_root, monkeypatch, chunk_cfg: dict):
    """Helper: build AdvancedRAGRetriever with a custom chunk config."""
    from zotero_mcp.retrievers import advanced_rag

    monkeypatch.setattr(advanced_rag, "is_local_mode", lambda: True)
    monkeypatch.setattr(advanced_rag, "LocalZoteroReader", FakeReader)

    engine = FakeEngine(md_root)
    engine.semantic_config["advanced_rag"]["chunk"] = chunk_cfg
    client = FakeChromaClient()
    return AdvancedRAGRetriever(engine=engine, chroma_client=client), client


def test_langchain_backend_metadata_written_to_chunks(monkeypatch, tmp_path):
    """Content chunks ingested with langchain backend must carry chunker_backend metadata."""
    md_root = tmp_path / "md"
    att_dir = md_root / "ATT1"
    att_dir.mkdir(parents=True)
    (att_dir / "a.md").write_text(
        "# Introduction\n\nThis is a sufficiently long introduction section. " * 5,
        encoding="utf-8",
    )

    chunk_cfg = {
        "backend": "langchain",
        "strategy": "markdown_recursive_v1",
        "chunk_size": 500,
        "chunk_overlap": 50,
        "min_chunk_chars": 30,
    }
    retriever, client = _make_retriever_with_chunk_cfg(md_root, monkeypatch, chunk_cfg)
    retriever.ingest_data()

    content_metas = [m for m in client.metas if m.get("chunk_kind") == "content"]
    assert content_metas, "Expected at least one content chunk"
    for meta in content_metas:
        assert meta.get("chunker_backend") == "langchain", (
            f"Expected chunker_backend='langchain', got: {meta.get('chunker_backend')!r}"
        )
        assert meta.get("chunker_strategy") == "markdown_recursive_v1", (
            f"Expected chunker_strategy='markdown_recursive_v1', got: {meta.get('chunker_strategy')!r}"
        )


def test_legacy_backend_metadata_written_to_chunks(monkeypatch, tmp_path):
    """Content chunks ingested with legacy backend must carry chunker_backend='legacy'."""
    md_root = tmp_path / "md"
    att_dir = md_root / "ATT1"
    att_dir.mkdir(parents=True)
    (att_dir / "a.md").write_text(
        "# Section\n\nLegacy chunking text that is long enough. " * 5,
        encoding="utf-8",
    )

    chunk_cfg = {
        "backend": "legacy",
        "max_chars": 200,
        "overlap_chars": 20,
        "min_chunk_chars": 20,
    }
    retriever, client = _make_retriever_with_chunk_cfg(md_root, monkeypatch, chunk_cfg)
    retriever.ingest_data()

    content_metas = [m for m in client.metas if m.get("chunk_kind") == "content"]
    assert content_metas, "Expected at least one content chunk"
    for meta in content_metas:
        assert meta.get("chunker_backend") == "legacy", (
            f"Expected chunker_backend='legacy', got: {meta.get('chunker_backend')!r}"
        )


def test_old_max_chars_config_still_produces_chunks(monkeypatch, tmp_path):
    """Backward-compat: old max_chars/overlap_chars config must not break ingestion."""
    md_root = tmp_path / "md"
    att_dir = md_root / "ATT1"
    att_dir.mkdir(parents=True)
    (att_dir / "a.md").write_text(
        "# Old Config\n\nSome text that should be indexed even with legacy config keys. " * 5,
        encoding="utf-8",
    )

    # Simulate an old-style config with no backend key and old field names
    chunk_cfg = {
        "max_chars": 300,
        "overlap_chars": 30,
        "min_chunk_chars": 20,
    }
    retriever, client = _make_retriever_with_chunk_cfg(md_root, monkeypatch, chunk_cfg)
    retriever.ingest_data()

    # Should produce chunks without crashing
    assert any(doc_id.startswith("I1:ATT1:") for doc_id in client.ids), (
        "Expected content chunks for I1 with legacy config"
    )


def test_meta_weight_default_is_0_70():
    """The in-code default meta_weight for advanced_rag must be 0.70."""
    from zotero_mcp import semantic_search as ss
    defaults = ss.ZoteroSemanticSearch._get_advanced_rag_defaults()
    assert defaults["retrieve"]["meta_weight"] == 0.70, (
        f"Expected meta_weight default=0.70, got: {defaults['retrieve']['meta_weight']!r}"
    )


def test_defaults_include_langchain_backend():
    """The in-code defaults must specify backend='langchain' for the chunk section."""
    from zotero_mcp import semantic_search as ss
    defaults = ss.ZoteroSemanticSearch._get_advanced_rag_defaults()
    assert defaults["chunk"]["backend"] == "langchain", (
        f"Expected chunk.backend='langchain' in defaults, got: {defaults['chunk']['backend']!r}"
    )
    assert defaults["chunk"]["strategy"] == "markdown_recursive_v1", (
        f"Expected chunk.strategy='markdown_recursive_v1', got: {defaults['chunk']['strategy']!r}"
    )
