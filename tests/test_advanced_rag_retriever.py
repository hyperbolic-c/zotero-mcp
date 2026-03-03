from pathlib import Path

import pytest

from zotero_mcp.rag.advanced_rag import AdvancedRAGRetriever, CandidateChunk


class FakeChromaClient:
    def __init__(self):
        self.docs = []
        self.metas = []
        self.ids = []
        self.raw_docs = []
        self.raw_metas = []
        self.raw_ids = []
        self.ref_lookup: dict[str, str] = {}
        self.persist_directory = "/tmp"
        self.embedding_model = "default"
        self.embedding_config = {}
        self.collection = self

    def reset_collection(self):
        self.docs = []
        self.metas = []
        self.ids = []
        self.raw_docs = []
        self.raw_metas = []
        self.raw_ids = []
        self.ref_lookup = {}

    def get_existing_ids(self, ids):
        return set()

    def upsert_documents(self, documents, metadatas, ids):
        self.docs = list(documents)
        self.metas = list(metadatas)
        self.ids = list(ids)

    def upsert_raw(self, documents, metadatas, ids):
        self.raw_docs.extend(list(documents))
        self.raw_metas.extend(list(metadatas))
        self.raw_ids.extend(list(ids))
        for doc_id, doc in zip(ids, documents):
            self.ref_lookup[doc_id] = doc

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

    def get(self, ids=None, include=None, where=None, limit=None):
        if where is not None and where.get("item_key"):
            prefix = f"{where['item_key']}:"
            candidates = [rid for rid in self.ref_lookup if rid.startswith(prefix)]
            if limit is not None:
                candidates = candidates[:limit]
            return {
                "ids": candidates,
                "documents": [self.ref_lookup[i] for i in candidates],
                "metadatas": [],
            }
        if ids is None:
            return {"ids": [], "documents": [], "metadatas": []}
        ret_ids = [doc_id for doc_id in ids if doc_id in self.ref_lookup]
        return {
            "ids": ret_ids,
            "documents": [self.ref_lookup[i] for i in ret_ids],
            "metadatas": [],
        }


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
        self.config_path = None
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


def _build_advanced_retriever(engine: FakeEngine, client: FakeChromaClient, refs_client=None):
    return AdvancedRAGRetriever(
        chroma_client=client,
        refs_client=refs_client,
        config=engine.semantic_config["advanced_rag"],
        db_path=engine.db_path,
        config_path=engine.config_path,
        get_items_from_source_fn=engine._get_items_from_source,
        parse_creators_fn=engine._parse_creators_string,
        get_item_by_key_fn=engine.item,
    )


class FakeLocalItem:
    def __init__(self, key, item_id):
        self.key = key
        self.item_id = item_id
        self.item_type = "journalArticle"
        self.title = f"Title {key}"
        self.abstract = ""
        self.extra = ""
        self.creators = ""
        self.notes = ""
        self.doi = None
        self.date_added = "2024-01-01T00:00:00Z"
        self.date_modified = "2024-01-01T00:00:00Z"


class FakeReader:
    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        return None

    def get_items_with_text(self, limit=None, include_fulltext=False):
        return [FakeLocalItem("I1", 1), FakeLocalItem("I2", 2)]

    def get_items_by_keys(self, keys):
        all_items = {
            "I1": FakeLocalItem("I1", 1),
            "I2": FakeLocalItem("I2", 2),
        }
        return [all_items[k] for k in keys if k in all_items]

    def _iter_parent_attachments(self, parent_item_id):
        if parent_item_id == 1:
            yield ("ATT1", "storage:a.pdf", "application/pdf")


