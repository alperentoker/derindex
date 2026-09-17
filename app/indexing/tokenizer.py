"""Custom tokenizer with support for standard text, Turkish characters, and code symbols."""

import re
from typing import List, Set

# Standard English and Turkish common stopwords
DEFAULT_STOPWORDS: Set[str] = {
    # English stopwords
    "a", "about", "above", "after", "again", "against", "all", "am", "an", "and",
    "any", "are", "aren't", "as", "at", "be", "because", "been", "before", "being",
    "below", "between", "both", "but", "by", "can't", "cannot", "could", "couldn't",
    "did", "didn't", "do", "does", "doesn't", "doing", "don't", "down", "during",
    "each", "few", "for", "from", "further", "had", "hadn't", "has", "hasn't",
    "have", "haven't", "having", "he", "he'd", "he'll", "he's", "her", "here",
    "here's", "hers", "herself", "him", "himself", "his", "how", "how's", "i",
    "i'd", "i'll", "i'm", "i've", "if", "in", "into", "is", "isn't", "it", "it's",
    "its", "itself", "let's", "me", "more", "most", "mustn't", "my", "myself",
    "no", "nor", "not", "of", "off", "on", "once", "only", "or", "other", "ought",
    "our", "ours", "ourselves", "out", "over", "own", "same", "shan't", "she",
    "she'd", "she'll", "she's", "should", "shouldn't", "so", "some", "such",
    "than", "that", "that's", "the", "their", "theirs", "them", "themselves",
    "then", "there", "there's", "these", "they", "they'd", "they'll", "they're",
    "they've", "this", "those", "through", "to", "too", "under", "until", "up",
    "very", "was", "wasn't", "we", "we'd", "we'll", "we're", "we've", "were",
    "weren't", "what", "what's", "when", "when's", "where", "where's", "which",
    "while", "who", "who's", "whom", "why", "why's", "with", "won't", "would",
    "wouldn't", "you", "you'd", "you'll", "you're", "you've", "your", "yours",
    # Turkish stopwords
    "ve", "ile", "de", "da", "bu", "şu", "o", "bir", "gibi", "için", "olan",
    "kadar", "daha", "çok", "ama", "fakat", "ancak", "lakin", "çünkü",
    "veya", "ya", "ise", "her", "hiç", "bazı", "tüm", "hep", "nasıl", "neden",
    "ne", "nerede", "kim", "hangi", "mi", "mı", "mu", "mü", "bunu", "şunu", "onu"
}


class Tokenizer:
    def __init__(self, remove_stopwords: bool = True, min_len: int = 2):
        self.remove_stopwords = remove_stopwords
        self.min_len = min_len
        self.stopwords = DEFAULT_STOPWORDS

    @staticmethod
    def _turkish_lower(text: str) -> str:
        """Turkish-aware case folding: İ→i, I→ı, preserving standard lower for others."""
        result = []
        for ch in text:
            if ch == 'İ':
                result.append('i')
            elif ch == 'I':
                result.append('ı')
            else:
                result.append(ch.lower())
        return ''.join(result)

    def tokenize(self, text: str, split_code_symbols: bool = True) -> List[str]:
        """
        Tokenizes text into normalized words, splitting camelCase and snake_case when applicable.
        """
        if not text:
            return []

        tokens: List[str] = []

        # If split_code_symbols is enabled, inspect original case before lowering
        # to correctly capture camelCase boundaries (e.g. MemoryManager -> Memory, Manager)
        if split_code_symbols:
            camel_matches = re.findall(r'[A-ZĞÜŞİÖÇ]?[a-zğüşıöç0-9]+|[A-ZĞÜŞİÖÇ]+(?=[A-ZĞÜŞİÖÇ][a-zğüşıöç]|\b)', text)
            for cw in camel_matches:
                low_cw = self._turkish_lower(cw).strip("_")
                if self._is_valid_token(low_cw):
                    tokens.append(low_cw)

        # Turkish-aware lowercase normalization
        normalized = self._turkish_lower(text)

        # Extract words and tokens using unicode alphanumeric pattern
        raw_tokens = re.findall(r'[a-z0-9_ğüşıöç]+', normalized)

        for token in raw_tokens:
            token = token.strip("_")
            if not token:
                continue

            if split_code_symbols:
                # If snake_case, split into parts but also keep original token
                if "_" in token:
                    sub_parts = [p for p in token.split("_") if p]
                    if len(sub_parts) > 1:
                        tokens.append(token)
                        for part in sub_parts:
                            if self._is_valid_token(part):
                                tokens.append(part)
                        continue

            if self._is_valid_token(token):
                tokens.append(token)

        # Fallback: if all candidate tokens were dropped (e.g. stopword-only query or short query),
        # keep the raw alphanumeric tokens so search queries don't result in empty searches.
        if not tokens and raw_tokens:
            for token in raw_tokens:
                token = token.strip("_")
                if token and re.search(r'[a-z0-9ğüşıöç]', token):
                    tokens.append(token)

        # Preserve order while deduplicating
        return list(dict.fromkeys(tokens))

    def _is_valid_token(self, token: str) -> bool:
        if len(token) < self.min_len:
            return False
        if self.remove_stopwords and token in self.stopwords:
            return False
        # Filter out tokens that are purely punctuation or special symbols
        if not re.search(r'[a-z0-9ğüşıöç]', token):
            return False
        return True
