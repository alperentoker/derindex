"""Custom Okapi BM25 implementation for lexical relevance ranking."""

import math
from typing import List, Dict, Tuple, Optional
from config import config
from .tokenizer import Tokenizer
from app.database.db import Database


class BM25Engine:
    def __init__(
        self,
        db: Database,
        k1: float = config.BM25_K1,
        b: float = config.BM25_B,
        tokenizer: Optional[Tokenizer] = None
    ):
        self.db = db
        self.k1 = k1
        self.b = b
        self.tokenizer = tokenizer or Tokenizer()

    def compute_idf(self, doc_freq: int, total_docs: int) -> float:
        """
        Robertson-Spärck Jones IDF formula with +1 smoothing:
        IDF = ln( (N - n + 0.5) / (n + 0.5) + 1 )
        """
        if total_docs == 0:
            return 0.0
        n = doc_freq
        N = total_docs
        idf = math.log(((N - n + 0.5) / (n + 0.5)) + 1.0)
        return max(0.0, idf)

    def score_query(self, query: str, top_k: int = 100) -> List[Tuple[int, float]]:
        """
        Computes BM25 scores for all matching chunks in the corpus.
        Returns sorted list of (chunk_id, score).
        """
        query_terms = self.tokenizer.tokenize(query)
        if not query_terms:
            return []

        total_chunks, avgdl, chunk_lengths = self.db.get_bm25_corpus_stats()
        if total_chunks == 0 or avgdl == 0.0:
            return []

        unique_query_terms = list(set(query_terms))
        doc_freqs = self.db.get_term_doc_frequencies(unique_query_terms)
        raw_postings = self.db.get_postings_for_terms(unique_query_terms)

        # Fallback: if exact terms yielded no postings, check prefix matching
        if not raw_postings:
            prefix_postings = []
            for term in unique_query_terms:
                prefix_postings.extend(self.db.get_postings_for_terms_prefix(term, limit=100))
            if prefix_postings:
                raw_postings = prefix_postings
                prefix_terms = list(set(p[0] for p in raw_postings))
                prefix_dfs = self.db.get_term_doc_frequencies(prefix_terms)
                for pt in prefix_terms:
                    doc_freqs[pt] = prefix_dfs.get(pt, 1)

        # Calculate IDF for each query term
        idfs: Dict[str, float] = {}
        all_terms = list(set([p[0] for p in raw_postings] + unique_query_terms))
        for term in all_terms:
            df = doc_freqs.get(term, 0)
            idfs[term] = self.compute_idf(df, total_chunks)

        # Accumulate scores per chunk
        chunk_scores: Dict[int, float] = {}
        for term, doc_id, chunk_id, term_freq in raw_postings:
            cid = int(chunk_id)
            idf = idfs.get(term, 0.0)
            if idf <= 0.0:
                continue

            doc_len = chunk_lengths.get(cid, int(avgdl))

            # BM25 term weight
            numerator = term_freq * (self.k1 + 1.0)
            denominator = term_freq + self.k1 * (1.0 - self.b + self.b * (doc_len / avgdl))
            term_score = idf * (numerator / denominator)

            chunk_scores[cid] = chunk_scores.get(cid, 0.0) + term_score

        # Sort by score descending
        sorted_results = sorted(chunk_scores.items(), key=lambda x: x[1], reverse=True)
        return [(int(cid), s) for cid, s in sorted_results[:top_k]]
