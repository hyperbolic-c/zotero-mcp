from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_eval_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "eval_advanced_rag.py"
    spec = importlib.util.spec_from_file_location("eval_advanced_rag", script_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_load_queries_parses_jsonl(tmp_path):
    mod = _load_eval_module()
    qpath = tmp_path / "queries.jsonl"
    qpath.write_text(
        "\n".join(
            [
                '{"query":"rag methods","gold_item_key":"I1"}',
                '{"query":"chunking strategy","gold_item_key":"I2","gold_section_hint":"Methods"}',
            ]
        ),
        encoding="utf-8",
    )

    rows = mod.load_queries(qpath)
    assert len(rows) == 2
    assert rows[0]["query"] == "rag methods"
    assert rows[1]["gold_section_hint"] == "Methods"


def test_compute_metrics_recall_and_mrr():
    mod = _load_eval_module()
    rows = [
        {
            "query": "q1",
            "gold_item_key": "I1",
            "results": [{"item_key": "I1"}, {"item_key": "I9"}],
        },
        {
            "query": "q2",
            "gold_item_key": "I2",
            "results": [{"item_key": "I9"}, {"item_key": "I2"}],
        },
        {
            "query": "q3",
            "gold_item_key": "I3",
            "results": [{"item_key": "I9"}],
        },
    ]

    metrics = mod.compute_metrics(rows)
    assert metrics["recall@5"] == 2 / 3
    assert metrics["recall@10"] == 2 / 3
    assert metrics["mrr@10"] == (1.0 + 0.5 + 0.0) / 3


def test_seconds_from_duration_str():
    mod = _load_eval_module()
    assert mod._seconds_from_duration_str("0:00:01.500000") == 1.5
    assert mod._seconds_from_duration_str("2:03.000000") == 123.0
