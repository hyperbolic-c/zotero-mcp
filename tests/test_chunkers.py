"""Unit tests for chunkers.py – strategy: markdown_recursive_v1 and legacy."""
from __future__ import annotations

import pytest

from zotero_mcp.retrievers.chunker_types import (
    BACKEND_LANGCHAIN,
    BACKEND_LEGACY,
    STRATEGY_MARKDOWN_RECURSIVE_V1,
    STRATEGY_SEMANTIC_V1,
)
from zotero_mcp.retrievers.chunkers import (
    LangChainMarkdownRecursiveChunker,
    LegacyChunkingBackend,
    get_chunking_backend,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _lc_cfg(**overrides):
    """Return a minimal LangChain chunk config with sane defaults."""
    cfg = {
        "backend": "langchain",
        "strategy": "markdown_recursive_v1",
        "chunk_size": 500,
        "chunk_overlap": 50,
        "min_chunk_chars": 30,
    }
    cfg.update(overrides)
    return cfg


def _legacy_cfg(**overrides):
    cfg = {
        "backend": "legacy",
        "max_chars": 200,
        "overlap_chars": 20,
        "min_chunk_chars": 20,
    }
    cfg.update(overrides)
    return cfg


# ---------------------------------------------------------------------------
# 1. Section title inheritance
# ---------------------------------------------------------------------------

def test_section_title_inherited_from_heading():
    """Chunks must carry the section_title of the nearest heading above them."""
    md = (
        "# Introduction\n\n"
        "This is the introduction text with enough content to form a chunk.\n\n"
        "## Methods\n\n"
        "Detailed methods section content that is long enough to be indexed.\n\n"
        "### Sub-method\n\n"
        "Sub-method description with sufficient length to pass min_chunk_chars.\n"
    )
    chunker = LangChainMarkdownRecursiveChunker(_lc_cfg())
    records = chunker.chunk(md)
    assert records, "Expected at least one chunk"
    titles = {r.section_title for r in records}
    # At least one of the heading texts should appear as a section title
    assert titles - {""}, f"All section_titles are empty; got: {titles}"


def test_section_title_h2_overrides_h1():
    """When content lives under an ## heading, section_title should be the h2 value."""
    md = (
        "# Paper Title\n\n"
        "Abstract text here with enough words to fill a chunk.\n\n"
        "## Results\n\n"
        "Results content: " + "significant findings " * 10 + "\n"
    )
    chunker = LangChainMarkdownRecursiveChunker(_lc_cfg())
    records = chunker.chunk(md)
    result_chunks = [r for r in records if "significant" in r.text]
    assert result_chunks, "Expected chunk containing results text"
    # The deepest heading for results content should be 'Results'
    assert any(r.section_title == "Results" for r in result_chunks), (
        f"Expected section_title='Results' for results chunks, got: {[r.section_title for r in result_chunks]}"
    )


# ---------------------------------------------------------------------------
# 2. Long paragraph with formula-like text – stable chunking
# ---------------------------------------------------------------------------

def test_long_paragraph_does_not_produce_tiny_chunks():
    """Chunks shorter than min_chunk_chars must be filtered out."""
    long_para = "word " * 400  # 2000 chars, no headings
    chunker = LangChainMarkdownRecursiveChunker(_lc_cfg(min_chunk_chars=50))
    records = chunker.chunk(long_para)
    assert records, "Expected at least one chunk from long paragraph"
    for r in records:
        assert len(r.text) >= 50, (
            f"Chunk shorter than min_chunk_chars=50: {len(r.text)!r} chars"
        )


def test_formula_mixed_text_does_not_crash():
    """Text with equation-like patterns must not raise."""
    md = (
        "## Methods\n\n"
        "Let $x = \\sum_{i=0}^{n} \\alpha_i^2$ represent the loss function. "
        "We minimise by gradient descent: $\\nabla_\\theta L = 0$. " * 20
    )
    chunker = LangChainMarkdownRecursiveChunker(_lc_cfg())
    records = chunker.chunk(md)
    assert isinstance(records, list)


# ---------------------------------------------------------------------------
# 3. Headless document – fallback to single-section recursive split
# ---------------------------------------------------------------------------

def test_headless_document_still_chunked():
    """A document with no headings must still produce chunks via recursive split."""
    text = "Just plain prose without any heading markers. " * 50
    chunker = LangChainMarkdownRecursiveChunker(_lc_cfg(chunk_size=200, chunk_overlap=20, min_chunk_chars=30))
    records = chunker.chunk(text)
    assert records, "Expected chunks even with no headings"
    # section_title should be empty string (no heading metadata)
    for r in records:
        assert r.section_title == "", f"Expected empty section_title for headless doc, got {r.section_title!r}"


# ---------------------------------------------------------------------------
# 4. Multi-file chunk index continuity
# ---------------------------------------------------------------------------

def test_chunk_indices_are_globally_sequential():
    """chunk_index values within a single chunk() call must be 0,1,2,..."""
    md = (
        "# Section A\n\n" + "alpha text " * 60 + "\n\n"
        "## Section B\n\n" + "beta text " * 60 + "\n\n"
        "### Section C\n\n" + "gamma text " * 60 + "\n"
    )
    chunker = LangChainMarkdownRecursiveChunker(_lc_cfg(chunk_size=300, chunk_overlap=30, min_chunk_chars=30))
    records = chunker.chunk(md)
    assert len(records) > 1, "Expected multiple chunks"
    indices = [r.chunk_index for r in records]
    assert indices == list(range(len(records))), (
        f"chunk_index values are not sequential: {indices}"
    )


# ---------------------------------------------------------------------------
# 5. Backward-compat: old max_chars/overlap_chars config still works
# ---------------------------------------------------------------------------

def test_legacy_backend_via_get_chunking_backend():
    """get_chunking_backend with backend='legacy' returns LegacyChunkingBackend."""
    cfg = {"backend": "legacy", "max_chars": 100, "overlap_chars": 10, "min_chunk_chars": 10}
    backend = get_chunking_backend(cfg)
    assert isinstance(backend, LegacyChunkingBackend)


def test_legacy_backend_produces_chunks():
    text = "word " * 100  # 500 chars
    backend = LegacyChunkingBackend({"max_chars": 100, "overlap_chars": 10, "min_chunk_chars": 10})
    records = backend.chunk(text)
    assert records
    for r in records:
        assert r.extra_metadata.get("chunker_backend") == BACKEND_LEGACY


def test_langchain_backend_metadata_fields():
    """LangChain chunks must carry chunker_backend and chunker_strategy metadata."""
    md = "# Title\n\nSome content that is long enough. " * 5
    chunker = LangChainMarkdownRecursiveChunker(_lc_cfg())
    records = chunker.chunk(md)
    assert records
    for r in records:
        assert r.extra_metadata.get("chunker_backend") == BACKEND_LANGCHAIN
        assert r.extra_metadata.get("chunker_strategy") == STRATEGY_MARKDOWN_RECURSIVE_V1


# ---------------------------------------------------------------------------
# 6. get_chunking_backend factory
# ---------------------------------------------------------------------------

def test_factory_returns_langchain_by_default():
    backend = get_chunking_backend({})
    assert isinstance(backend, LangChainMarkdownRecursiveChunker)


def test_factory_returns_legacy_when_requested():
    backend = get_chunking_backend({"backend": "legacy"})
    assert isinstance(backend, LegacyChunkingBackend)


def test_factory_unknown_strategy_falls_back_to_legacy():
    backend = get_chunking_backend({"backend": "langchain", "strategy": "nonexistent_v99"})
    assert isinstance(backend, LegacyChunkingBackend)


def test_factory_semantic_v1_falls_back_to_markdown_recursive():
    backend = get_chunking_backend({"backend": "langchain", "strategy": STRATEGY_SEMANTIC_V1})
    assert isinstance(backend, LangChainMarkdownRecursiveChunker)


# ---------------------------------------------------------------------------
# 7. Image stripping (preprocessor)
# ---------------------------------------------------------------------------

def test_image_lines_stripped_before_chunking():
    """Lines starting with ![ must be removed during preprocessing."""
    md = (
        "# Section\n\n"
        "![Figure 1](fig1.png)\n"
        "Normal text that should survive. " * 10
    )
    chunker = LangChainMarkdownRecursiveChunker(_lc_cfg())
    records = chunker.chunk(md)
    for r in records:
        assert "![" not in r.text, f"Image markup leaked into chunk: {r.text!r}"


# ---------------------------------------------------------------------------
# 8. Empty / whitespace-only input
# ---------------------------------------------------------------------------

def test_empty_text_returns_no_chunks():
    chunker = LangChainMarkdownRecursiveChunker(_lc_cfg())
    assert chunker.chunk("") == []
    assert chunker.chunk("   \n\n  ") == []


def test_legacy_empty_text_returns_no_chunks():
    backend = LegacyChunkingBackend({"max_chars": 200, "overlap_chars": 20, "min_chunk_chars": 10})
    assert backend.chunk("") == []
