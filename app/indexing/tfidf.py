"""Custom TF-IDF Vector Space Model and Cosine Similarity implementation."""

import math
from typing import List, Dict, Tuple, Optional
from .tokenizer import Tokenizer
from app.database.db import Database


class TFIDFEngine:
    def __init__(self, db: Database, tokenizer: Optional[Tokenizer] = None):
        self.db = db
        self.tokenizer = tokenizer or Tokenizer()

    def compute_cosine_similarity(self, vec_a: Dict[str, float], vec_b: Dict[str, float]) -> float:
        """Computes cosine similarity between two sparse term weight dictionaries."""
        dot_product = sum(vec_a[t] * vec_b.get(t, 0.0) for t in vec_a)
        norm_a = math.sqrt(sum(v * v for v in vec_a.values()))
        norm_b = math.sqrt(sum(v * v for v in vec_b.values()))
        if norm_a == 0.0 or norm_b == 0.0:
            return 0.0
        return dot_product / (norm_a * norm_b)

    def score_query(self, query: str, top_k: int = 50) -> List[Tuple[int, float]]:
        """
        Calculates TF-IDF cosine similarity between query vector and matching chunks.
        """
        query_terms = self.tokenizer.tokenize(query)
        if not query_terms:
            return []

        total_chunks, _, _ = self.db.get_bm25_corpus_stats()
        if total_chunks == 0:
            return []

        unique_terms = list(set(query_terms))
        doc_freqs = self.db.get_term_doc_frequencies(unique_terms)
        raw_postings = self.db.get_postings_for_terms(unique_terms)

        # Build Query TF-IDF vector: tf * idf
        query_vec: Dict[str, float] = {}
        for term in unique_terms:
            tf = query_terms.count(term) / len(query_terms)
            df = doc_freqs.get(term, 0)
            idf = math.log((1 + total_chunks) / (1 + df)) + 1.0
            query_vec[term] = tf * idf

        # Group postings by chunk_id
        chunk_postings: Dict[int, Dict[str, int]] = {}
        for term, doc_id, chunk_id, term_freq in raw_postings:
            if chunk_id not in chunk_postings:
                chunk_postings[chunk_id] = {}
            chunk_postings[chunk_id][term] = term_freq

        # Compute cosine similarity for each candidate chunk
        results: List[Tuple[int, float]] = []
        for chunk_id, t_counts in chunk_postings.items():
            total_doc_terms = sum(t_counts.values())
            chunk_vec: Dict[str, float] = {}
            for term, count in t_counts.items():
                tf = count / total_doc_terms
                df = doc_freqs.get(term, 0)
                idf = math.log((1 + total_chunks) / (1 + df)) + 1.0
                chunk_vec[term] = tf * idf

            score = self.compute_cosine_similarity(query_vec, chunk_vec)
            if score > 0.0:
                results.append((chunk_id, score))

        results.sort(key=lambda x: x[1], reverse=True)
        return results[:top_k]
