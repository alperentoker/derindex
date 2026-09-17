"""Fast, local vector storage using NumPy matrix and persistent NPZ storage. Thread-safe."""

import logging
import threading
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
        self._lock = threading.RLock()
        self.load()

    def load(self) -> None:
        """Loads vectors and chunk IDs from persistent file if present."""
        with self._lock:
            if self.storage_path.exists():
                try:
                    data = np.load(self.storage_path)
                    self.chunk_ids = [int(x) for x in data["chunk_ids"]]
                    self.matrix = data["matrix"].astype(np.float32)
                    self._id_to_idx = {cid: idx for idx, cid in enumerate(self.chunk_ids)}
                    logger.info(f"Loaded {len(self.chunk_ids)} vectors from {self.storage_path}")
                except Exception as e:
                    logger.error(f"Error loading vector store from {self.storage_path}: {e}")
                    self.chunk_ids = []
                    self.matrix = None
                    self._id_to_idx = {}

    def _save_unlocked(self) -> None:
        """Persists vectors and chunk IDs to file. Must be called with _lock held."""
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

    def save(self) -> None:
        """Persists vectors and chunk IDs to file (thread-safe)."""
        with self._lock:
            self._save_unlocked()

    def add_vectors(self, chunk_ids: List[int], vectors: np.ndarray, save_to_disk: bool = True) -> None:
        """
        Adds or updates embeddings for the given chunk IDs. Thread-safe.
        Guarantees unit L2-norm for each vector so dot product strictly equals cosine similarity.
        Set save_to_disk=False during batch indexing to avoid repeated disk serialization.
        """
        if len(chunk_ids) == 0 or vectors.size == 0:
            return

        chunk_ids = [int(x) for x in chunk_ids]

        if len(chunk_ids) != vectors.shape[0]:
            raise ValueError(f"chunk_ids count ({len(chunk_ids)}) does not match vectors rows ({vectors.shape[0]})")

        # Guarantee strict L2-normalization for cosine dot-product equivalence
        vec_f32 = vectors.astype(np.float32)
        norms = np.linalg.norm(vec_f32, axis=1, keepdims=True)
        norms[norms == 0.0] = 1.0
        normalized_vectors = vec_f32 / norms

        with self._lock:
            # Handle overwriting existing IDs
            existing_ids = set(self._id_to_idx.keys())
            overwrite_ids = set(chunk_ids).intersection(existing_ids)
            if overwrite_ids:
                self._delete_vectors_unlocked(overwrite_ids)

            if self.matrix is None or len(self.chunk_ids) == 0:
                self.matrix = normalized_vectors
                self.chunk_ids = list(chunk_ids)
            else:
                self.matrix = np.vstack([self.matrix, normalized_vectors])
                self.chunk_ids.extend(chunk_ids)

            self._id_to_idx = {cid: idx for idx, cid in enumerate(self.chunk_ids)}
            if save_to_disk:
                self._save_unlocked()

    def _delete_vectors_unlocked(self, chunk_ids_to_delete: Set[int]) -> None:
        """Internal delete without locking. Must be called with _lock held."""
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

    def delete_vectors(self, chunk_ids_to_delete: Set[int], save_to_disk: bool = True) -> None:
        """Removes specified chunk IDs and their corresponding vectors. Thread-safe."""
        with self._lock:
            self._delete_vectors_unlocked(chunk_ids_to_delete)
            if save_to_disk:
                self._save_unlocked()

    def search(
        self,
        query_vector: np.ndarray,
        top_k: int = 50,
        candidate_ids: Optional[Set[int]] = None
    ) -> List[Tuple[int, float]]:
        """
        Calculates cosine similarity of query_vector against stored vectors.
        Since vectors are guaranteed L2-normalized, cosine similarity is simply the dot product.
        Optional candidate_ids allows fast pre-filtering for scoped / operator queries.
        Returns sorted list of (chunk_id, score). Thread-safe.
        """
        with self._lock:
            if self.matrix is None or len(self.chunk_ids) == 0:
                return []

            # Snapshot references under lock to prevent mid-read mutation
            matrix = self.matrix
            chunk_ids = list(self.chunk_ids)
            id_to_idx = dict(self._id_to_idx)

        # Compute outside lock for maximum concurrency
        q_vec = query_vector.flatten().astype(np.float32)
        q_norm = np.linalg.norm(q_vec)
        if q_norm > 0:
            q_vec = q_vec / q_norm

        if candidate_ids is not None:
            # Filtered search across candidate subset
            target_indices = [id_to_idx[cid] for cid in candidate_ids if cid in id_to_idx]
            if not target_indices:
                return []
            sub_matrix = matrix[target_indices]
            sub_cids = [chunk_ids[idx] for idx in target_indices]
            scores = sub_matrix @ q_vec

            if len(scores) <= top_k:
                sorted_indices = np.argsort(-scores)
            else:
                top_k_unsorted = np.argpartition(-scores, top_k)[:top_k]
                sorted_indices = top_k_unsorted[np.argsort(-scores[top_k_unsorted])]

            results: List[Tuple[int, float]] = []
            for idx in sorted_indices:
                score = float(scores[idx])
                results.append((int(sub_cids[idx]), score))
            return results

        # Full corpus matrix-vector multiplication: (N, D) @ (D,) -> (N,)
        scores = matrix @ q_vec

        # Top-K indices using argpartition for fast O(N) selection
        if len(scores) <= top_k:
            sorted_indices = np.argsort(-scores)
        else:
            top_k_unsorted = np.argpartition(-scores, top_k)[:top_k]
            sorted_indices = top_k_unsorted[np.argsort(-scores[top_k_unsorted])]

        results: List[Tuple[int, float]] = []
        for idx in sorted_indices:
            score = float(scores[idx])
            results.append((int(chunk_ids[idx]), score))

        return results

    def clear(self) -> None:
        """Clears all vectors in memory and on disk. Thread-safe."""
        with self._lock:
            self.chunk_ids = []
            self.matrix = None
            self._id_to_idx = {}
            if self.storage_path.exists():
                self.storage_path.unlink()