def _make_retriever(md_root, monkeypatch, reranker_enabled=False):
    from zotero_mcp.rag import advanced_rag

    monkeypatch.setattr(advanced_rag, "is_local_mode", lambda: True)
    monkeypatch.setattr(advanced_rag, "LocalZoteroReader", FakeReader)

    engine = FakeEngine(md_root)
    client = FakeChromaClient()
    return _build_advanced_retriever(engine, client, refs_client=client), client


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
    from zotero_mcp.rag import advanced_rag

    monkeypatch.setattr(advanced_rag, "is_local_mode", lambda: True)
    monkeypatch.setattr(advanced_rag, "LocalZoteroReader", FakeReader)

    engine = FakeEngine("")  # empty md_root
    client = FakeChromaClient()
    retriever = _build_advanced_retriever(engine, client)

    with caplog.at_level(logging.WARNING, logger="zotero_mcp.rag.ingestor"):
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
    from zotero_mcp.rag import advanced_rag

    monkeypatch.setattr(advanced_rag, "is_local_mode", lambda: True)
    monkeypatch.setattr(advanced_rag, "LocalZoteroReader", FakeReader)

    engine = FakeEngine(str(tmp_path / "md"))
    client = FakeChromaClient()

    # Force _build_item_chunks to raise on item I1
    retriever = _build_advanced_retriever(engine, client)
    original_build = retriever._build_item_chunks

    def _broken_build(item, attachment_keys):
        if item.get("key") == "I1":
            raise RuntimeError("simulated parse failure")
        return original_build(item, attachment_keys)

    monkeypatch.setattr(retriever, "_build_item_chunks", _broken_build)

    with caplog.at_level(logging.WARNING, logger="zotero_mcp.rag.ingestor"):
        stats = retriever.ingest_data()

    assert stats["errors"] == 1
    assert any("I1" in record.message for record in caplog.records), (
        "Expected a log message mentioning item key 'I1'"
    )


# --- reranker error status ---

def test_reranker_init_error_distinguishes_import_vs_runtime(monkeypatch):
    """_init_reranker returns 'degraded:package_missing' for ImportError,
    'degraded:init_error:...' for other exceptions."""
    from zotero_mcp.rag import advanced_rag

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
    retriever = _build_advanced_retriever(engine, client)
    assert retriever._reranker_status == "degraded:package_missing"

    # Simulate runtime error (e.g. bad model name, ONNX fail) by patching Ranker
    import types
    fake_flashrank = types.ModuleType("flashrank")

    class BadRanker:
        def __init__(self, **kwargs):
            raise OSError("model not found")

    fake_flashrank.Ranker = BadRanker
    monkeypatch.setitem(__import__("sys").modules, "flashrank", fake_flashrank)

    retriever2 = _build_advanced_retriever(engine, client)
    assert retriever2._reranker_status.startswith("degraded:init_error:"), (
        f"Expected 'degraded:init_error:...', got: {retriever2._reranker_status!r}"
    )


# --- top_n wired into reranker ---

def test_rerank_passes_top_n_to_ranker(monkeypatch, tmp_path):
    """_rerank must pass top_n from config to ranker.rank()."""
    import types
    from zotero_mcp.rag import advanced_rag

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
    retriever = _build_advanced_retriever(engine, client)

    # Build some fake candidates
    candidates = [
        CandidateChunk("I1", "ATT1", f"text {i}", {}, 0.9 - i * 0.1, 0.9 - i * 0.1)
        for i in range(5)
    ]
    retriever._rerank("query", candidates)

    assert captured.get("top_n") == 3, (
        f"Expected top_n=3 passed to ranker.rank(), got: {captured.get('top_n')!r}"
    )


# --- semantic search boundary tests ---

def test_semantic_search_does_not_expose_delete_item(monkeypatch):
    """Search interface should not expose indexing/deletion operations."""
    from zotero_mcp import semantic_search as ss

    class StubRetriever:
        def ingest_data(self, **kw): return {}
        def search(self, **kw): return {}
        def get_database_status(self): return {"collection_info": {}}

    monkeypatch.setattr(ss, "get_zotero_client", lambda: object())
    monkeypatch.setattr(ss, "create_retriever", lambda mode, **kwargs: StubRetriever())

    search = ss.ZoteroSemanticSearch(chroma_client=FakeChromaClient())
    assert not hasattr(search, "delete_item")


