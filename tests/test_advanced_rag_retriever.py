from pathlib import Path

from zotero_mcp.retrievers.advanced_rag import AdvancedRAGRetriever


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
