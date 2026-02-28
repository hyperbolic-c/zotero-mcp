#!/usr/bin/env python3
"""Offline evaluation for advanced_rag chunking strategies.

Compares two advanced_rag chunking configs against a JSONL query set and reports:
- Recall@5 / Recall@10
- MRR@10
- Average chunks per processed item
- Indexing duration (seconds)
- Query latency p95 (milliseconds)
- Top-N failure samples
"""

from __future__ import annotations

import argparse
import copy
import json
import statistics
import tempfile
import time
from pathlib import Path
from typing import Any

from zotero_mcp.cli import setup_zotero_environment
from zotero_mcp.semantic_search import create_semantic_search


def load_queries(path: Path) -> list[dict[str, str]]:
    records: list[dict[str, str]] = []
    with path.open("r", encoding="utf-8") as f:
        for lineno, line in enumerate(f, start=1):
            raw = line.strip()
            if not raw:
                continue
            payload = json.loads(raw)
            query = (payload.get("query") or "").strip()
            gold_item_key = (payload.get("gold_item_key") or "").strip()
            if not query or not gold_item_key:
                raise ValueError(f"Invalid query JSONL at line {lineno}: missing query/gold_item_key")
            records.append(
                {
                    "query": query,
                    "gold_item_key": gold_item_key,
                    "gold_section_hint": (payload.get("gold_section_hint") or "").strip(),
                }
            )
    if not records:
        raise ValueError("Query set is empty")
    return records


def _rank_of_gold(results: list[dict[str, Any]], gold_item_key: str, k: int) -> int | None:
    for idx, row in enumerate(results[:k], start=1):
        if row.get("item_key") == gold_item_key:
            return idx
    return None


def compute_metrics(rows: list[dict[str, Any]]) -> dict[str, float]:
    total = len(rows)
    if total == 0:
        return {"recall@5": 0.0, "recall@10": 0.0, "mrr@10": 0.0}

    hit5 = 0
    hit10 = 0
    mrr = 0.0
    for row in rows:
        r5 = _rank_of_gold(row["results"], row["gold_item_key"], 5)
        r10 = _rank_of_gold(row["results"], row["gold_item_key"], 10)
        if r5 is not None:
            hit5 += 1
        if r10 is not None:
            hit10 += 1
            mrr += 1.0 / r10

    return {
        "recall@5": hit5 / total,
        "recall@10": hit10 / total,
        "mrr@10": mrr / total,
    }


def _seconds_from_duration_str(duration: str | None) -> float:
    if not duration:
        return 0.0
    # format like H:MM:SS.microseconds or MM:SS.microseconds
    parts = str(duration).split(":")
    if len(parts) == 3:
        h = int(parts[0])
        m = int(parts[1])
        s = float(parts[2])
        return h * 3600 + m * 60 + s
    if len(parts) == 2:
        m = int(parts[0])
        s = float(parts[1])
        return m * 60 + s
    return float(parts[0])


def _with_chunk_cfg(base_cfg: dict[str, Any], chunk_cfg: dict[str, Any], persist_dir: Path, collection_name: str) -> dict[str, Any]:
    cfg = copy.deepcopy(base_cfg)
    semantic = cfg.setdefault("semantic_search", {})
    semantic["retriever_mode"] = "advanced_rag"
    semantic["persist_directory"] = str(persist_dir)
    semantic["collection_name"] = collection_name
    advanced = semantic.setdefault("advanced_rag", {})
    advanced["chunk"] = chunk_cfg
    return cfg


