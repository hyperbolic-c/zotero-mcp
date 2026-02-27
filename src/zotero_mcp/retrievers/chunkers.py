from __future__ import annotations

"""Chunking strategy implementations for AdvancedRAGRetriever.

This module exposes a ChunkingBackend ABC and concrete implementations:

- LegacyChunkingBackend  – wraps the original _split_sections + _chunk_text logic
- LangChainMarkdownRecursiveChunker – uses MarkdownHeaderTextSplitter +
  RecursiveCharacterTextSplitter (strategy: markdown_recursive_v1)

Public factory:  ``get_chunking_backend(chunk_cfg)``
"""

import logging
import re
from abc import ABC, abstractmethod
from typing import Any

from .chunker_types import (
    BACKEND_LANGCHAIN,
    BACKEND_LEGACY,
    STRATEGY_MARKDOWN_RECURSIVE_V1,
    DEFAULT_CHUNK_OVERLAP,
    DEFAULT_CHUNK_SIZE,
    DEFAULT_HEADERS,
    DEFAULT_MIN_CHUNK_CHARS,
    DEFAULT_SEPARATORS,
    ChunkRecord,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------


class ChunkingBackend(ABC):
    """Abstract chunking strategy.

    Each backend takes a block of merged markdown text and produces a list of
    :class:`ChunkRecord` instances.  The caller is responsible for assigning
    attachment-level metadata (attachment_key, item_key, etc.).
    """

    @abstractmethod
    def chunk(self, merged_markdown_text: str) -> list[ChunkRecord]:
        """Split *merged_markdown_text* into chunks."""


# ---------------------------------------------------------------------------
# Legacy backend (original character-sliding-window approach)
# ---------------------------------------------------------------------------


class LegacyChunkingBackend(ChunkingBackend):
    """Replicates the original _split_sections + _chunk_text logic."""

    def __init__(self, chunk_cfg: dict[str, Any]) -> None:
        self._max_chars = int(chunk_cfg.get("max_chars", chunk_cfg.get("chunk_size", 1600)))
        self._overlap_chars = int(chunk_cfg.get("overlap_chars", chunk_cfg.get("chunk_overlap", 200)))
        self._min_chunk_chars = int(chunk_cfg.get("min_chunk_chars", 120))

    # ------------------------------------------------------------------
    # Internal helpers (mirrored from AdvancedRAGRetriever)
    # ------------------------------------------------------------------

    def _split_sections(self, text: str) -> list[tuple[str, str]]:
        sections: list[tuple[str, str]] = []
        current_title = ""
        current_lines: list[str] = []
        for line in text.splitlines():
            if re.match(r"^#{1,3}\s+", line.strip()):
                if current_lines:
                    sections.append((current_title, "\n".join(current_lines).strip()))
                    current_lines = []
                current_title = re.sub(r"^#{1,3}\s+", "", line.strip())
                continue
            current_lines.append(line)
        if current_lines:
            sections.append((current_title, "\n".join(current_lines).strip()))
        return [sec for sec in sections if sec[1]]

    def _chunk_text(self, text: str) -> list[str]:
        if len(text) <= self._max_chars:
            return [text] if len(text) >= self._min_chunk_chars else []
        chunks: list[str] = []
        start = 0
        text_len = len(text)
        while start < text_len:
            end = min(start + self._max_chars, text_len)
            chunk = text[start:end].strip()
            if len(chunk) >= self._min_chunk_chars:
                chunks.append(chunk)
            if end >= text_len:
                break
            start = max(0, end - self._overlap_chars)
        return chunks

    # ------------------------------------------------------------------

    def chunk(self, merged_markdown_text: str) -> list[ChunkRecord]:
        records: list[ChunkRecord] = []
        idx = 0
        for section_title, section_text in self._split_sections(merged_markdown_text):
            for text in self._chunk_text(section_text):
                records.append(
                    ChunkRecord(
                        text=text,
                        section_title=section_title,
                        chunk_index=idx,
                        extra_metadata={
                            "chunker_backend": BACKEND_LEGACY,
                            "chunker_strategy": "legacy_v1",
                        },
                    )
                )
                idx += 1
        return records


# ---------------------------------------------------------------------------
# LangChain markdown_recursive_v1 backend
# ---------------------------------------------------------------------------


def _preprocess_markdown(text: str) -> str:
    """Strip image lines, normalise newlines, collapse 3+ blank lines to 2."""
    # Remove image lines
    lines = [line for line in text.splitlines() if not line.strip().startswith("![")]
    text = "\n".join(lines)
    # Collapse 3+ consecutive blank lines → 2
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


class LangChainMarkdownRecursiveChunker(ChunkingBackend):
    """strategy: markdown_recursive_v1

    Pipeline:
      1. Preprocess (strip images, normalise whitespace)
      2. MarkdownHeaderTextSplitter → per-section Documents
      3. RecursiveCharacterTextSplitter on each section
      4. Filter chunks < min_chunk_chars
    """

    def __init__(self, chunk_cfg: dict[str, Any]) -> None:
        self._chunk_size = int(chunk_cfg.get("chunk_size", DEFAULT_CHUNK_SIZE))
        self._chunk_overlap = int(chunk_cfg.get("chunk_overlap", DEFAULT_CHUNK_OVERLAP))
        self._min_chunk_chars = int(chunk_cfg.get("min_chunk_chars", DEFAULT_MIN_CHUNK_CHARS))
        self._separators: list[str] = chunk_cfg.get("separators", DEFAULT_SEPARATORS)
        raw_headers: list[str] = chunk_cfg.get("headers", DEFAULT_HEADERS)
        # MarkdownHeaderTextSplitter expects list of (marker, metadata_key) tuples
        self._headers_to_split_on = [(h, f"h{i + 1}") for i, h in enumerate(raw_headers)]

    def chunk(self, merged_markdown_text: str) -> list[ChunkRecord]:
        try:
            from langchain_text_splitters import MarkdownHeaderTextSplitter, RecursiveCharacterTextSplitter
        except ImportError:
            logger.warning(
                "langchain-text-splitters is not installed; falling back to legacy chunker. "
                "Install with: pip install langchain-text-splitters>=0.3"
            )
            return LegacyChunkingBackend({
                "chunk_size": self._chunk_size,
                "chunk_overlap": self._chunk_overlap,
                "min_chunk_chars": self._min_chunk_chars,
            }).chunk(merged_markdown_text)

        preprocessed = _preprocess_markdown(merged_markdown_text)
        if not preprocessed:
            return []

        header_splitter = MarkdownHeaderTextSplitter(
            headers_to_split_on=self._headers_to_split_on,
            strip_headers=False,
        )
        recursive_splitter = RecursiveCharacterTextSplitter(
            chunk_size=self._chunk_size,
            chunk_overlap=self._chunk_overlap,
            separators=self._separators,
            length_function=len,
        )

        try:
            section_docs = header_splitter.split_text(preprocessed)
        except Exception as exc:
            logger.warning("MarkdownHeaderTextSplitter failed (%s); treating as single section", exc)
            section_docs = []
            # Build a single pseudo-document
            class _Doc:
                page_content = preprocessed
                metadata: dict = {}
            section_docs = [_Doc()]

        records: list[ChunkRecord] = []
        idx = 0
        for section_doc in section_docs:
            section_text: str = section_doc.page_content
            # Derive section title from header metadata (prefer deepest heading)
            header_meta: dict[str, str] = getattr(section_doc, "metadata", {}) or {}
            # Headers are stored as h1, h2, h3, h4 – take last non-empty
            section_title = ""
            for key in ["h1", "h2", "h3", "h4"]:
                if val := header_meta.get(key, ""):
                    section_title = val

            try:
                sub_chunks = recursive_splitter.split_text(section_text)
            except Exception as exc:
                logger.warning("RecursiveCharacterTextSplitter failed for section '%s': %s", section_title, exc)
                sub_chunks = [section_text] if len(section_text) >= self._min_chunk_chars else []

            for chunk_text in sub_chunks:
                if len(chunk_text) < self._min_chunk_chars:
                    continue
                records.append(
                    ChunkRecord(
                        text=chunk_text,
                        section_title=section_title,
                        chunk_index=idx,
                        extra_metadata={
                            "chunker_backend": BACKEND_LANGCHAIN,
                            "chunker_strategy": STRATEGY_MARKDOWN_RECURSIVE_V1,
                        },
                    )
                )
                idx += 1

        return records


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def get_chunking_backend(chunk_cfg: dict[str, Any]) -> ChunkingBackend:
    """Return the appropriate ChunkingBackend based on *chunk_cfg*.

    Config keys read:
      - ``backend``: ``"langchain"`` (default) or ``"legacy"``
      - ``strategy``: ``"markdown_recursive_v1"`` (default)

    Backward-compat: if ``max_chars``/``overlap_chars`` exist and new fields
    are absent, they are passed through transparently.
    """
    backend_name = chunk_cfg.get("backend", BACKEND_LANGCHAIN)
    if backend_name == BACKEND_LEGACY:
        return LegacyChunkingBackend(chunk_cfg)

    strategy = chunk_cfg.get("strategy", STRATEGY_MARKDOWN_RECURSIVE_V1)
    if strategy == STRATEGY_MARKDOWN_RECURSIVE_V1:
        return LangChainMarkdownRecursiveChunker(chunk_cfg)

    # Unknown strategy → warn and fall back
    logger.warning(
        "Unknown chunking strategy %r; falling back to legacy backend.", strategy
    )
    return LegacyChunkingBackend(chunk_cfg)
