"""Fast, local vector storage using NumPy memory-mapped matrix and persistent NPZ storage."""

import logging
from pathlib import Path
from typing import List, Tuple, Dict, Optional, Set
import numpy as np

from config import config

logger = logging.getLogger(__name__)


class VectorStore:
    def __init__(self, storage_path: Optional[Path] = None):
        self.storage_path = storage_path or config.VECTORS_PATH
        self.chunk_ids: List[int] = []
        self.matrix: Optional[np.ndarray] = None  # Shape: (N, D)
        self._id_to_idx: Dict[int, int] = {}
        self.load()

    def load(self) -> None:
        """Loads vectors and chunk IDs from persistent file if present."""
        if self.storage_path.exists():
            try:
                data = np.load(self.storage_path)
                self.chunk_ids = list(data["chunk_ids"])
                self.matrix = data["matrix"].astype(np.float32)
                self._id_to_idx = {cid: idx for idx, cid in enumerate(self.chunk_ids)}
                logger.info(f"Loaded {len(self.chunk_ids)} vectors from {self.storage_path}")
            except Exception as e:
                logger.error(f"Error loading vector store from {self.storage_path}: {e}")
                self.chunk_ids = []
                self.matrix = None
                self._id_to_idx = {}

    def save(self) -> None:
        """Persists vectors and chunk IDs to file."""
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        if self.matrix is not None and len(self.chunk_ids) > 0:
            np.savez_compressed(
                self.storage_path,
                chunk_ids=np.array(self.chunk_ids, dtype=np.int64),
                matrix=self.matrix
            )
            logger.info(f"Saved {len(self.chunk_ids)} vectors to {self.storage_path}")
        elif self.storage_path.exists():
            self.storage_path.unlink()

    def add_vectors(self, chunk_ids: List[int], vectors: np.ndarray) -> None:
        """
        Adds or updates embeddings for the given chunk IDs.
        """
        if len(chunk_ids) == 0 or vectors.size == 0:
            return

        if len(chunk_ids) != vectors.shape[0]:
            raise ValueError(f"chunk_ids count ({len(chunk_ids)}) does not match vectors rows ({vectors.shape[0]})")

        # Handle overwriting existing IDs
        existing_ids = set(self._id_to_idx.keys())
        overwrite_ids = set(chunk_ids).intersection(existing_ids)
        if overwrite_ids:
            self.delete_vectors(overwrite_ids)

        if self.matrix is None or len(self.chunk_ids) == 0:
            self.matrix = vectors.astype(np.float32)
            self.chunk_ids = list(chunk_ids)
        else:
            self.matrix = np.vstack([self.matrix, vectors.astype(np.float32)])
            self.chunk_ids.extend(chunk_ids)

        self._id_to_idx = {cid: idx for idx, cid in enumerate(self.chunk_ids)}
        self.save()

    def delete_vectors(self, chunk_ids_to_delete: Set[int]) -> None:
        """Removes specified chunk IDs and their corresponding vectors."""
        if not chunk_ids_to_delete or self.matrix is None or len(self.chunk_ids) == 0:
            return

        keep_indices = [
            idx for idx, cid in enumerate(self.chunk_ids)
            if cid not in chunk_ids_to_delete
        ]

        if not keep_indices:
            self.chunk_ids = []
            self.matrix = None
            self._id_to_idx = {}
        else:
            self.chunk_ids = [self.chunk_ids[i] for i in keep_indices]
            self.matrix = self.matrix[keep_indices]
            self._id_to_idx = {cid: idx for idx, cid in enumerate(self.chunk_ids)}

        self.save()

    def search(self, query_vector: np.ndarray, top_k: int = 50) -> List[Tuple[int, float]]:
        """
        Calculates cosine similarity of query_vector against all stored vectors.
        Since vectors are L2-normalized, cosine similarity is simply the dot product.
        Returns sorted list of (chunk_id, score).
        """
        if self.matrix is None or len(self.chunk_ids) == 0:
            return []

        # Ensure 1D query vector
        q_vec = query_vector.flatten().astype(np.float32)
        q_norm = np.linalg.norm(q_vec)
        if q_norm > 0:
            q_vec = q_vec / q_norm

        # Matrix-vector multiplication: (N, D) @ (D,) -> (N,)
        scores = self.matrix @ q_vec

        # Top-K indices using argpartition for fast O(N) selection
        if len(scores) <= top_k:
            sorted_indices = np.argsort(-scores)
        else:
            top_k_unsorted = np.argpartition(-scores, top_k)[:top_k]
            sorted_indices = top_k_unsorted[np.argsort(-scores[top_k_unsorted])]

        results: List[Tuple[int, float]] = []
        for idx in sorted_indices:
            score = float(scores[idx])
            results.append((self.chunk_ids[idx], score))

        return results

    def clear(self) -> None:
        """Clears all vectors in memory and on disk."""
        self.chunk_ids = []
        self.matrix = None
        self._id_to_idx = {}
        if self.storage_path.exists():
            self.storage_path.unlink()
