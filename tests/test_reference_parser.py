from __future__ import annotations

import logging

from zotero_mcp.retrievers.reference_parser import (
    extract_numeric_citation_ids,
    find_reference_block_cutpoint,
    strip_and_extract_references,
)


def _cfg(**overrides):
    cfg = {
        "exclude_sections_enabled": True,
        "detect_reference_block_without_heading": True,
        "reference_block_tail_ratio": 0.35,
        "reference_block_window_lines": 20,
        "reference_block_min_density": 0.45,
        "reference_block_min_hits": 8,
        "reference_block_min_doc_chars": 3000,
    }
    cfg.update(overrides)
    return cfg


def test_heading_references_detection_case_insensitive():
    lines = ["# Intro", "Body", "## REFERENCES", "[1] Foo"]
    idx = find_reference_block_cutpoint(lines, _cfg(reference_block_min_doc_chars=1))
    assert idx == 2


def test_headless_dual_gate_detection():
    body = ["intro line " + ("x" * 80) for _ in range(40)]
    refs = [f"[{i}] Smith et al. doi:10.1000/{i}" for i in range(1, 13)]
    lines = body + refs
    idx = find_reference_block_cutpoint(lines, _cfg(reference_block_min_doc_chars=100))
    assert idx is not None
    assert idx >= len(body)


def test_strip_and_extract_references_by_numeric_prefix():
    md = """
# Intro
Body text.

## References
[1] First reference.
[2] Second reference line one.
continued line.
""".strip()
    body, ref_map = strip_and_extract_references(md, _cfg(reference_block_min_doc_chars=1))
    assert "References" not in body
    assert ref_map[1] == "First reference."
    assert "continued line." in ref_map[2]


def test_extract_numeric_citation_ids_all_patterns():
    text = "as shown in [1], [2,4], [3-5], [1,3-5,7]"
    assert extract_numeric_citation_ids(text) == [1, 2, 3, 4, 5, 7]


def test_extract_numeric_citation_skips_large_range_with_debug_log(caplog):
    with caplog.at_level(logging.DEBUG):
        ids = extract_numeric_citation_ids("bad [1-100] good [4]")
    assert ids == [4]
    assert any("Skipping oversized citation range" in r.message for r in caplog.records)


def test_headless_dual_gate_detection_abbreviated_journal_no_doi():
    """Dual-gate must fire for biology-style refs with abbreviated journal names but no DOI.

    This specifically tests that _BIB_TOKEN_RE matches 'Nat.', 'Adv.', 'et al.'
    even without a trailing word boundary after the period.
    """
    body = ["intro line " + ("x" * 80) for _ in range(40)]
    refs = [
        f"[{i}] Author et al. Nat. Commun. vol. {i}, pp. 100-110 (2023)"
        for i in range(1, 13)
    ]
    lines = body + refs
    idx = find_reference_block_cutpoint(lines, _cfg(reference_block_min_doc_chars=100))
    assert idx is not None, (
        "Dual-gate should detect headless reference block with abbreviated journal names (no doi:)"
    )
    assert idx >= len(body)


def test_headless_dual_gate_detection_adv_mater_style():
    """Dual-gate must detect Adv. Mater. style references (chemistry, no DOI)."""
    body = ["intro line " + ("x" * 80) for _ in range(40)]
    refs = [
        f"[{i}] Smith, J. Adv. Mater. {2010 + i}, 22, {100 + i}."
        for i in range(1, 13)
    ]
    lines = body + refs
    idx = find_reference_block_cutpoint(lines, _cfg(reference_block_min_doc_chars=100))
    assert idx is not None, (
        "Dual-gate should detect headless reference block with 'Adv.' journal abbreviations"
    )
    assert idx >= len(body)


def test_min_doc_length_guard_only_affects_headless_heuristic():
    lines = ["# Intro", "Body", "## References", "[1] Foo"]
    assert find_reference_block_cutpoint(lines, _cfg(reference_block_min_doc_chars=99999)) == 2

    headless = ["body"] * 50 + ["[1] et al. doi:10"] * 10
    assert find_reference_block_cutpoint(headless, _cfg(reference_block_min_doc_chars=99999)) is None
