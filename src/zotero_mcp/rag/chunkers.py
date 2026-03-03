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
    STRATEGY_SEMANTIC_V1,
    DEFAULT_CHUNK_OVERLAP,
    DEFAULT_CHUNK_SIZE,
    DEFAULT_HEADERS,
    DEFAULT_MIN_CHUNK_CHARS,
    DEFAULT_SEPARATORS,
    ChunkRecord,
)
from .reference_parser import strip_and_extract_references

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
                        chunk_kind="content",
                        extra_metadata={
                            "chunker_backend": BACKEND_LEGACY,
                            "chunker_strategy": "legacy_v1",
                            "section_type": "body",
                            "chunk_char_len": len(text),
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
        self._chunk_cfg = chunk_cfg
        self._chunk_size = int(chunk_cfg.get("chunk_size", DEFAULT_CHUNK_SIZE))
        self._chunk_overlap = int(chunk_cfg.get("chunk_overlap", DEFAULT_CHUNK_OVERLAP))
        self._min_chunk_chars = int(chunk_cfg.get("min_chunk_chars", DEFAULT_MIN_CHUNK_CHARS))
        self._separators: list[str] = chunk_cfg.get("separators", DEFAULT_SEPARATORS)
        raw_headers: list[str] = chunk_cfg.get("headers", DEFAULT_HEADERS)
        self._merge_short_tail_chunks = bool(chunk_cfg.get("merge_short_tail_chunks", True))
        self._short_tail_merge_threshold = int(
            chunk_cfg.get("short_tail_merge_threshold", self._min_chunk_chars)
        )
        self._section_chunk_overrides = chunk_cfg.get("section_chunk_overrides", {}) or {}
        # MarkdownHeaderTextSplitter expects list of (marker, metadata_key) tuples
        self._headers_to_split_on = [(h, f"h{i + 1}") for i, h in enumerate(raw_headers)]

    @staticmethod
    def _normalize_section_title(title: str) -> str:
        normalized = re.sub(r"^\s*#+\s*", "", title or "")
        normalized = re.sub(r"\s+", " ", normalized).strip().lower()
        return normalized

    def _find_section_override(self, section_title: str) -> dict[str, int] | None:
        if not isinstance(self._section_chunk_overrides, dict) or not self._section_chunk_overrides:
            return None
        normalized_title = self._normalize_section_title(section_title)

        # Tier 1: exact normalized title
        for key, value in self._section_chunk_overrides.items():
            if not isinstance(value, dict):
                continue
            if self._normalize_section_title(str(key)) == normalized_title:
                return value

        # Tier 2: whole-word match only
        for key, value in self._section_chunk_overrides.items():
            if not isinstance(value, dict):
                continue
            escaped = re.escape(self._normalize_section_title(str(key)))
            if re.search(rf"(?i)\b{escaped}\b", normalized_title):
                return value
        return None

    def _merge_short_tail(self, chunks: list[str]) -> list[str]:
        if (
            not self._merge_short_tail_chunks
            or len(chunks) < 2
            or len(chunks[-1]) >= self._short_tail_merge_threshold
        ):
            return chunks
        merged = list(chunks[:-2])
        merged.append((chunks[-2].rstrip() + "\n" + chunks[-1].lstrip()).strip())
        return merged

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

        body_text, ref_map = strip_and_extract_references(preprocessed, self._chunk_cfg)
        if not body_text:
            body_text = preprocessed

        header_splitter = MarkdownHeaderTextSplitter(
            headers_to_split_on=self._headers_to_split_on,
            strip_headers=False,
        )

        try:
            section_docs = header_splitter.split_text(body_text)
        except Exception as exc:
            logger.warning("MarkdownHeaderTextSplitter failed (%s); treating as single section", exc)
            section_docs = []
            # Build a single pseudo-document
            class _Doc:
                page_content = body_text
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

            section_chunk_size = self._chunk_size
            section_chunk_overlap = self._chunk_overlap
            if section_override := self._find_section_override(section_title):
                if "chunk_size" in section_override:
                    section_chunk_size = int(section_override["chunk_size"])
                if "chunk_overlap" in section_override:
                    section_chunk_overlap = int(section_override["chunk_overlap"])

            recursive_splitter = RecursiveCharacterTextSplitter(
                chunk_size=section_chunk_size,
                chunk_overlap=section_chunk_overlap,
                separators=self._separators,
                length_function=len,
            )

            try:
                sub_chunks = recursive_splitter.split_text(section_text)
            except Exception as exc:
                logger.warning("RecursiveCharacterTextSplitter failed for section '%s': %s", section_title, exc)
                sub_chunks = [section_text] if len(section_text) >= self._min_chunk_chars else []

            sub_chunks = self._merge_short_tail(sub_chunks)
            for chunk_text in sub_chunks:
                if len(chunk_text) < self._min_chunk_chars:
                    continue
                records.append(
                    ChunkRecord(
                        text=chunk_text,
                        section_title=section_title,
                        chunk_index=idx,
                        chunk_kind="content",
                        extra_metadata={
                            "chunker_backend": BACKEND_LANGCHAIN,
                            "chunker_strategy": STRATEGY_MARKDOWN_RECURSIVE_V1,
                            "section_type": "body",
                            "chunk_char_len": len(chunk_text),
                        },
                    )
                )
                idx += 1

        for ref_num in sorted(ref_map):
            ref_text = ref_map[ref_num]
            if not ref_text:
                continue
            records.append(
                ChunkRecord(
                    text=ref_text,
                    section_title="references",
                    chunk_index=idx,
                    chunk_kind="references",
                    extra_metadata={
                        "chunker_backend": BACKEND_LANGCHAIN,
                        "chunker_strategy": STRATEGY_MARKDOWN_RECURSIVE_V1,
                        "section_type": "reference",
                        "chunk_char_len": len(ref_text),
                        "ref_num": ref_num,
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
    if strategy == STRATEGY_SEMANTIC_V1:
        logger.warning(
            "Chunking strategy %r is not implemented yet; falling back to %r.",
            STRATEGY_SEMANTIC_V1,
            STRATEGY_MARKDOWN_RECURSIVE_V1,
        )
        return LangChainMarkdownRecursiveChunker(chunk_cfg)

    # Unknown strategy → warn and fall back
    logger.warning(
        "Unknown chunking strategy %r; falling back to legacy backend.", strategy
    )
    return LegacyChunkingBackend(chunk_cfg)
