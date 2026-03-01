"""
Indexing and synchronization logic for Zotero MCP.

This module handles the ingestion of Zotero items into ChromaDB,
tracking update status, and determining when updates are needed.
"""

import json
import os
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .chroma_client import ChromaClient
from .client import get_zotero_client
from .utils import format_creators
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
        db_path: str | None = None,
        semantic_config: dict[str, Any] | None = None,
        chroma_client: ChromaClient | None = None,
        zotero_client: Any | None = None,
        retriever_factory: Any = create_retriever,
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
        self.db_path = db_path
        self.semantic_config = semantic_config or {}
        self.zotero_client = zotero_client or get_zotero_client()
        self.chroma_client = chroma_client
        
        # Initialize update config
        self.update_config = self._load_update_config()
        
        # Create retriever for ingestion/search strategy operations.
        self.retriever = retriever_factory(self.retriever_mode, self)
        
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

    # Legacy support methods used by legacy retrievers
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
        del chroma_client, force_rebuild
        try:
            with LocalZoteroReader(db_path=self.db_path) as reader:
                local_items = reader.get_items_with_text(limit=limit, include_fulltext=extract_fulltext)

            api_items: list[dict[str, Any]] = []
            for item in local_items:
                api_item = {
                    "key": item.key,
                    "version": 0,
                    "data": {
                        "key": item.key,
                        "itemType": item.item_type or "journalArticle",
                        "title": item.title or "",
                        "abstractNote": item.abstract or "",
                        "extra": item.extra or "",
                        "fulltext": item.fulltext or "",
                        "fulltextSource": item.fulltext_source or "",
                        "dateAdded": item.date_added,
                        "dateModified": item.date_modified,
                        "creators": self._parse_creators_string(item.creators or ""),
                    },
                }
                if item.notes:
                    api_item["data"]["notes"] = item.notes
                api_items.append(api_item)

            return api_items
        except Exception as e:
            logger.error(f"Error reading from local database: {e}")
            return []

    def _parse_creators_string(self, creators_str: str) -> list[dict[str, str]]:
        """Parse local DB creators string into API creator objects."""
        if not creators_str:
            return []

        creators: list[dict[str, str]] = []
        for creator in creators_str.split(";"):
            creator = creator.strip()
            if not creator:
                continue
            if "," in creator:
                last, first = creator.split(",", 1)
                creators.append(
                    {
                        "creatorType": "author",
                        "firstName": first.strip(),
                        "lastName": last.strip(),
                    }
                )
            else:
                creators.append({"creatorType": "author", "name": creator})
        return creators

    def _create_document_text(self, item: dict[str, Any]) -> str:
        """Create searchable text from a Zotero item."""
        data = item.get("data", {})
        title = data.get("title", "")
        abstract = data.get("abstractNote", "")
        creators_text = format_creators(data.get("creators", []))

        extra_fields: list[str] = []
        if publication := data.get("publicationTitle"):
            extra_fields.append(publication)
        if tags := data.get("tags"):
            extra_fields.append(" ".join([tag.get("tag", "") for tag in tags]))
        if note := data.get("note"):
            import re

            extra_fields.append(re.sub(r"<[^>]+>", "", note))

        text_parts = [title, creators_text, abstract] + extra_fields
        return " ".join(filter(None, text_parts))

    def _create_metadata(self, item: dict[str, Any]) -> dict[str, Any]:
        """Create metadata for a Zotero item."""
        data = item.get("data", {})
        metadata = {
            "item_key": item.get("key", ""),
            "item_type": data.get("itemType", ""),
            "title": data.get("title", ""),
            "date": data.get("date", ""),
            "date_added": data.get("dateAdded", ""),
            "date_modified": data.get("dateModified", ""),
            "creators": format_creators(data.get("creators", [])),
            "publication": data.get("publicationTitle", ""),
            "url": data.get("url", ""),
            "doi": data.get("DOI", ""),
        }
        if data.get("fulltext"):
            metadata["has_fulltext"] = True
            if data.get("fulltextSource"):
                metadata["fulltext_source"] = data.get("fulltextSource")

        if tags := data.get("tags"):
            metadata["tags"] = " ".join([tag.get("tag", "") for tag in tags])
        else:
            metadata["tags"] = ""

        extra = data.get("extra", "")
        citation_key = ""
        for line in extra.split("\n"):
            if line.lower().startswith(("citation key:", "citationkey:")):
                citation_key = line.split(":", 1)[1].strip()
                break
        metadata["citation_key"] = citation_key

        return metadata

    def _process_item_batch(self, items: list[dict[str, Any]], force_rebuild: bool = False) -> dict[str, int]:
        """Process and upsert a batch of items into ChromaDB."""
        stats = {"processed": 0, "added": 0, "updated": 0, "skipped": 0, "errors": 0}
        documents: list[str] = []
        metadatas: list[dict[str, Any]] = []
        ids: list[str] = []

        for item in items:
            try:
                item_key = item.get("key", "")
                if not item_key:
                    stats["skipped"] += 1
                    continue
                fulltext = item.get("data", {}).get("fulltext", "")
                doc_text = fulltext if fulltext.strip() else self._create_document_text(item)
                metadata = self._create_metadata(item)
                if not doc_text.strip():
                    stats["skipped"] += 1
                    continue
                documents.append(doc_text)
                metadatas.append(metadata)
                ids.append(item_key)
                stats["processed"] += 1
            except Exception as e:
                logger.error(f"Error processing item {item.get('key', 'unknown')}: {e}")
                stats["errors"] += 1

        if documents and self.chroma_client is not None:
            try:
                existing_ids = set()
                if not force_rebuild:
                    existing_ids = self.chroma_client.get_existing_ids(ids)
                self.chroma_client.upsert_documents(documents, metadatas, ids)
                for doc_id in ids:
                    if doc_id in existing_ids:
                        stats["updated"] += 1
                    else:
                        stats["added"] += 1
            except Exception as e:
                logger.error(f"Error adding documents to ChromaDB: {e}")
                stats["errors"] += len(documents)
        return stats

    def _legacy_update_database(
        self,
        force_full_rebuild: bool = False,
        limit: int | None = None,
        extract_fulltext: bool = False,
    ) -> dict[str, Any]:
        """Legacy ingestion path used by legacy retrievers."""
        start_time = datetime.now()
        stats = {
            "total_items": 0,
            "processed_items": 0,
            "added_items": 0,
            "updated_items": 0,
            "skipped_items": 0,
            "errors": 0,
            "start_time": start_time.isoformat(),
            "duration": None,
        }
        try:
            if self.chroma_client is None:
                return {**stats, "error": "Chroma client is not initialized"}

            if force_full_rebuild:
                self.chroma_client.reset_collection()

            all_items = self._get_items_from_source(
                limit=limit,
                extract_fulltext=extract_fulltext,
                chroma_client=self.chroma_client if not force_full_rebuild else None,
                force_rebuild=force_full_rebuild,
            )
            stats["total_items"] = len(all_items)

            batch_size = 50
            for i in range(0, len(all_items), batch_size):
                batch = all_items[i : i + batch_size]
                batch_stats = self._process_item_batch(batch, force_full_rebuild)
                stats["processed_items"] += batch_stats["processed"]
                stats["added_items"] += batch_stats["added"]
                stats["updated_items"] += batch_stats["updated"]
                stats["skipped_items"] += batch_stats["skipped"]
                stats["errors"] += batch_stats["errors"]

            end_time = datetime.now()
            stats["duration"] = str(end_time - start_time)
            stats["end_time"] = end_time.isoformat()
            return stats
        except Exception as e:
            logger.error(f"Error updating database: {e}")
            end_time = datetime.now()
            stats["duration"] = str(end_time - start_time)
            stats["error"] = str(e)
            return stats

    def _enrich_search_results(self, chroma_results: dict[str, Any], query: str) -> list[dict[str, Any]]:
        """Enrich ChromaDB results with Zotero item payloads."""
        enriched: list[dict[str, Any]] = []
        if not chroma_results.get("ids") or not chroma_results["ids"][0]:
            return enriched

        ids = chroma_results["ids"][0]
        distances = chroma_results.get("distances", [[]])[0]
        documents = chroma_results.get("documents", [[]])[0]
        metadatas = chroma_results.get("metadatas", [[]])[0]

        for i, item_key in enumerate(ids):
            try:
                zotero_item = self.zotero_client.item(item_key)
                enriched.append(
                    {
                        "item_key": item_key,
                        "similarity_score": 1 - distances[i] if i < len(distances) else 0,
                        "matched_text": documents[i] if i < len(documents) else "",
                        "metadata": metadatas[i] if i < len(metadatas) else {},
                        "zotero_item": zotero_item,
                        "query": query,
                    }
                )
            except Exception as e:
                enriched.append(
                    {
                        "item_key": item_key,
                        "similarity_score": 1 - distances[i] if i < len(distances) else 0,
                        "matched_text": documents[i] if i < len(documents) else "",
                        "metadata": metadatas[i] if i < len(metadatas) else {},
                        "query": query,
                        "error": f"Could not fetch full item data: {e}",
                    }
                )
        return enriched

    def _legacy_search(
        self, query: str, limit: int = 10, filters: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Legacy search path for legacy retrievers."""
        try:
            if self.chroma_client is None:
                return {"query": query, "limit": limit, "filters": filters, "results": [], "total_found": 0, "error": "Chroma client is not initialized"}
            results = self.chroma_client.search(query_texts=[query], n_results=limit, where=filters)
            enriched_results = self._enrich_search_results(results, query)
            return {
                "query": query,
                "limit": limit,
                "filters": filters,
                "results": enriched_results,
                "total_found": len(enriched_results),
            }
        except Exception as e:
            logger.error(f"Error performing semantic search: {e}")
            return {
                "query": query,
                "limit": limit,
                "filters": filters,
                "results": [],
                "total_found": 0,
                "error": str(e),
            }

    def _legacy_get_database_status(self) -> dict[str, Any]:
        """Legacy status payload for legacy retrievers."""
        if self.chroma_client is None:
            return {
                "collection_info": {"error": "Chroma client is not initialized"},
                "update_config": self.update_config,
                "should_update": self.should_update_database(),
                "last_update": self.update_config.get("last_update"),
                "retriever_mode": self.retriever_mode,
            }
        return {
            "collection_info": self.chroma_client.get_collection_info(),
            "update_config": self.update_config,
            "should_update": self.should_update_database(),
            "last_update": self.update_config.get("last_update"),
            "retriever_mode": self.retriever_mode,
        }
