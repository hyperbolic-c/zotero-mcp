"""
Semantic search functionality for Zotero MCP.

This module provides semantic search capabilities by integrating ChromaDB
with the existing Zotero client to enable vector-based similarity search
over research libraries.
"""

import json
import os
import sys
import logging
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from .chroma_client import ChromaClient, create_chroma_client
from .client import get_zotero_client
from .utils import format_creators, is_local_mode
from .local_db import LocalZoteroReader
from .retrievers.factory import create_retriever
from .indexer import ZoteroIndexer

logger = logging.getLogger(__name__)


@contextmanager
def suppress_stdout():
    """Context manager to suppress stdout temporarily."""
    with open(os.devnull, 'w') as devnull:
        old_stdout = sys.stdout
        sys.stdout = devnull
        try:
            yield
        finally:
            sys.stdout = old_stdout


class ZoteroSemanticSearch:
    """Semantic search interface for Zotero libraries using ChromaDB."""

    def __init__(self,
                 chroma_client: ChromaClient | None = None,
                 config_path: str | None = None,
                 db_path: str | None = None):
        """
        Initialize semantic search.

        Args:
            chroma_client: Optional ChromaClient instance
            config_path: Path to configuration file
            db_path: Optional path to Zotero database (overrides config file)
        """
        self.config_path = config_path
        self.db_path = db_path  # CLI override for Zotero database path
        self.zotero_client = get_zotero_client()
        self.semantic_config = self._load_semantic_config()
        self.retriever_mode = self.semantic_config.get("retriever_mode", "legacy_metadata")
        self.chroma_client = chroma_client
        
        # Initialize indexer for update management
        self.indexer = ZoteroIndexer(
            retriever_mode=self.retriever_mode,
            config_path=self.config_path,
            semantic_config=self.semantic_config,
            chroma_client=self.chroma_client,
            zotero_client=self.zotero_client
        )
        
        # Pull state from indexer for convenience and backward compatibility
        self.update_config = self.indexer.update_config
        self.retriever = self.indexer.retriever
        
        if self.chroma_client is None:
            self.chroma_client = self.indexer.chroma_client

    @staticmethod
    def _get_advanced_rag_defaults() -> dict[str, Any]:
        """Return the in-code defaults for the advanced_rag config section."""
        return {
            "md_root": "",
            "chunk": {
                # --- new LangChain-backed fields (M1) ---
                "backend": "langchain",
                "strategy": "markdown_recursive_v1",
                "chunk_size": 1100,
                "chunk_overlap": 180,
                "min_chunk_chars": 220,
                "separators": ["\n\n", "\n", ". ", "; ", ", ", " "],
                "headers": ["#", "##", "###", "####"],
                "exclude_sections_enabled": True,
                "exclude_sections": ["references", "acknowledgments", "appendix", "supplementary"],
                "detect_reference_block_without_heading": True,
                "reference_block_tail_ratio": 0.35,
                "reference_block_window_lines": 20,
                "reference_block_min_density": 0.45,
                "reference_block_min_hits": 8,
                "reference_block_min_doc_chars": 3000,
                "merge_short_tail_chunks": True,
                "short_tail_merge_threshold": 220,
                "section_chunk_overrides": {},
                # --- legacy fields kept for backward compat ---
                "max_chars": 1600,
                "overlap_chars": 200,
                "heading_first": True,
            },
            "ingest": {"strip_images": True},
            "reranker": {
                "enabled": True,
                "backend": "flashrank",
                "model_name": "ms-marco-MiniLM-L-12-v2",
                "local_model_path": None,
                "top_n": 8,
            },
            "retrieve": {"candidate_k": 30, "evidence_per_item": 2, "meta_weight": 0.70},
        }

    def _load_semantic_config(self) -> dict[str, Any]:
        """Load semantic search configuration with backward-compatible defaults."""
        config: dict[str, Any] = {
            "retriever_mode": "legacy_metadata",
            "advanced_rag": self._get_advanced_rag_defaults(),
        }
        if self.config_path and os.path.exists(self.config_path):
            try:
                with open(self.config_path) as f:
                    file_config = json.load(f).get("semantic_search", {})
                    config.update(file_config)
                    if "advanced_rag" in file_config:
                        advanced_cfg = config["advanced_rag"]
                        file_advanced = file_config.get("advanced_rag", {})
                        for key in ("chunk", "ingest", "reranker", "retrieve"):
                            if key in file_advanced and isinstance(file_advanced[key], dict):
                                advanced_cfg[key].update(file_advanced[key])
                        for key in ("md_root",):
                            if key in file_advanced:
                                advanced_cfg[key] = file_advanced[key]
            except Exception as e:
                logger.warning(f"Error loading semantic config: {e}")

        # Backward-compat: map old max_chars/overlap_chars → chunk_size/chunk_overlap
        chunk_cfg: dict[str, Any] = config.get("advanced_rag", {}).get("chunk", {})
        migrated = False
        if "max_chars" in chunk_cfg and "chunk_size" not in chunk_cfg:
            chunk_cfg["chunk_size"] = chunk_cfg["max_chars"]
            migrated = True
        if "overlap_chars" in chunk_cfg and "chunk_overlap" not in chunk_cfg:
            chunk_cfg["chunk_overlap"] = chunk_cfg["overlap_chars"]
            migrated = True
        if migrated:
            logger.info(
                "advanced_rag chunk config: migrated legacy fields "
                "(max_chars→chunk_size, overlap_chars→chunk_overlap)"
            )

        return config

    def should_update_database(self) -> bool:
        """Check if the database should be updated. Delegated to indexer."""
        return self.indexer.should_update_database()

    def update_database(self, **kwargs) -> dict[str, Any]:
        """Update semantic database. Delegated to indexer."""
        return self.indexer.update_database(**kwargs)

    def search(self,
               query: str,
               limit: int = 10,
               **kwargs: Any) -> list[dict[str, Any]]:
        """
        Perform semantic search for Zotero items.

        Args:
            query: Search query string
            limit: Number of results to return
            **kwargs: Strategy-specific search parameters

        Returns:
            List of search results with similarity scores
        """
        if not query or not query.strip():
            return []

        logger.info(f"Performing semantic search for: '{query}' (limit: {limit})")
        return self.retriever.search(query, limit=limit, **kwargs)

    def get_database_status(self) -> dict[str, Any]:
        """Get the current status of the semantic database."""
        try:
            return self.retriever.get_database_status()
        except Exception as e:
            logger.error(f"Error getting database status: {e}")
            return {"error": str(e)}


def create_semantic_search(config_path: str | None = None,
                          db_path: str | None = None) -> ZoteroSemanticSearch:
    """
    Helper function to create a ZoteroSemanticSearch instance.

    Args:
        config_path: Path to configuration file
        db_path: Optional path to Zotero database

    Returns:
        ZoteroSemanticSearch instance
    """
    return ZoteroSemanticSearch(config_path=config_path, db_path=db_path)
