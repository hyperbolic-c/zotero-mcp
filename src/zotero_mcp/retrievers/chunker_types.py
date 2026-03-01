from __future__ import annotations

from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Strategy constants
# ---------------------------------------------------------------------------
BACKEND_LEGACY = "legacy"
BACKEND_LANGCHAIN = "langchain"

STRATEGY_MARKDOWN_RECURSIVE_V1 = "markdown_recursive_v1"
STRATEGY_SEMANTIC_V1 = "semantic_v1"

# ---------------------------------------------------------------------------
# Default chunking parameters (English academic paper optimised)
# ---------------------------------------------------------------------------
DEFAULT_CHUNK_SIZE = 1100
DEFAULT_CHUNK_OVERLAP = 180
DEFAULT_MIN_CHUNK_CHARS = 220
DEFAULT_SEPARATORS = ["\n\n", "\n", ". ", "; ", ", ", " "]
DEFAULT_HEADERS = ["#", "##", "###", "####"]


@dataclass
class ChunkRecord:
    """A single chunk produced by a ChunkingBackend."""

    text: str
    section_title: str = ""
    chunk_index: int = 0
    char_start: int = 0
    char_end: int = 0
    chunk_kind: str = "content"
    extra_metadata: dict = field(default_factory=dict)
