#!/usr/bin/env python3
"""Smoke test for advanced_rag vectorization and reranking pipeline.

This script creates a temporary semantic_search config, runs ingest, and executes
queries against the advanced_rag retriever to validate end-to-end behavior.
"""

from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path
from typing import Any

from zotero_mcp.cli import setup_zotero_environment
from zotero_mcp.semantic_search import create_semantic_search


def _build_config(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "semantic_search": {
            "retriever_mode": "advanced_rag",
            "collection_name": args.collection_name,
            "persist_directory": str(args.persist_directory),
            "embedding_model": args.embedding_model,
            "advanced_rag": {
                "md_root": str(args.md_root),
                "chunk": {
                    "max_chars": args.max_chars,
                    "overlap_chars": args.overlap_chars,
                    "heading_first": True,
                    "min_chunk_chars": args.min_chunk_chars,
                },
                "ingest": {
                    "strip_images": not args.keep_images,
                },
                "reranker": {
                    "enabled": not args.disable_reranker,
                    "backend": "flashrank",
                    "model_name": args.reranker_model,
                    "local_model_path": args.reranker_cache,
                    "top_n": args.reranker_top_n,
                },
                "retrieve": {
                    "candidate_k": args.candidate_k,
                    "evidence_per_item": args.evidence_per_item,
                    "meta_weight": args.meta_weight,
                },
            },
        }
    }


def _print_json(title: str, payload: dict[str, Any]) -> None:
    print(f"\n=== {title} ===")
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def _print_search_results(query: str, result: dict[str, Any]) -> None:
    print(f"\n=== Search: {query} ===")
    print(f"total_found: {result.get('total_found', 0)}")
    for idx, item in enumerate(result.get("results", []), start=1):
        zotero_item = item.get("zotero_item", {}) or {}
        title = (zotero_item.get("data", {}) or {}).get("title", "")
        print(f"{idx}. {item.get('item_key', '')}  score={item.get('similarity_score', 0):.4f}  title={title}")
        for ev in item.get("evidence", [])[:2]:
            snippet = (ev.get("text") or "").replace("\n", " ")[:160]
            print(
                "   - "
                f"kind={ev.get('chunk_kind', '')} "
                f"att={ev.get('attachment_key', '')} "
                f"sec={ev.get('section_title', '')} "
                f"score={ev.get('score', 0):.4f} "
                f"text={snippet}"
            )


def main() -> int:
    parser = argparse.ArgumentParser(description="Advanced RAG smoke test for vectorization and reranking")
    parser.add_argument("--md-root", type=Path, default=Path("/Users/liam/.config/zotero-mcp/md_root"))
    parser.add_argument("--db-path", type=str, default=None, help="Optional zotero.sqlite path override")
    parser.add_argument("--persist-directory", type=Path, default=Path("/tmp/zotero-mcp-rag-smoke-db"))
    parser.add_argument("--collection-name", type=str, default="zotero_rag_chunks_release_smoke")
    parser.add_argument("--embedding-model", type=str, default="default")
    parser.add_argument("--limit", type=int, default=200)
    parser.add_argument("--force-rebuild", action="store_true")

    parser.add_argument("--max-chars", type=int, default=1600)
    parser.add_argument("--overlap-chars", type=int, default=200)
    parser.add_argument("--min-chunk-chars", type=int, default=120)
    parser.add_argument("--keep-images", action="store_true")

    parser.add_argument("--disable-reranker", action="store_true")
    parser.add_argument("--reranker-model", type=str, default="ms-marco-MiniLM-L-12-v2")
    parser.add_argument("--reranker-cache", type=str, default=None)
    parser.add_argument("--reranker-top-n", type=int, default=8)

    parser.add_argument("--candidate-k", type=int, default=30)
    parser.add_argument("--evidence-per-item", type=int, default=2)
    parser.add_argument("--meta-weight", type=float, default=0.85)

    parser.add_argument(
        "--query",
        action="append",
        default=[],
        help="Repeat this argument to run multiple queries",
    )

    args = parser.parse_args()

    if not args.md_root.exists():
        print(f"Error: md_root does not exist: {args.md_root}")
        return 2

    args.persist_directory.mkdir(parents=True, exist_ok=True)

    config = _build_config(args)
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as tmp:
        json.dump(config, tmp, ensure_ascii=False, indent=2)
        config_path = Path(tmp.name)

    print(f"Using temporary config: {config_path}")
    print(f"md_root: {args.md_root}")
    print(f"persist_directory: {args.persist_directory}")
    print(f"collection_name: {args.collection_name}")

    # Align runtime behavior with CLI commands (`update-db`, `db-status`).
    setup_zotero_environment()

    search = create_semantic_search(str(config_path), db_path=args.db_path)

    status_before = search.get_database_status()
    _print_json("Status Before", status_before)

    ingest_stats = search.update_database(
        force_full_rebuild=args.force_rebuild,
        limit=args.limit,
        extract_fulltext=False,
    )
    _print_json("Ingest Stats", ingest_stats)

    status_after = search.get_database_status()
    _print_json("Status After", status_after)

    queries = args.query or [
        "deep learning methods",
        "retrieval augmented generation",
    ]
    for q in queries:
        result = search.search(query=q, limit=5)
        _print_search_results(q, result)

    print("\nSmoke test completed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
