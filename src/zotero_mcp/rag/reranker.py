"""Reranker module for advanced RAG."""

import logging
import os
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class CandidateChunk:
    """Represents a candidate chunk from vector search."""
    item_key: str
    attachment_key: str
    text: str
    metadata: dict[str, Any]
    similarity_score: float
    rank_score: float


class Reranker:
    """Handles reranking of search candidates using flashrank."""

    def __init__(self, config: dict[str, Any]):
        self.config = config
        self._ranker = None
        self._reranker_status = self._init_reranker()

    def status(self) -> str:
        """Return the current status of the reranker."""
        return self._reranker_status

    def _init_reranker(self) -> str:
        """Initialize the reranker based on config."""
        if not self.config.get("enabled", True):
            return "disabled"
        if self.config.get("backend", "flashrank") != "flashrank":
            return "degraded:unsupported_backend"
        try:
            from flashrank import Ranker

            model_name = self.config.get("model_name", "ms-marco-MiniLM-L-12-v2")
            local_model_path = self.config.get("local_model_path")
            kwargs = {"model_name": model_name}
            if local_model_path and os.path.exists(local_model_path):
                kwargs["cache_dir"] = local_model_path
            self._ranker = Ranker(**kwargs)
            return f"enabled:flashrank/{model_name}"
        except ImportError:
            self._ranker = None
            return "degraded:package_missing"
        except Exception as exc:
            self._ranker = None
            return f"degraded:init_error:{type(exc).__name__}"

    def rerank(
        self, query: str, candidates: list[CandidateChunk]
    ) -> list[CandidateChunk]:
        """Rerank candidates using the flashrank model."""
        if not candidates or self._ranker is None:
            return candidates

        top_n = int(self.config.get("top_n", 8))
        try:
            from flashrank import RerankRequest

            passages = [{"id": str(i), "text": c.text} for i, c in enumerate(candidates)]
            results = self._ranker.rank(RerankRequest(query=query, passages=passages), top_n=top_n)
            reranked: list[CandidateChunk] = []
            for result in results:
                idx = int(result.get("id", -1))
                if idx < 0 or idx >= len(candidates):
                    continue
                candidate = candidates[idx]
                score = float(result.get("score", candidate.similarity_score))
                reranked.append(
                    CandidateChunk(
                        item_key=candidate.item_key,
                        attachment_key=candidate.attachment_key,
                        text=candidate.text,
                        metadata=candidate.metadata,
                        similarity_score=candidate.similarity_score,
                        rank_score=score,
                    )
                )
            return reranked if reranked else candidates
        except Exception:
            return candidates
