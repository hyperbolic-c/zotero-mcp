from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

_HEADING_LINE_RE = re.compile(r"^\s*#{1,4}\s*(.+?)\s*$")
_LIST_LIKE_RE = re.compile(r"^\s*(\[\d+\]|\d+\.\s+)")
_BIB_TOKEN_RE = re.compile(
    r"(doi:|arxiv:|\bvol\.|\bpp\.|\bet al\.|\bnat\.|\badv\.|\bacs\b|\bieee\b)",
    re.IGNORECASE,
)
_REF_ITEM_RE = re.compile(r"^\s*(?:\[(\d+)\]|(\d+)\.)\s*(.+?)\s*$")
_CITATION_BRACKET_RE = re.compile(r"\[(\d+(?:\s*[-,]\s*\d+)*)\]")


def _cfg(cfg: dict[str, Any], key: str, default: Any) -> Any:
    return cfg.get(key, default) if isinstance(cfg, dict) else default


def _is_excluded_heading(line: str, cfg: dict[str, Any]) -> bool:
    match = _HEADING_LINE_RE.match(line or "")
    if not match:
        return False
    heading_text = re.sub(r"\s+", " ", match.group(1).strip().lower())
    configured = [str(v).strip().lower() for v in _cfg(cfg, "exclude_sections", ["references"]) or []]
    candidates = set(configured + ["references", "bibliography", "works cited", "参考文献"])
    return heading_text in candidates


def find_reference_block_cutpoint(lines: list[str], cfg: dict[str, Any]) -> int | None:
    """Return the line index where references begin, or None."""
    if not lines:
        return None

    # Stage 1: exact heading match.
    for idx, line in enumerate(lines):
        if _is_excluded_heading(line, cfg):
            return idx

    if not bool(_cfg(cfg, "detect_reference_block_without_heading", True)):
        return None

    joined = "\n".join(lines)
    min_doc_chars = int(_cfg(cfg, "reference_block_min_doc_chars", 3000))
    if len(joined) < min_doc_chars:
        return None

    # Stage 2: tail-only dual-gate heuristic.
    tail_ratio = float(_cfg(cfg, "reference_block_tail_ratio", 0.35))
    total = len(lines)
    tail_start = max(0, int(total * (1.0 - tail_ratio)))
    tail_lines = lines[tail_start:]

    window_lines = max(1, int(_cfg(cfg, "reference_block_window_lines", 20)))
    min_density = float(_cfg(cfg, "reference_block_min_density", 0.45))
    min_hits = int(_cfg(cfg, "reference_block_min_hits", 8))

    best_start: int | None = None
    for i in range(0, len(tail_lines)):
        window = tail_lines[i : i + window_lines]
        if not window:
            continue
        list_like_idx = [j for j, line in enumerate(window) if _LIST_LIKE_RE.match(line)]
        list_like_hits = [window[j] for j in list_like_idx]
        if len(list_like_hits) < min_hits:
            continue
        density = len(list_like_hits) / len(window)
        if density < min_density:
            continue

        bib_hits = sum(1 for line in list_like_hits if _BIB_TOKEN_RE.search(line))
        bib_ratio = bib_hits / max(1, len(list_like_hits))
        if bib_ratio >= min_density:
            best_start = tail_start + i + list_like_idx[0]
            break

    return best_start


def strip_and_extract_references(markdown_text: str, cfg: dict[str, Any]) -> tuple[str, dict[int, str]]:
    """Split markdown into body text and numbered references map."""
    lines = markdown_text.splitlines()

    if not bool(_cfg(cfg, "exclude_sections_enabled", True)):
        return markdown_text.strip(), {}

    cutpoint = find_reference_block_cutpoint(lines, cfg)
    if cutpoint is None:
        return markdown_text.strip(), {}

    body_text = "\n".join(lines[:cutpoint]).strip()
    start_idx = cutpoint + 1 if _is_excluded_heading(lines[cutpoint] if cutpoint < len(lines) else "", cfg) else cutpoint
    ref_text = "\n".join(lines[start_idx:]).strip()
    ref_map: dict[int, str] = {}

    current_num: int | None = None
    current_lines: list[str] = []

    def _flush() -> None:
        nonlocal current_num, current_lines
        if current_num is None:
            current_lines = []
            return
        text = " ".join(part.strip() for part in current_lines if part.strip()).strip()
        if text:
            ref_map[current_num] = text
        current_lines = []

    for line in ref_text.splitlines():
        match = _REF_ITEM_RE.match(line)
        if match:
            _flush()
            num = int(match.group(1) or match.group(2))
            current_num = num
            current_lines = [match.group(3).strip()]
            continue

        if current_num is not None:
            if line.strip():
                current_lines.append(line.strip())
            else:
                _flush()
                current_num = None

    _flush()
    return body_text, ref_map


def extract_numeric_citation_ids(text: str) -> list[int]:
    """Extract unique numeric citation ids from bracket citations."""
    found: set[int] = set()

    for bracket in _CITATION_BRACKET_RE.findall(text or ""):
        parts = [p.strip() for p in bracket.split(",") if p.strip()]
        for part in parts:
            if "-" in part:
                bounds = [b.strip() for b in part.split("-", 1)]
                if len(bounds) != 2 or not bounds[0].isdigit() or not bounds[1].isdigit():
                    continue
                start = int(bounds[0])
                end = int(bounds[1])
                if end < start:
                    start, end = end, start
                span = end - start + 1
                if span > 30:
                    logger.debug("Skipping oversized citation range [%s] (span=%d)", part, span)
                    continue
                for n in range(start, end + 1):
                    found.add(n)
                continue

            if part.isdigit():
                found.add(int(part))

    return sorted(found)