def evaluate_variant(
    name: str,
    config: dict[str, Any],
    queries: list[dict[str, str]],
    db_path: str | None,
    force_rebuild: bool,
    limit: int | None,
    top_n_failures: int,
) -> dict[str, Any]:
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as tmp:
        json.dump(config, tmp, ensure_ascii=False, indent=2)
        cfg_path = tmp.name

    setup_zotero_environment()
    search = create_semantic_search(cfg_path, db_path=db_path)

    ingest_stats = search.update_database(
        force_full_rebuild=force_rebuild,
        limit=limit,
        extract_fulltext=False,
    )

    query_rows: list[dict[str, Any]] = []
    query_latencies_ms: list[float] = []
    for q in queries:
        start = time.perf_counter()
        result = search.search(query=q["query"], limit=10)
        query_latencies_ms.append((time.perf_counter() - start) * 1000.0)
        query_rows.append(
            {
                "query": q["query"],
                "gold_item_key": q["gold_item_key"],
                "results": result.get("results", []),
            }
        )

    metrics = compute_metrics(query_rows)
    failures = [
        {
            "query": row["query"],
            "gold_item_key": row["gold_item_key"],
            "top_item_key": (row["results"][0].get("item_key") if row["results"] else ""),
        }
        for row in query_rows
        if _rank_of_gold(row["results"], row["gold_item_key"], 10) is None
    ][:top_n_failures]

    processed_items = int(ingest_stats.get("processed_items", 0) or 0)
    added_chunks = int(ingest_stats.get("added_chunks", 0) or 0)
    updated_chunks = int(ingest_stats.get("updated_chunks", 0) or 0)
    total_chunks = added_chunks + updated_chunks

    return {
        "name": name,
        "metrics": metrics,
        "avg_chunks_per_item": (total_chunks / processed_items) if processed_items > 0 else 0.0,
        "index_seconds": _seconds_from_duration_str(ingest_stats.get("duration")),
        "query_p95_ms": statistics.quantiles(query_latencies_ms, n=20)[-1] if len(query_latencies_ms) >= 2 else (query_latencies_ms[0] if query_latencies_ms else 0.0),
        "failures": failures,
        "ingest_stats": ingest_stats,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate advanced_rag chunking variants with an offline query set")
    parser.add_argument("--base-config", type=Path, required=True, help="Path to config JSON")
    parser.add_argument("--queries", type=Path, required=True, help="Query set JSONL")
    parser.add_argument("--persist-dir", type=Path, required=True, help="Directory for vector DB")
    parser.add_argument("--db-path", type=str, default=None, help="Optional zotero.sqlite path override")
    parser.add_argument("--limit", type=int, default=None, help="Optional indexing item limit")
    parser.add_argument("--top-n-failures", type=int, default=20)
    parser.add_argument("--no-force-rebuild", action="store_true", help="Disable force rebuild during ingest")
    args = parser.parse_args()

    base_cfg = json.loads(args.base_config.read_text(encoding="utf-8"))
    queries = load_queries(args.queries)

    args.persist_dir.mkdir(parents=True, exist_ok=True)
    baseline_cfg = _with_chunk_cfg(
        base_cfg,
        {"backend": "legacy", "max_chars": 1600, "overlap_chars": 200, "min_chunk_chars": 120},
        persist_dir=args.persist_dir,
        collection_name="zotero_rag_eval_legacy",
    )
    lc_cfg = _with_chunk_cfg(
        base_cfg,
        {
            "backend": "langchain",
            "strategy": "markdown_recursive_v1",
            "chunk_size": 1100,
            "chunk_overlap": 180,
            "min_chunk_chars": 220,
            "separators": ["\n\n", "\n", ". ", "; ", ", ", " "],
            "headers": ["#", "##", "###", "####"],
        },
        persist_dir=args.persist_dir,
        collection_name="zotero_rag_eval_langchain",
    )

    force_rebuild = not args.no_force_rebuild
    baseline = evaluate_variant(
        "legacy",
        baseline_cfg,
        queries,
        db_path=args.db_path,
        force_rebuild=force_rebuild,
        limit=args.limit,
        top_n_failures=args.top_n_failures,
    )
    candidate = evaluate_variant(
        "langchain(markdown_recursive_v1)",
        lc_cfg,
        queries,
        db_path=args.db_path,
        force_rebuild=force_rebuild,
        limit=args.limit,
        top_n_failures=args.top_n_failures,
    )

    report = {
        "baseline": baseline,
        "candidate": candidate,
        "delta": {
            "recall@10_rel": (
                (candidate["metrics"]["recall@10"] - baseline["metrics"]["recall@10"])
                / baseline["metrics"]["recall@10"]
                if baseline["metrics"]["recall@10"] > 0
                else None
            ),
            "mrr@10_rel": (
                (candidate["metrics"]["mrr@10"] - baseline["metrics"]["mrr@10"])
                / baseline["metrics"]["mrr@10"]
                if baseline["metrics"]["mrr@10"] > 0
                else None
            ),
            "index_seconds_rel": (
                (candidate["index_seconds"] - baseline["index_seconds"]) / baseline["index_seconds"]
                if baseline["index_seconds"] > 0
                else None
            ),
            "query_p95_ms_rel": (
                (candidate["query_p95_ms"] - baseline["query_p95_ms"]) / baseline["query_p95_ms"]
                if baseline["query_p95_ms"] > 0
                else None
            ),
        },
    }

    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
