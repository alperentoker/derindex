"""Local embedding model manager using sentence-transformers."""

import logging
from typing import List, Union, Optional
import numpy as np

from config import config

logger = logging.getLogger(__name__)


class LocalEmbedder:
    _instance: Optional["LocalEmbedder"] = None

    def __init__(self, model_name: str = config.EMBEDDING_MODEL_NAME):
        self.model_name = model_name
        self._model = None

    @classmethod
    def get_instance(cls) -> "LocalEmbedder":
        if cls._instance is None:
            cls._instance = LocalEmbedder()
        return cls._instance

    def _load_model(self):
        if self._model is None:
            try:
                import torch
                from sentence_transformers import SentenceTransformer
                device = "cuda" if torch.cuda.is_available() else "cpu"
                logger.info(f"Loading local embedding model '{self.model_name}' on device '{device}'...")
                self._model = SentenceTransformer(self.model_name, device=device)
                logger.info("Embedding model loaded successfully.")
            except Exception as e:
                logger.error(f"Failed to load sentence-transformers model {self.model_name}: {e}")
                raise

    def embed_texts(self, texts: List[str], batch_size: int = config.EMBEDDING_BATCH_SIZE) -> np.ndarray:
        """
        Embeds a list of texts into normalized float32 vectors.
        Shape: (len(texts), embedding_dim)
        """
        if not texts:
            return np.empty((0, config.EMBEDDING_DIMENSION), dtype=np.float32)

        self._load_model()
        embeddings = self._model.encode(
            texts,
            batch_size=batch_size,
            show_progress_bar=False,
            normalize_embeddings=True,
            convert_to_numpy=True
        )
        return embeddings.astype(np.float32)

    def embed_query(self, query: str) -> np.ndarray:
        """
        Embeds a single query string into a normalized 1D float32 vector.
        """
        self._load_model()
        vec = self._model.encode(
            query,
            show_progress_bar=False,
            normalize_embeddings=True,
            convert_to_numpy=True
        )
        return vec.astype(np.float32)
