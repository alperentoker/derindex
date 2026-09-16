"""Embeddings and vector storage package."""

from .embedder import LocalEmbedder
from .vector_store import VectorStore

__all__ = ["LocalEmbedder", "VectorStore"]
