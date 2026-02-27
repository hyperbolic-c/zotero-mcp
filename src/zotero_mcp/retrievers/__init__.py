"""Retriever strategies for semantic search."""

from .base import BaseRetriever
from .factory import create_retriever

__all__ = ["BaseRetriever", "create_retriever"]
