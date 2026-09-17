"""Reranking engine using Reciprocal Rank Fusion (RRF) and metadata exact-match boosting."""

import re
from typing import List, Dict, Tuple, Optional
from app.database.models import SearchResult


class Reranker:
    def __init__(self, rrf_k: int = 60):
        self.rrf_k = rrf_k

    def rerank(
        self,
        results: List[SearchResult],
        query: str,
        boost_symbol_match: bool = True
    ) -> List[SearchResult]:
        """
        Reranks search results using:
        1. Exact phrase / title / symbol match boosts
        2. Reciprocal rank fusion weighting
        """
        if not results:
            return []

        query_lower = query.lower().strip()
        query_words = set(re.findall(r'[a-z0-9_ğüşıöç]+', query_lower))

        for item in results:
            boost = 0.0

            # Boost if symbol name matches query exactly
            if boost_symbol_match and item.symbol_name:
                sym_lower = item.symbol_name.lower()
                if sym_lower == query_lower:
                    boost += 0.35
                elif query_lower in sym_lower:
                    boost += 0.15

            # Boost if section title matches query
            if item.section:
                sec_lower = item.section.lower()
                if query_lower in sec_lower:
                    boost += 0.20

            # Boost if filename matches query
            if item.filename:
                fn_lower = item.filename.lower()
                fn_tokens = re.findall(r'[a-z0-9_ğüşıöç]+', fn_lower)
                if query_lower == fn_lower or query_lower in fn_tokens:
                    boost += 0.40
                elif query_lower in fn_lower:
                    boost += 0.20

            # Exact phrase match in snippet
            if query_lower in item.matched_snippet.lower():
                boost += 0.10

            item.score = min(1.0, item.score + boost)

        # Sort by updated score descending
        results.sort(key=lambda x: x.score, reverse=True)
        return results