def test_advanced_rag_ingest_and_item_level_search(monkeypatch, tmp_path):
    md_root = tmp_path / "md"
    att_dir = md_root / "ATT1"
    att_dir.mkdir(parents=True)
    (att_dir / "a.md").write_text("# Intro\nFirst part text.", encoding="utf-8")
    (att_dir / "b.md").write_text("## More\nSecond part text.", encoding="utf-8")

    from zotero_mcp.rag import advanced_rag

    monkeypatch.setattr(advanced_rag, "is_local_mode", lambda: True)
    monkeypatch.setattr(advanced_rag, "LocalZoteroReader", FakeReader)

    engine = FakeEngine(md_root)
    client = FakeChromaClient()
    retriever = _build_advanced_retriever(engine, client)

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
    from zotero_mcp.rag import advanced_rag

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
    return _build_advanced_retriever(engine, client, refs_client=client), client


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
    from zotero_mcp.rag import advanced_rag

    md_root = tmp_path / "md"
    att_dir = md_root / "ATT1"
    att_dir.mkdir(parents=True)
    (att_dir / "a.md").write_text(
        "# Intro\n" + "word " * 30 + "\n## Body\n" + "word " * 30,
        encoding="utf-8",
    )

    sleep_calls: list[float] = []
    monkeypatch.setattr("zotero_mcp.rag.advanced_rag.time.sleep", lambda s: sleep_calls.append(s))

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
    from zotero_mcp.rag import advanced_rag

    monkeypatch.setattr(advanced_rag, "is_local_mode", lambda: True)
    monkeypatch.setattr(advanced_rag, "LocalZoteroReader", FakeReader)

    engine = FakeEngine(md_root)
    engine.semantic_config["advanced_rag"]["chunk"] = chunk_cfg
    client = FakeChromaClient()
    return _build_advanced_retriever(engine, client, refs_client=client), client


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


# ---------------------------------------------------------------------------
# CHROMA_MAX_BATCH sub-slicing in _flush_batch
# ---------------------------------------------------------------------------


class LimitEnforcingChromaClient(FakeChromaClient):
    """Raises if any single upsert call exceeds max_per_call items."""

    def __init__(self, max_per_call: int):
        super().__init__()
        self.max_per_call = max_per_call
        self.upsert_calls: list[list[str]] = []
        self.all_ids: list[str] = []

    def upsert_documents(self, documents, metadatas, ids):
        if len(ids) > self.max_per_call:
            raise ValueError(
                f"ChromaDB batch size exceeded: got {len(ids)}, max {self.max_per_call}"
            )
        self.upsert_calls.append(list(ids))
        self.all_ids.extend(ids)
        self.ids = self.all_ids


def test_flush_batch_splits_oversized_single_item_batch(monkeypatch, tmp_path):
    """_flush_batch must split any batch larger than CHROMA_MAX_BATCH into sub-slices.

    This regression test covers the case where a single item produces more chunks
    than ChromaDB's hard limit (e.g. 45779 > 5461).  The fix must ensure that
    upsert_documents is never called with more than CHROMA_MAX_BATCH ids at once.
    """
    from zotero_mcp.rag import advanced_rag, ingestor

    # Use a tiny limit so we can exercise the split without huge data
    tiny_limit = 5
    # Patch both modules: advanced_rag re-exports CHROMA_MAX_BATCH from ingestor,
    # and Ingestor._flush_batch reads the constant from ingestor module scope.
    monkeypatch.setattr(advanced_rag, "CHROMA_MAX_BATCH", tiny_limit)
    monkeypatch.setattr(ingestor, "CHROMA_MAX_BATCH", tiny_limit)

    # Build n_chunks > tiny_limit docs/metas/ids to simulate an oversized single-item batch
    n_chunks = tiny_limit * 3 + 1  # e.g. 16 > 5
    docs = [f"doc {i}" for i in range(n_chunks)]
    metas = [{"item_key": "I1", "chunk_kind": "content"} for _ in range(n_chunks)]
    ids = [f"I1:ATT1:{i}" for i in range(n_chunks)]

    client = LimitEnforcingChromaClient(max_per_call=tiny_limit)
    engine = FakeEngine(str(tmp_path))
    retriever = _build_advanced_retriever(engine, client)

    stats = {"added_chunks": 0, "updated_chunks": 0}
    # Must not raise, even though len(ids) > tiny_limit
    retriever._flush_batch(docs, metas, ids, stats)

    assert len(client.all_ids) == n_chunks, (
        f"All {n_chunks} chunks must be upserted; got {len(client.all_ids)}"
    )
    assert len(client.upsert_calls) > 1, (
        "Expected multiple upsert calls when batch exceeds CHROMA_MAX_BATCH"
    )
    for call_ids in client.upsert_calls:
        assert len(call_ids) <= tiny_limit, (
            f"Single upsert call exceeded limit: {len(call_ids)} > {tiny_limit}"
        )
    assert stats["added_chunks"] == n_chunks


