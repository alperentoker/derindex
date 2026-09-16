"""Hybrid search engine combining BM25 lexical ranking and semantic embeddings."""

import re
import logging
from typing import List, Dict, Tuple, Optional, Any
from collections import defaultdict

from config import config
from app.database.db import Database
from app.database.models import SearchResult, ChunkRecord, DocumentRecord
from app.indexing.bm25 import BM25Engine
from app.embeddings.embedder import LocalEmbedder
from app.embeddings.vector_store import VectorStore
from .reranker import Reranker

logger = logging.getLogger(__name__)


class HybridSearcher:
    def __init__(
        self,
        db: Optional[Database] = None,
        vector_store: Optional[VectorStore] = None,
        embedder: Optional[LocalEmbedder] = None,
        bm25_engine: Optional[BM25Engine] = None,
        reranker: Optional[Reranker] = None
    ):
        self.db = db or Database()
        self.vector_store = vector_store or VectorStore()
        self.embedder = embedder or LocalEmbedder.get_instance()
        self.bm25 = bm25_engine or BM25Engine(self.db)
        self.reranker = reranker or Reranker()

    @staticmethod
    def _normalize_scores(scores_dict: Dict[int, float]) -> Dict[int, float]:
        """Min-Max normalization of scores into range [0.0, 1.0]."""
        if not scores_dict:
            return {}

        min_val = min(scores_dict.values())
        max_val = max(scores_dict.values())

        if max_val == min_val:
            return {k: 1.0 for k in scores_dict}

        diff = max_val - min_val
        return {k: max(0.0, min(1.0, (v - min_val) / diff)) for k, v in scores_dict.items()}

    def _extract_snippet(self, text: str, query: str, max_chars: int = 350) -> str:
        """Finds the most relevant snippet of text containing query terms."""
        if len(text) <= max_chars:
            return text.strip()

        words = [re.escape(w.lower()) for w in re.findall(r'\w+', query) if len(w) > 1]
        if not words:
            return text[:max_chars].strip() + "..."

        pattern = re.compile(r'(' + '|'.join(words) + r')', re.IGNORECASE)
        matches = list(pattern.finditer(text))

        if not matches:
            return text[:max_chars].strip() + "..."

        # Focus around the first match
        first_match = matches[0]
        start_idx = max(0, first_match.start() - 100)
        end_idx = min(len(text), start_idx + max_chars)

        snippet = text[start_idx:end_idx].strip()
        prefix = "..." if start_idx > 0 else ""
        suffix = "..." if end_idx < len(text) else ""
        return f"{prefix}{snippet}{suffix}"

    def search(
        self,
        query: str,
        alpha: float = config.DEFAULT_ALPHA,
        limit: int = config.SEARCH_LIMIT_DEFAULT,
        code_only: bool = False
    ) -> List[SearchResult]:
        """
        Executes hybrid search:
        1. BM25 scoring
        2. Local embedding semantic similarity scoring
        3. Min-Max normalization
        4. Weighted combination: alpha * BM25 + (1 - alpha) * Semantic
        5. Metadata lookup & snippet generation
        6. Reranking
        """
        if not query or not query.strip():
            return []

        # 1. BM25 Search
        bm25_raw = self.bm25.score_query(query, top_k=limit * 4)
        bm25_dict = dict(bm25_raw)

        # 2. Semantic Search
        semantic_dict: Dict[int, float] = {}
        try:
            query_vector = self.embedder.embed_query(query)
            semantic_raw = self.vector_store.search(query_vector, top_k=limit * 4)
            semantic_dict = dict(semantic_raw)
        except Exception as e:
            logger.warning(f"Semantic search embedding failed or unavailable: {e}")

        # If both are empty, return early
        all_chunk_ids = set(bm25_dict.keys()).union(set(semantic_dict.keys()))
        if not all_chunk_ids:
            return []

        # 3. Score Normalization
        norm_bm25 = self._normalize_scores(bm25_dict)
        norm_semantic = self._normalize_scores(semantic_dict)

        # 4. Score Combination: final_score = alpha * bm25_score + (1 - alpha) * semantic_score
        fused_scores: Dict[int, Tuple[float, float, float]] = {}  # chunk_id -> (final, bm25, sem)
        for cid in all_chunk_ids:
            b_score = norm_bm25.get(cid, 0.0)
            s_score = norm_semantic.get(cid, 0.0)
            combined = (alpha * b_score) + ((1.0 - alpha) * s_score)
            fused_scores[cid] = (combined, b_score, s_score)

        # 5. Fetch Chunk and Document records from DB
        top_chunk_ids = [cid for cid, _ in sorted(fused_scores.items(), key=lambda x: x[1][0], reverse=True)]
        chunk_data_map = self.db.get_chunks_by_ids(top_chunk_ids[:limit * 3])

        results: List[SearchResult] = []
        for cid in top_chunk_ids:
            if cid not in chunk_data_map:
                continue

            chunk, doc = chunk_data_map[cid]

            # Filter code if requested
            if code_only and not chunk.code_type:
                continue

            final_s, b_s, s_s = fused_scores[cid]
            snippet = self._extract_snippet(chunk.text, query)

            results.append(SearchResult(
                chunk_id=chunk.id,
                doc_id=doc.id,
                filename=doc.filename,
                path=doc.path,
                score=final_s,
                matched_snippet=snippet,
                page_number=chunk.page_number,
                section=chunk.section_title,
                code_type=chunk.code_type,
                symbol_name=chunk.symbol_name,
                start_line=chunk.start_line,
                end_line=chunk.end_line,
                bm25_score=b_s,
                semantic_score=s_s
            ))

        # 6. Apply Reranking
        reranked = self.reranker.rerank(results, query)
        return reranked[:limit]

    def search_code(self, query: str, limit: int = config.SEARCH_LIMIT_DEFAULT) -> List[SearchResult]:
        """Search strictly across code chunks."""
        return self.search(query, alpha=config.DEFAULT_ALPHA, limit=limit, code_only=True)

    def search_symbol(self, symbol_name: str, limit: int = 20) -> List[Dict[str, Any]]:
        """Direct database lookup for code symbols (functions, classes, structs)."""
        return self.db.find_symbols(symbol_name, limit=limit)
