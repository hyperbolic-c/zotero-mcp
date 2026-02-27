from __future__ import annotations

import json
import logging
import os
from typing import Any

from zotero_mcp.chroma_client import ChromaClient, create_chroma_client

from .advanced_rag import AdvancedRAGRetriever
from .base import BaseRetriever
from .legacy_fulltext import LegacyFulltextRetriever
from .legacy_metadata import LegacyMetadataRetriever

logger = logging.getLogger(__name__)


def _load_chroma_config(config_path: str | None) -> dict[str, Any]:
    config = {
        "collection_name": "zotero_library",
        "embedding_model": "default",
        "embedding_config": {},
        "persist_directory": None,
    }
    if config_path and os.path.exists(config_path):
        try:
            with open(config_path) as f:
                file_config = json.load(f)
            config.update(file_config.get("semantic_search", {}))
        except Exception as exc:
            logger.warning("Error loading semantic config for retriever factory: %s", exc)

    env_embedding_model = os.getenv("ZOTERO_EMBEDDING_MODEL")
    if env_embedding_model:
        config["embedding_model"] = env_embedding_model

    return config


def _build_advanced_chroma_client(config_path: str | None) -> ChromaClient:
    config = _load_chroma_config(config_path)
    return ChromaClient(
        collection_name="zotero_rag_chunks_v1",
        persist_directory=config.get("persist_directory"),
        embedding_model=config.get("embedding_model", "default"),
        embedding_config=config.get("embedding_config", {}),
    )


def create_retriever(mode: str, engine: Any) -> BaseRetriever:
    if mode == "legacy_metadata":
        return LegacyMetadataRetriever(engine)

    if mode == "legacy_fulltext":
        return LegacyFulltextRetriever(engine)

    if mode == "advanced_rag":
        chroma_client = engine.chroma_client or _build_advanced_chroma_client(engine.config_path)
        return AdvancedRAGRetriever(engine=engine, chroma_client=chroma_client)

    logger.warning("Unknown retriever mode '%s', falling back to legacy_metadata", mode)
    return LegacyMetadataRetriever(engine)
