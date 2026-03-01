"""
Indexing and synchronization logic for Zotero MCP.

This module handles the ingestion of Zotero items into ChromaDB,
tracking update status, and determining when updates are needed.
"""

import json
import os
import sys
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from .chroma_client import ChromaClient
from .client import get_zotero_client
from .utils import is_local_mode
from .local_db import LocalZoteroReader
from .retrievers.factory import create_retriever

logger = logging.getLogger(__name__)

class ZoteroIndexer:
    """Handles synchronization of Zotero data to ChromaDB."""

    def __init__(
        self,
        retriever_mode: str = "legacy_metadata",
        config_path: str | None = None,
        semantic_config: dict[str, Any] | None = None,
        chroma_client: ChromaClient | None = None,
        zotero_client: Any | None = None,
    ):
        """
        Initialize the indexer.

        Args:
            retriever_mode: Mode for data ingestion ('legacy_metadata', 'advanced_rag', etc.)
            config_path: Path to configuration file for saving update state
            semantic_config: Full semantic configuration dictionary
            chroma_client: Optional ChromaClient instance
            zotero_client: Optional Zotero client instance
        """
        self.retriever_mode = retriever_mode
        self.config_path = config_path
        self.semantic_config = semantic_config or {}
        self.zotero_client = zotero_client or get_zotero_client()
        self.chroma_client = chroma_client
        
        # Initialize update config
        self.update_config = self._load_update_config()
        
        # Create retriever for ingestion
        # Note: We pass a proxy or limited context if needed, 
        # but for now factory expects the search object. 
        # We might need to adjust factory.py later.
        from .retrievers.factory import create_retriever
        self.retriever = create_retriever(self.retriever_mode, self)
        
        if self.chroma_client is None:
            self.chroma_client = getattr(self.retriever, "chroma_client", None)

    def _load_update_config(self) -> dict[str, Any]:
        """Load update configuration from file or use defaults."""
        config = {
            "auto_update": False,
            "update_frequency": "manual",
            "last_update": None,
            "update_days": 7
        }

        if self.config_path and os.path.exists(self.config_path):
            try:
                with open(self.config_path) as f:
                    file_config = json.load(f)
                    config.update(file_config.get("semantic_search", {}).get("update_config", {}))
            except Exception as e:
                logger.warning(f"Error loading update config: {e}")

        return config

    def _save_update_config(self) -> None:
        """Save update configuration to file."""
        if not self.config_path:
            return

        config_dir = Path(self.config_path).parent
        config_dir.mkdir(parents=True, exist_ok=True)

        # Load existing config or create new one
        full_config = {}
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path) as f:
                    full_config = json.load(f)
            except Exception:
                pass

        # Update semantic search config
        if "semantic_search" not in full_config:
            full_config["semantic_search"] = {}

        full_config["semantic_search"]["update_config"] = self.update_config

        try:
            with open(self.config_path, 'w') as f:
                json.dump(full_config, f, indent=2)
        except Exception as e:
            logger.error(f"Error saving update config: {e}")

    def should_update_database(self) -> bool:
        """Check if the database should be updated based on configuration."""
        if not self.update_config.get("auto_update", False):
            return False

        frequency = self.update_config.get("update_frequency", "manual")

        if frequency == "manual":
            return False
        elif frequency == "startup":
            return True
        elif frequency == "daily":
            last_update = self.update_config.get("last_update")
            if not last_update:
                return True

            last_update_date = datetime.fromisoformat(last_update)
            return datetime.now() - last_update_date >= timedelta(days=1)
        elif frequency.startswith("every_"):
            try:
                days = int(frequency.split("_")[1])
                last_update = self.update_config.get("last_update")
                if not last_update:
                    return True

                last_update_date = datetime.fromisoformat(last_update)
                return datetime.now() - last_update_date >= timedelta(days=days)
            except (ValueError, IndexError):
                return False

        return False

    def update_database(
        self,
        force_full_rebuild: bool = False,
        limit: int | None = None,
        extract_fulltext: bool = False,
    ) -> dict[str, Any]:
        """Update semantic database using the configured retriever strategy."""
        start_time = datetime.now()
        
        # Delegate to retriever
        stats = self.retriever.ingest_data(
            force_rebuild=force_full_rebuild,
            limit=limit,
            extract_fulltext=extract_fulltext,
        )
        
        end_time = datetime.now()
        stats.setdefault("start_time", start_time.isoformat())
        stats.setdefault("end_time", end_time.isoformat())
        stats.setdefault("duration", str(end_time - start_time))
        
        if "added_items" not in stats and "added_chunks" in stats:
            stats["added_items"] = stats.get("added_chunks", 0)
        if "updated_items" not in stats and "updated_chunks" in stats:
            stats["updated_items"] = stats.get("updated_chunks", 0)
            
        self.update_config["last_update"] = datetime.now().isoformat()
        self._save_update_config()
        
        if "retriever_mode" not in stats:
            stats["retriever_mode"] = self.retriever_mode
            
        return stats

    # Legacy support methods if needed for older retrievers
    def _get_items_from_source(self, limit: int | None = None, extract_fulltext: bool = False, chroma_client: ChromaClient | None = None, force_rebuild: bool = False) -> list[dict[str, Any]]:
        """Get items from either local database or API."""
        if extract_fulltext and is_local_mode():
            return self._get_items_from_local_db(
                limit,
                extract_fulltext=extract_fulltext,
                chroma_client=chroma_client,
                force_rebuild=force_rebuild
            )
        else:
            return self._get_items_from_api(limit)

    def _get_items_from_api(self, limit: int | None = None) -> list[dict[str, Any]]:
        """Get items from Zotero API."""
        logger.info("Retrieving items from Zotero API...")
        all_items = []
        batch_size = 100
        start = 0

        while True:
            batch_params = {
                "limit": batch_size,
                "start": start,
                "sort": "dateModified",
                "direction": "desc"
            }
            
            if limit and start + batch_size > limit:
                batch_params["limit"] = limit - start
                if batch_params["limit"] <= 0:
                    break

            try:
                items = self.zotero_client.items(**batch_params)
            except Exception as e:
                raise Exception(f"Zotero API connection error: {e}") from e
                
            if not items:
                break

            filtered_items = [
                item for item in items
                if item.get("data", {}).get("itemType") not in ["attachment", "note"]
            ]

            all_items.extend(filtered_items)
            start += batch_size

            if len(items) < batch_size:
                break

        if limit:
            all_items = all_items[:limit]

        return all_items

    def _get_items_from_local_db(self, limit: int | None = None, extract_fulltext: bool = False, chroma_client: ChromaClient | None = None, force_rebuild: bool = False) -> list[dict[str, Any]]:
        """Get items from local Zotero database."""
        try:
            from .semantic_search import create_chroma_client # For backward compat in legacy methods
            reader = LocalZoteroReader(db_path=getattr(self, "db_path", None))
            return reader.get_items(
                limit=limit,
                extract_fulltext=extract_fulltext,
                chroma_client=chroma_client,
                force_rebuild=force_rebuild
            )
        except Exception as e:
            logger.error(f"Error reading from local database: {e}")
            return []
