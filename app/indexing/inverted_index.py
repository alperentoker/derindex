"""Custom Inverted Index implementation for fast keyword and posting lookup."""

import math
from collections import Counter, defaultdict
from typing import List, Dict, Tuple, Set, Optional

from .tokenizer import Tokenizer
from app.database.db import Database
from app.database.models import InvertedIndexRecord


class InvertedIndex:
    def __init__(self, db: Database, tokenizer: Optional[Tokenizer] = None):
        self.db = db
        self.tokenizer = tokenizer or Tokenizer()

    def index_chunk(self, doc_id: int, chunk_id: int, text: str) -> List[InvertedIndexRecord]:
        """
        Tokenizes chunk text, computes term frequencies, and returns InvertedIndexRecords.
        """
        tokens = self.tokenizer.tokenize(text)
        if not tokens:
            return []

        counts = Counter(tokens)
        records: List[InvertedIndexRecord] = []
        for term, freq in counts.items():
            records.append(InvertedIndexRecord(
                term=term,
                doc_id=doc_id,
                chunk_id=chunk_id,
                term_freq=freq
            ))
        return records

    def get_postings(self, terms: List[str]) -> Dict[str, List[Tuple[int, int, int]]]:
        """
        Returns mapping: term -> list of (doc_id, chunk_id, term_freq)
        """
        raw_postings = self.db.get_postings_for_terms(terms)
        postings: Dict[str, List[Tuple[int, int, int]]] = defaultdict(list)
        for term, doc_id, chunk_id, term_freq in raw_postings:
            postings[term].append((doc_id, chunk_id, term_freq))
        return postings

    def get_document_frequencies(self, terms: List[str]) -> Dict[str, int]:
        """Returns document frequency for each term."""
        return self.db.get_term_doc_frequencies(terms)
