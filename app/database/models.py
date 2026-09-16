"""Data models for documents, chunks, symbols, and search results."""

from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any


@dataclass
class DocumentRecord:
    id: Optional[int]
    path: str
    filename: str
    extension: str
    sha256: str
    size_bytes: int
    mtime: float
    indexed_at: str


@dataclass
class ChunkRecord:
    id: Optional[int]
    doc_id: int
    chunk_index: int
    text: str
    page_number: Optional[int] = None
    section_title: Optional[str] = None
    code_type: Optional[str] = None
    symbol_name: Optional[str] = None
    start_line: Optional[int] = None
    end_line: Optional[int] = None
    token_count: int = 0


@dataclass
class SymbolRecord:
    id: Optional[int]
    doc_id: int
    chunk_id: Optional[int]
    name: str
    kind: str  # function, class, method, struct, import
    line_number: int


@dataclass
class InvertedIndexRecord:
    term: str
    doc_id: int
    chunk_id: int
    term_freq: int


@dataclass
class SearchResult:
    chunk_id: int
    doc_id: int
    filename: str
    path: str
    score: float
    matched_snippet: str
    page_number: Optional[int] = None
    section: Optional[str] = None
    code_type: Optional[str] = None
    symbol_name: Optional[str] = None
    start_line: Optional[int] = None
    end_line: Optional[int] = None
    bm25_score: float = 0.0
    semantic_score: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "chunk_id": self.chunk_id,
            "doc_id": self.doc_id,
            "filename": self.filename,
            "path": self.path,
            "score": round(self.score, 4),
            "bm25_score": round(self.bm25_score, 4),
            "semantic_score": round(self.semantic_score, 4),
            "matched_snippet": self.matched_snippet,
            "page_number": self.page_number,
            "section": self.section,
            "code_type": self.code_type,
            "symbol_name": self.symbol_name,
            "start_line": self.start_line,
            "end_line": self.end_line,
        }