# ---------------------------------------------------------------------------
# _flush_batch delegation
# ---------------------------------------------------------------------------


def test_facade_flush_batch_delegates_to_ingestor(tmp_path):
    """AdvancedRAGRetriever._flush_batch must delegate to self._ingestor.flush_batch.

    The facade must not implement flush logic itself - it must delegate to the
    Ingestor to avoid duplicated code.
    """
    engine = FakeEngine(str(tmp_path))
    client = FakeChromaClient()
    retriever = _build_advanced_retriever(engine, client)

    # Track calls to Ingestor.flush_batch
    flush_calls: list[dict] = []
    original_flush = retriever._ingestor.flush_batch

    def tracking_flush_batch(batch_docs, batch_metas, batch_ids, stats):
        flush_calls.append({"docs": batch_docs, "ids": batch_ids})
        original_flush(batch_docs, batch_metas, batch_ids, stats)

    retriever._ingestor.flush_batch = tracking_flush_batch

    docs = ["doc1", "doc2"]
    metas = [{"item_key": "I1"}, {"item_key": "I1"}]
    ids = ["I1:ATT1:0", "I1:ATT1:1"]
    stats = {"added_chunks": 0, "updated_chunks": 0}

    retriever._flush_batch(docs, metas, ids, stats)

    assert len(flush_calls) == 1, (
        "AdvancedRAGRetriever._flush_batch must delegate to self._ingestor.flush_batch exactly once"
    )
    assert flush_calls[0]["ids"] == ids, "flush_batch must pass the same ids to Ingestor"
    assert stats["added_chunks"] == 2


# ---------------------------------------------------------------------------
# Fix 1: CHROMA_MAX_BATCH derived from SQLite at runtime
# ---------------------------------------------------------------------------


def test_chroma_max_batch_derived_from_sqlite_limit():
    """CHROMA_MAX_BATCH must be computed from SQLite's MAX_VARIABLE_NUMBER compile option,
    not a magic literal, so it stays correct if the SQLite build changes.

    ChromaDB internally uses VARIABLES_PER_RECORD=6 and computes:
        max_batch = MAX_VARIABLE_NUMBER // 6
    We mirror that formula so our constant stays in sync.
    """
    import sqlite3
    from zotero_mcp.rag.advanced_rag import CHROMA_MAX_BATCH

    con = sqlite3.connect(":memory:")
    sqlite_var_limit = None
    for row in con.execute("pragma compile_options"):
        if "MAX_VARIABLE_NUMBER" in row[0]:
            sqlite_var_limit = int(row[0].split("=")[1])
            break
    if sqlite_var_limit is None:
        sqlite_var_limit = con.getlimit(9)  # fallback: runtime limit
    con.close()

    # ChromaDB uses VARIABLES_PER_RECORD=6 (from embeddings_queue.py)
    expected = sqlite_var_limit // 6
    assert CHROMA_MAX_BATCH == expected, (
        f"CHROMA_MAX_BATCH={CHROMA_MAX_BATCH} != expected formula result={expected}. "
        "The constant must be derived via sqlite MAX_VARIABLE_NUMBER // 6."
    )


# ---------------------------------------------------------------------------
# Fix 2: get_existing_ids slices oversized id lists
# ---------------------------------------------------------------------------


