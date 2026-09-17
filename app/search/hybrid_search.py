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

        words = [re.escape(w.lower()) for w in re.findall(r'[a-z0-9_ğüşıöç]+', query.lower()) if w]
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

    def search_paginated(
        self,
        query: str,
        page: int = 1,
        limit: int = config.SEARCH_LIMIT_DEFAULT,
        alpha: float = config.DEFAULT_ALPHA,
        code_only: bool = False
    ) -> Tuple[List[SearchResult], int]:
        """
        Executes hybrid search with pagination:
        1. BM25 scoring
        2. Local embedding semantic similarity scoring
        3. Filename and code symbol matching
        4. Min-Max normalization and score combination
        5. Metadata lookup & snippet generation
        6. Reranking
        7. Paging slice: (page_results, total_matching_count)
        """
        if not query or not query.strip():
            return [], 0

        page = max(1, page)
        limit = max(1, limit)
        offset = (page - 1) * limit
        pool_size = max(500, offset + limit * 5)

        # 1. BM25 Search
        bm25_raw = self.bm25.score_query(query, top_k=pool_size)
        bm25_dict: Dict[int, float] = {int(cid): float(score) for cid, score in bm25_raw}

        # 2. Semantic Search (with threshold filtering to prevent random noise)
        semantic_dict: Dict[int, float] = {}
        sem_threshold = getattr(config, "SEMANTIC_MIN_SCORE", 0.36)
        try:
            query_vector = self.embedder.embed_query(query)
            semantic_raw = self.vector_store.search(query_vector, top_k=pool_size)
            for cid, raw_score in semantic_raw:
                cid = int(cid)
                raw_s = float(raw_score)
                if raw_s >= sem_threshold:
                    # Calibrate score smoothly into [0.0, 1.0] range above threshold
                    calibrated = (raw_s - sem_threshold) / (1.0 - sem_threshold)
                    semantic_dict[cid] = min(1.0, max(0.0, calibrated))
        except Exception as e:
            logger.warning(f"Semantic search embedding failed or unavailable: {e}")

        # 3. Filename & Symbol Candidates
        filename_matches = self.db.find_chunk_ids_by_filename(query, limit=100)
        symbol_matches = self.db.find_chunk_ids_by_symbol(query, limit=100)

        max_bm25 = max(bm25_dict.values()) if bm25_dict else 1.0
        for cid, fn_weight in filename_matches:
            cid = int(cid)
            if cid not in bm25_dict:
                bm25_dict[cid] = fn_weight * max_bm25
            else:
                bm25_dict[cid] += fn_weight * max_bm25 * 0.5

        for cid, sym_weight in symbol_matches:
            cid = int(cid)
            if cid not in bm25_dict:
                bm25_dict[cid] = sym_weight * max_bm25
            else:
                bm25_dict[cid] += sym_weight * max_bm25 * 0.5

        # If all sources are empty, return early
        all_chunk_ids = set(bm25_dict.keys()).union(set(semantic_dict.keys()))
        if not all_chunk_ids:
            return [], 0

        # 4. Score Normalization
        norm_bm25 = self._normalize_scores(bm25_dict)
        # semantic_dict is already calibrated into [0.0, 1.0] above threshold

        # 5. Score Combination: final_score = alpha * bm25_score + (1 - alpha) * semantic_score
        fused_scores: Dict[int, Tuple[float, float, float]] = {}  # chunk_id -> (final, bm25, sem)
        for cid in all_chunk_ids:
            b_score = norm_bm25.get(cid, 0.0)
            s_score = semantic_dict.get(cid, 0.0)
            combined = (alpha * b_score) + ((1.0 - alpha) * s_score)
            
            # CRITICAL: Only include results that have actual relevance
            if combined > 0.005:
                fused_scores[cid] = (combined, b_score, s_score)

        if not fused_scores:
            return [], 0

        # 6. Fetch Chunk and Document records from DB
        top_chunk_ids = [int(cid) for cid, _ in sorted(fused_scores.items(), key=lambda x: x[1][0], reverse=True)]
        candidate_chunk_ids = top_chunk_ids[:pool_size]
        chunk_data_map = self.db.get_chunks_by_ids(candidate_chunk_ids)

        all_results: List[SearchResult] = []
        for cid in candidate_chunk_ids:
            if cid not in chunk_data_map:
                continue

            chunk, doc = chunk_data_map[cid]

            # Filter code if requested
            if code_only and not chunk.code_type:
                continue

            final_s, b_s, s_s = fused_scores[cid]
            snippet = self._extract_snippet(chunk.text, query)

            all_results.append(SearchResult(
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

        # 7. Apply Reranking
        reranked = self.reranker.rerank(all_results, query)
        total_count = len(reranked)

        # 8. Return paginated slice
        paged_results = reranked[offset : offset + limit]
        return paged_results, total_count

    def search(
        self,
        query: str,
        alpha: float = config.DEFAULT_ALPHA,
        limit: int = config.SEARCH_LIMIT_DEFAULT,
        code_only: bool = False
    ) -> List[SearchResult]:
        """Backwards-compatible search returning top results."""
        results, _ = self.search_paginated(
            query=query,
            page=1,
            limit=limit,
            alpha=alpha,
            code_only=code_only
        )
        return results

    def search_code(self, query: str, limit: int = config.SEARCH_LIMIT_DEFAULT) -> List[SearchResult]:
        """Search strictly across code chunks."""
        return self.search(query, alpha=config.DEFAULT_ALPHA, limit=limit, code_only=True)

    def search_symbol(self, symbol_name: str, limit: int = 20) -> List[Dict[str, Any]]:
        """Direct database lookup for code symbols (functions, classes, structs)."""
        return self.db.find_symbols(symbol_name, limit=limit)
