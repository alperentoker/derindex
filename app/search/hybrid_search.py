"""Hybrid search engine combining BM25 lexical ranking and semantic embeddings."""

import re
import time
import logging
from typing import List, Dict, Tuple, Optional, Any, Set
from collections import defaultdict

from config import config
from app.database.db import Database
from app.database.models import SearchResult, ChunkRecord, DocumentRecord
from app.indexing.bm25 import BM25Engine
from app.embeddings.embedder import LocalEmbedder
from app.embeddings.vector_store import VectorStore
from .reranker import Reranker
from .query_parser import QueryParser, ParsedQuery

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
        """Safely normalizes scores into [0.0, 1.0] range without inflating isolated weak scores."""
        if not scores_dict:
            return {}

        max_val = max(scores_dict.values())
        if max_val <= 0.0:
            return {k: 0.0 for k in scores_dict}

        min_val = min(scores_dict.values())
        if max_val == min_val:
            # Single item or identical scores: preserve actual scale capped at 1.0
            return {k: min(1.0, max(0.0, v)) for k, v in scores_dict.items()}

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
        Executes hybrid search with syntax parsing, candidate pre-filtering,
        BM25 lexical scoring, semantic vector scoring, recency boosting, and reranking.
        """
        if not query or not query.strip():
            return [], 0

        # Parse query syntax for filters (ext:, path:, type:, after:, before:, symbol:)
        parsed: ParsedQuery = QueryParser.parse(query)
        effective_query = parsed.clean_query.strip()

        page = max(1, page)
        limit = max(1, limit)
        offset = (page - 1) * limit
        pool_size = max(500, offset + limit * 5)

        # Determine if candidate pre-filtering is active
        is_code = code_only or (parsed.file_type == "code")
        allowed_type_exts = (
            QueryParser.TYPE_EXTENSIONS.get(parsed.file_type)
            if parsed.file_type and parsed.file_type != "code"
            else None
        )

        candidate_ids_set: Optional[Set[int]] = None
        candidate_ordered_list: Optional[List[int]] = None
        if parsed.has_filters or is_code:
            # If there's an effective text query, do not artificially limit candidates (all matching files should be searchable)
            filter_limit = None if effective_query else pool_size * 2
            candidate_ordered_list = self.db.get_candidate_chunk_ids_by_filters(
                extensions=parsed.extensions if parsed.extensions else None,
                exclude_extensions=parsed.exclude_extensions if parsed.exclude_extensions else None,
                path_pattern=parsed.path_pattern,
                allowed_type_extensions=allowed_type_exts,
                code_only=is_code,
                after_timestamp=parsed.after_timestamp,
                before_timestamp=parsed.before_timestamp,
                symbol_filter=parsed.symbol_filter,
                limit=filter_limit
            )
            if not candidate_ordered_list:
                return [], 0
            candidate_ids_set = set(candidate_ordered_list)

        now_ts = time.time()

        # Handle pure filter queries without text (e.g., "ext:pdf" or "type:code")
        if not effective_query:
            if not candidate_ordered_list:
                return [], 0
            chunk_data_map = self.db.get_chunks_by_ids(candidate_ordered_list)
            pure_results: List[SearchResult] = []
            seen_doc_ids: Set[int] = set()

            for cid in candidate_ordered_list:
                if cid not in chunk_data_map:
                    continue
                chunk, doc = chunk_data_map[cid]

                # Deduplicate by document: only show 1 representative chunk per file
                if doc.id in seen_doc_ids:
                    continue
                seen_doc_ids.add(doc.id)


                # Recency Boost calculation
                age_seconds = now_ts - doc.mtime
                recency_boost = 0.08 if age_seconds <= 7 * 86400 else (0.04 if age_seconds <= 30 * 86400 else 0.0)
                final_score = min(1.0, 0.70 + recency_boost)

                snippet = chunk.text[:300].strip() + ("..." if len(chunk.text) > 300 else "")
                pure_results.append(SearchResult(
                    chunk_id=chunk.id,
                    doc_id=doc.id,
                    filename=doc.filename,
                    path=doc.path,
                    score=final_score,
                    raw_score=0.70,
                    recency_boost=recency_boost,
                    mtime=doc.mtime,
                    matched_snippet=snippet,
                    page_number=chunk.page_number,
                    section=chunk.section_title,
                    code_type=chunk.code_type,
                    symbol_name=chunk.symbol_name,
                    start_line=chunk.start_line,
                    end_line=chunk.end_line,
                    bm25_score=0.0,
                    semantic_score=0.0
                ))
            pure_results.sort(key=lambda r: (r.score, r.mtime or 0.0), reverse=True)
            return pure_results[offset : offset + limit], len(pure_results)


        # 1. BM25 Search
        bm25_raw = self.bm25.score_query(effective_query, top_k=pool_size)
        bm25_dict: Dict[int, float] = {
            int(cid): float(score)
            for cid, score in bm25_raw
            if candidate_ids_set is None or int(cid) in candidate_ids_set
        }

        # 2. Semantic Search (with candidate pre-filtering to avoid unnecessary cosine distances)
        semantic_dict: Dict[int, float] = {}
        sem_threshold = getattr(config, "SEMANTIC_MIN_SCORE", 0.36)
        try:
            query_vector = self.embedder.embed_query(effective_query)
            semantic_raw = self.vector_store.search(
                query_vector,
                top_k=pool_size,
                candidate_ids=candidate_ids_set
            )
            for cid, raw_score in semantic_raw:
                cid = int(cid)
                raw_s = float(raw_score)
                if raw_s >= sem_threshold:
                    calibrated = (raw_s - sem_threshold) / (1.0 - sem_threshold)
                    semantic_dict[cid] = min(1.0, max(0.0, calibrated))
        except Exception as e:
            logger.warning(f"Semantic search embedding failed or unavailable: {e}")

        # 3. Filename & Symbol Candidates
        filename_matches = self.db.find_chunk_ids_by_filename(effective_query, limit=100)
        symbol_matches = self.db.find_chunk_ids_by_symbol(effective_query, limit=100)

        max_bm25 = max(bm25_dict.values()) if bm25_dict else 1.0
        for cid, fn_weight in filename_matches:
            cid = int(cid)
            if candidate_ids_set is not None and cid not in candidate_ids_set:
                continue
            if cid not in bm25_dict:
                bm25_dict[cid] = fn_weight * max_bm25
            else:
                bm25_dict[cid] += fn_weight * max_bm25 * 0.5

        for cid, sym_weight in symbol_matches:
            cid = int(cid)
            if candidate_ids_set is not None and cid not in candidate_ids_set:
                continue
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
        norm_semantic = self._normalize_scores(semantic_dict) if len(semantic_dict) > 1 else semantic_dict

        # 5. Score Combination: final_score = alpha * bm25_score + (1 - alpha) * semantic_score
        fused_scores: Dict[int, Tuple[float, float, float]] = {}  # chunk_id -> (fused, bm25, sem)
        for cid in all_chunk_ids:
            b_score = norm_bm25.get(cid, 0.0)
            s_score = norm_semantic.get(cid, 0.0)
            combined = (alpha * b_score) + ((1.0 - alpha) * s_score)
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

            # Post-verification of filters
            if not QueryParser.matches_filters(
                parsed,
                doc_path=doc.path,
                doc_extension=doc.extension,
                doc_mtime=doc.mtime,
                chunk_code_type=chunk.code_type,
                chunk_symbol_name=chunk.symbol_name
            ):
                continue

            if is_code and not chunk.code_type:
                continue

            raw_combined, b_s, s_s = fused_scores[cid]

            # Recency Boost (+0.08 for last 7 days, +0.04 for last 30 days)
            age_seconds = now_ts - doc.mtime
            recency_boost = 0.08 if age_seconds <= 7 * 86400 else (0.04 if age_seconds <= 30 * 86400 else 0.0)
            final_boosted = min(1.0, raw_combined + recency_boost)

            snippet = self._extract_snippet(chunk.text, effective_query)

            all_results.append(SearchResult(
                chunk_id=chunk.id,
                doc_id=doc.id,
                filename=doc.filename,
                path=doc.path,
                score=final_boosted,
                raw_score=raw_combined,
                recency_boost=recency_boost,
                mtime=doc.mtime,
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
        reranked = self.reranker.rerank(all_results, effective_query)

        # Result Diversity: allow at most 2 highest-ranking chunks per document to prevent starvation
        doc_counts = defaultdict(int)
        diverse_results: List[SearchResult] = []
        for res in reranked:
            if doc_counts[res.doc_id] < 2:
                diverse_results.append(res)
                doc_counts[res.doc_id] += 1

        total_count = len(diverse_results)

        # 8. Return paginated slice
        paged_results = diverse_results[offset : offset + limit]
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