def test_get_existing_ids_slices_large_id_lists(monkeypatch):
    """get_existing_ids must split large id lists into sub-batches to avoid
    SQLite variable-number errors, and return the union of all results."""
    from zotero_mcp.chroma_client import ChromaClient, CHROMA_GET_MAX_BATCH

    call_sizes: list[int] = []

    class FakeCollection:
        def get(self, ids, include):
            call_sizes.append(len(ids))
            # Simulate that the first half of any batch "exists"
            half = ids[: len(ids) // 2]
            return {"ids": half}

    client = ChromaClient.__new__(ChromaClient)
    client.collection = FakeCollection()

    n_ids = CHROMA_GET_MAX_BATCH * 2 + 3
    ids = [f"id_{i}" for i in range(n_ids)]

    result = client.get_existing_ids(ids)

    assert isinstance(result, set)
    assert len(call_sizes) > 1, (
        f"Expected multiple collection.get calls for {n_ids} ids, got {len(call_sizes)}"
    )
    for sz in call_sizes:
        assert sz <= CHROMA_GET_MAX_BATCH, (
            f"A single get call used {sz} ids, exceeding CHROMA_GET_MAX_BATCH={CHROMA_GET_MAX_BATCH}"
        )


def test_ingest_stores_references_in_separate_collection(monkeypatch, tmp_path):
    md_root = tmp_path / "md"
    att_dir = md_root / "ATT1"
    att_dir.mkdir(parents=True)
    content = (
        "# Intro\n\n"
        + ("main body " * 1200)
        + "\n\n## References\n\n"
        + "[1] First ref doi:10.1000/1\n"
        + "[2] Second ref doi:10.1000/2\n"
    )
    (att_dir / "a.md").write_text(content, encoding="utf-8")

    retriever, client = _make_retriever(md_root, monkeypatch)
    retriever.ingest_data()

    assert any(rid.startswith("I1:ATT1:ref:") for rid in client.raw_ids)
    assert "I1:ATT1:ref:1" in client.raw_ids
    assert "I1:ATT1:ref:2" in client.raw_ids
    assert all(":ref:" not in cid for cid in client.ids)


def test_search_resolves_citations_with_partial_id_hits(monkeypatch, tmp_path):
    retriever, client = _make_retriever(tmp_path / "md", monkeypatch)
    client.ref_lookup = {
        "I1:ATT1:ref:1": "Reference 1",
        "I1:ATT1:ref:3": "Reference 3",
    }
    client.search = lambda query_texts, n_results, where=None: {
        "ids": [["I1:ATT1:0"]],
        "documents": [["evidence [1,2-3]"]],
        "metadatas": [[{"item_key": "I1", "attachment_key": "ATT1", "chunk_kind": "content"}]],
        "distances": [[0.1]],
    }

    result = retriever.search("q", limit=1)
    citations = result["results"][0]["resolved_citations"]
    assert [c["ref_num"] for c in citations] == [1, 3]
    assert [c["ref_text"] for c in citations] == ["Reference 1", "Reference 3"]


def test_search_meta_fallback_resolves_citations(monkeypatch, tmp_path):
    retriever, client = _make_retriever(tmp_path / "md", monkeypatch)
    client.ref_lookup = {
        "I1:ATTX:ref:1": "Fallback Ref 1",
    }
    client.search = lambda query_texts, n_results, where=None: {
        "ids": [["I1:meta:0"]],
        "documents": [["meta evidence [1]"]],
        "metadatas": [[{"item_key": "I1", "attachment_key": "meta", "chunk_kind": "meta"}]],
        "distances": [[0.1]],
    }

    result = retriever.search("q", limit=1)
    citations = result["results"][0]["resolved_citations"]
    assert citations
    assert citations[0]["ref_num"] == 1
    assert citations[0]["ref_text"] == "Fallback Ref 1"


def test_force_rebuild_resets_both_collections(monkeypatch, tmp_path):
    """force_rebuild=True must call reset_collection on both chunks and refs clients."""
    md_root = tmp_path / "md"
    att_dir = md_root / "ATT1"
    att_dir.mkdir(parents=True)
    content = (
        "# Intro\n\n"
        + ("main body " * 1200)
        + "\n\n## References\n\n"
        + "[1] First ref doi:10.1000/1\n"
        + "[2] Second ref doi:10.1000/2\n"
    )
    (att_dir / "a.md").write_text(content, encoding="utf-8")

    from zotero_mcp.rag import advanced_rag

    monkeypatch.setattr(advanced_rag, "is_local_mode", lambda: True)
    monkeypatch.setattr(advanced_rag, "LocalZoteroReader", FakeReader)

    engine = FakeEngine(md_root)
    chunks_client = FakeChromaClient()
    refs_client = FakeChromaClient()

    # Track reset calls independently
    chunks_resets: list[int] = []
    refs_resets: list[int] = []
    _orig_chunks_reset = chunks_client.reset_collection
    _orig_refs_reset = refs_client.reset_collection

    def _track_chunks_reset():
        chunks_resets.append(1)
        _orig_chunks_reset()

    def _track_refs_reset():
        refs_resets.append(1)
        _orig_refs_reset()

    chunks_client.reset_collection = _track_chunks_reset
    refs_client.reset_collection = _track_refs_reset

    retriever = _build_advanced_retriever(engine, chunks_client, refs_client=refs_client)

    # Without rebuild, neither reset should be called
    retriever.ingest_data(force_rebuild=False)
    assert len(chunks_resets) == 0
    assert len(refs_resets) == 0

    # With force_rebuild, both must be reset
    retriever.ingest_data(force_rebuild=True)
    assert len(chunks_resets) == 1, "Expected chunks collection to be reset on force_rebuild"
    assert len(refs_resets) == 1, "Expected refs collection to be reset on force_rebuild"


# ---------------------------------------------------------------------------
# Searcher compat import tests
# ---------------------------------------------------------------------------


def test_search_uses_compat_local_reader_monkeypatch(monkeypatch, tmp_path):
    """Monkeypatching advanced_rag.LocalZoteroReader must affect _hydrate_items in Searcher.

    This verifies that searcher.py uses the compat module (not a direct import)
    so that test monkeypatching via advanced_rag.LocalZoteroReader propagates correctly.
    """
    from zotero_mcp.rag import advanced_rag

    monkeypatch.setattr(advanced_rag, "is_local_mode", lambda: True)

    reader_was_called = []

    class TrackingReader:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            reader_was_called.append(True)
            return self

        def __exit__(self, *args):
            return None

        def get_items_by_keys(self, keys):
            return [
                FakeLocalItem(k, i) for i, k in enumerate(keys)
            ]

    monkeypatch.setattr(advanced_rag, "LocalZoteroReader", TrackingReader)

    engine = FakeEngine(str(tmp_path / "md"))
    client = FakeChromaClient()
    retriever = _build_advanced_retriever(engine, client)

    retriever.search("test query", limit=2)

    assert reader_was_called, (
        "TrackingReader was never instantiated during search._hydrate_items. "
        "This means searcher.py is not using compat.LocalZoteroReader — "
        "monkeypatching advanced_rag.LocalZoteroReader has no effect on the Searcher."
    )


def test_hydrate_items_uses_get_items_by_keys(monkeypatch, tmp_path):
    """_hydrate_items must call get_items_by_keys with only the requested keys.

    This verifies that the optimized path (not the O(n) full-scan fallback) is taken.
    """
    from zotero_mcp.rag import advanced_rag

    monkeypatch.setattr(advanced_rag, "is_local_mode", lambda: True)

    fetched_keys: list[list[str]] = []

    class KeyTrackingReader:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def get_items_by_keys(self, keys):
            fetched_keys.append(list(keys))
            return [FakeLocalItem(k, i) for i, k in enumerate(keys)]

    monkeypatch.setattr(advanced_rag, "LocalZoteroReader", KeyTrackingReader)

    engine = FakeEngine(str(tmp_path / "md"))
    client = FakeChromaClient()
    retriever = _build_advanced_retriever(engine, client)

    # FakeChromaClient.search returns items I1 and I2
    retriever.search("test query", limit=2)

    assert len(fetched_keys) == 1, "Expected exactly one get_items_by_keys call"
    # Keys should be only the keys from search results, not all items
    assert set(fetched_keys[0]) == {"I1", "I2"}, (
        f"Expected keys={{I1, I2}}, got {set(fetched_keys[0])}"
    )
