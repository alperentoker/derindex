"""SQLite database manager for documents, chunks, symbols, and inverted index."""

import sqlite3
import logging
from pathlib import Path
from typing import Optional, List, Dict, Tuple, Any, Set
from contextlib import contextmanager

try:
    import numpy as np
    sqlite3.register_adapter(np.int64, int)
    sqlite3.register_adapter(np.int32, int)
except ImportError:
    pass

from config import config
from .models import DocumentRecord, ChunkRecord, SymbolRecord, InvertedIndexRecord

logger = logging.getLogger(__name__)


class Database:
    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or config.DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    @contextmanager
    def get_connection(self):
        conn = sqlite3.connect(
            str(self.db_path),
            timeout=30.0,
            check_same_thread=False
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.execute("PRAGMA journal_mode = WAL;")
        try:
            yield conn
            conn.commit()
        except Exception as e:
            conn.rollback()
            logger.error(f"Database error: {e}")
            raise
        finally:
            conn.close()

    def _init_db(self) -> None:
        schema_path = Path(__file__).resolve().parent / "schema.sql"
        if not schema_path.exists():
            raise FileNotFoundError(f"Schema file not found at {schema_path}")

        with open(schema_path, "r", encoding="utf-8") as f:
            schema_sql = f.read()

        with self.get_connection() as conn:
            conn.executescript(schema_sql)
        logger.info(f"Database initialized at {self.db_path}")

    # Document Operations
    def get_document_by_path(self, path: str) -> Optional[DocumentRecord]:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT id, path, filename, extension, sha256, size_bytes, mtime, indexed_at FROM documents WHERE path = ?",
                (path,)
            )
            row = cursor.fetchone()
            if row:
                return DocumentRecord(
                    id=row["id"],
                    path=row["path"],
                    filename=row["filename"],
                    extension=row["extension"],
                    sha256=row["sha256"],
                    size_bytes=row["size_bytes"],
                    mtime=row["mtime"],
                    indexed_at=row["indexed_at"]
                )
            return None

    def get_all_document_paths(self) -> Dict[str, str]:
        """Returns map of path -> sha256 for all stored documents."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT path, sha256 FROM documents")
            return {row["path"]: row["sha256"] for row in cursor.fetchall()}

    def insert_document(self, doc: DocumentRecord) -> int:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO documents (path, filename, extension, sha256, size_bytes, mtime, indexed_at)
                VALUES (?, ?, ?, ?, ?, ?, datetime('now'))
                ON CONFLICT(path) DO UPDATE SET
                    sha256 = excluded.sha256,
                    size_bytes = excluded.size_bytes,
                    mtime = excluded.mtime,
                    indexed_at = datetime('now')
                """,
                (doc.path, doc.filename, doc.extension, doc.sha256, doc.size_bytes, doc.mtime)
            )
            return cursor.lastrowid

    def delete_document(self, doc_id: int) -> None:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            # Cascade deletes chunks, symbols, and inverted_index automatically via foreign keys
            cursor.execute("DELETE FROM documents WHERE id = ?", (doc_id,))

    def delete_document_by_path(self, path: str) -> bool:
        doc = self.get_document_by_path(path)
        if doc and doc.id is not None:
            self.delete_document(doc.id)
            return True
        return False

    # Chunk Operations
    def insert_chunks(self, chunks: List[ChunkRecord]) -> List[int]:
        if not chunks:
            return []
        chunk_ids = []
        with self.get_connection() as conn:
            cursor = conn.cursor()
            for chunk in chunks:
                cursor.execute(
                    """
                    INSERT INTO chunks (doc_id, chunk_index, text, page_number, section_title, code_type, symbol_name, start_line, end_line, token_count)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        chunk.doc_id, chunk.chunk_index, chunk.text,
                        chunk.page_number, chunk.section_title, chunk.code_type,
                        chunk.symbol_name, chunk.start_line, chunk.end_line,
                        chunk.token_count
                    )
                )
                chunk_ids.append(cursor.lastrowid)
        return chunk_ids

    def get_chunk(self, chunk_id: int) -> Optional[Tuple[ChunkRecord, DocumentRecord]]:
        chunk_id = int(chunk_id)
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT c.id as c_id, c.doc_id, c.chunk_index, c.text, c.page_number, c.section_title,
                       c.code_type, c.symbol_name, c.start_line, c.end_line, c.token_count,
                       d.id as d_id, d.path, d.filename, d.extension, d.sha256, d.size_bytes, d.mtime, d.indexed_at
                FROM chunks c
                JOIN documents d ON c.doc_id = d.id
                WHERE c.id = ?
                """,
                (chunk_id,)
            )
            row = cursor.fetchone()
            if not row:
                return None

            chunk = ChunkRecord(
                id=row["c_id"],
                doc_id=row["doc_id"],
                chunk_index=row["chunk_index"],
                text=row["text"],
                page_number=row["page_number"],
                section_title=row["section_title"],
                code_type=row["code_type"],
                symbol_name=row["symbol_name"],
                start_line=row["start_line"],
                end_line=row["end_line"],
                token_count=row["token_count"]
            )
            doc = DocumentRecord(
                id=row["d_id"],
                path=row["path"],
                filename=row["filename"],
                extension=row["extension"],
                sha256=row["sha256"],
                size_bytes=row["size_bytes"],
                mtime=row["mtime"],
                indexed_at=row["indexed_at"]
            )
            return chunk, doc

    def get_chunks_by_ids(self, chunk_ids: List[int]) -> Dict[int, Tuple[ChunkRecord, DocumentRecord]]:
        if not chunk_ids:
            return {}
        chunk_ids = [int(cid) for cid in chunk_ids]
        placeholders = ",".join("?" for _ in chunk_ids)
        result = {}
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                f"""
                SELECT c.id as c_id, c.doc_id, c.chunk_index, c.text, c.page_number, c.section_title,
                       c.code_type, c.symbol_name, c.start_line, c.end_line, c.token_count,
                       d.id as d_id, d.path, d.filename, d.extension, d.sha256, d.size_bytes, d.mtime, d.indexed_at
                FROM chunks c
                JOIN documents d ON c.doc_id = d.id
                WHERE c.id IN ({placeholders})
                """,
                chunk_ids
            )
            for row in cursor.fetchall():
                chunk = ChunkRecord(
                    id=row["c_id"],
                    doc_id=row["doc_id"],
                    chunk_index=row["chunk_index"],
                    text=row["text"],
                    page_number=row["page_number"],
                    section_title=row["section_title"],
                    code_type=row["code_type"],
                    symbol_name=row["symbol_name"],
                    start_line=row["start_line"],
                    end_line=row["end_line"],
                    token_count=row["token_count"]
                )
                doc = DocumentRecord(
                    id=row["d_id"],
                    path=row["path"],
                    filename=row["filename"],
                    extension=row["extension"],
                    sha256=row["sha256"],
                    size_bytes=row["size_bytes"],
                    mtime=row["mtime"],
                    indexed_at=row["indexed_at"]
                )
                result[chunk.id] = (chunk, doc)
        return result

    # Symbol Operations
    def insert_symbols(self, symbols: List[SymbolRecord]) -> None:
        if not symbols:
            return
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.executemany(
                "INSERT INTO symbols (doc_id, chunk_id, name, kind, line_number) VALUES (?, ?, ?, ?, ?)",
                [(s.doc_id, s.chunk_id, s.name, s.kind, s.line_number) for s in symbols]
            )

    def find_symbols(self, query: str, limit: int = 20) -> List[Dict[str, Any]]:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            pattern = f"%{query}%"
            cursor.execute(
                """
                SELECT s.id, s.name, s.kind, s.line_number, s.chunk_id,
                       d.path, d.filename, c.text, c.start_line, c.end_line
                FROM symbols s
                JOIN documents d ON s.doc_id = d.id
                LEFT JOIN chunks c ON s.chunk_id = c.id
                WHERE s.name LIKE ?
                ORDER BY (s.name = ?) DESC, length(s.name) ASC
                LIMIT ?
                """,
                (pattern, query, limit)
            )
            results = []
            for row in cursor.fetchall():
                results.append({
                    "symbol_id": row["id"],
                    "name": row["name"],
                    "kind": row["kind"],
                    "line_number": row["line_number"],
                    "chunk_id": row["chunk_id"],
                    "path": row["path"],
                    "filename": row["filename"],
                    "code_snippet": row["text"][:300] if row["text"] else "",
                    "start_line": row["start_line"],
                    "end_line": row["end_line"]
                })
            return results

    def find_chunk_ids_by_filename(self, query: str, limit: int = 20) -> List[Tuple[int, float]]:
        """Finds chunk IDs of documents whose filename matches query."""
        if not query or not query.strip():
            return []
        q_clean = query.strip()
        pattern = f"%{q_clean}%"
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT c.id, d.filename
                FROM documents d
                JOIN chunks c ON c.doc_id = d.id AND c.chunk_index = 0
                WHERE d.filename LIKE ? OR d.path LIKE ?
                ORDER BY (d.filename = ?) DESC, length(d.filename) ASC
                LIMIT ?
                """,
                (pattern, pattern, q_clean, limit)
            )
            results = []
            for row in cursor.fetchall():
                cid = int(row["id"])
                fn = row["filename"].lower()
                score = 1.0 if q_clean.lower() == fn else 0.85
                results.append((cid, score))
            return results

    def find_chunk_ids_by_symbol(self, query: str, limit: int = 20) -> List[Tuple[int, float]]:
        """Finds chunk IDs of symbols matching query."""
        if not query or not query.strip():
            return []
        q_clean = query.strip()
        pattern = f"%{q_clean}%"
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT DISTINCT c.id, s.name
                FROM symbols s
                JOIN chunks c ON s.chunk_id = c.id
                WHERE s.name LIKE ?
                ORDER BY (s.name = ?) DESC, length(s.name) ASC
                LIMIT ?
                """,
                (pattern, q_clean, limit)
            )
            results = []
            for row in cursor.fetchall():
                cid = int(row["id"])
                sym = row["name"].lower()
                score = 0.95 if q_clean.lower() == sym else 0.75
                results.append((cid, score))
            return results

    # Inverted Index & Postings
    def insert_postings(self, postings: List[InvertedIndexRecord]) -> None:
        if not postings:
            return
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.executemany(
                """
                INSERT INTO inverted_index (term, doc_id, chunk_id, term_freq)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(term, chunk_id) DO UPDATE SET
                    term_freq = excluded.term_freq
                """,
                [(p.term, p.doc_id, p.chunk_id, p.term_freq) for p in postings]
            )

    def update_term_stats(self) -> None:
        """Recalculate term document frequencies across all chunks."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM term_stats")
            cursor.execute(
                """
                INSERT INTO term_stats (term, doc_freq)
                SELECT term, COUNT(DISTINCT chunk_id) as doc_freq
                FROM inverted_index
                GROUP BY term
                """
            )

    def get_postings_for_terms(self, terms: List[str]) -> List[Tuple[str, int, int, int]]:
        """Returns list of (term, doc_id, chunk_id, term_freq) for the given terms."""
        if not terms:
            return []
        placeholders = ",".join("?" for _ in terms)
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                f"SELECT term, doc_id, chunk_id, term_freq FROM inverted_index WHERE term IN ({placeholders})",
                terms
            )
            return cursor.fetchall()

    def get_postings_for_terms_prefix(self, prefix: str, limit: int = 100) -> List[Tuple[str, int, int, int]]:
        """Returns list of (term, doc_id, chunk_id, term_freq) for terms starting with prefix."""
        if not prefix or len(prefix) < 2:
            return []
        pattern = f"{prefix}%"
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT term, doc_id, chunk_id, term_freq FROM inverted_index WHERE term LIKE ? LIMIT ?",
                (pattern, limit)
            )
            return cursor.fetchall()

    def get_term_doc_frequencies(self, terms: List[str]) -> Dict[str, int]:
        """Returns document frequency for each term."""
        if not terms:
            return {}
        placeholders = ",".join("?" for _ in terms)
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                f"SELECT term, doc_freq FROM term_stats WHERE term IN ({placeholders})",
                terms
            )
            return {row["term"]: row["doc_freq"] for row in cursor.fetchall()}

    def get_bm25_corpus_stats(self) -> Tuple[int, float, Dict[int, int]]:
        """
        Returns:
            total_chunks: N (total number of chunks in the collection)
            avgdl: average chunk length in tokens
            chunk_lengths: map of chunk_id -> token_count
        """
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id, token_count FROM chunks")
            rows = cursor.fetchall()
            if not rows:
                return 0, 0.0, {}

            total_chunks = len(rows)
            chunk_lengths = {row["id"]: row["token_count"] for row in rows}
            total_tokens = sum(chunk_lengths.values())
            avgdl = total_tokens / total_chunks if total_chunks > 0 else 0.0
            return total_chunks, avgdl, chunk_lengths

    def get_stats(self) -> Dict[str, Any]:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM documents")
            doc_count = cursor.fetchone()[0]

            cursor.execute("SELECT COUNT(*) FROM chunks")
            chunk_count = cursor.fetchone()[0]

            cursor.execute("SELECT COUNT(DISTINCT term) FROM inverted_index")
            term_count = cursor.fetchone()[0]

            cursor.execute("SELECT COUNT(*) FROM symbols")
            symbol_count = cursor.fetchone()[0]

            cursor.execute("SELECT SUM(token_count) FROM chunks")
            total_tokens = cursor.fetchone()[0] or 0

            # File types breakdown
            cursor.execute("SELECT extension, COUNT(*) FROM documents GROUP BY extension")
            extensions = {row[0]: row[1] for row in cursor.fetchall()}

            db_size_bytes = self.db_path.stat().st_size if self.db_path.exists() else 0

            return {
                "total_documents": doc_count,
                "total_chunks": chunk_count,
                "total_unique_terms": term_count,
                "total_symbols": symbol_count,
                "total_tokens": total_tokens,
                "database_size_mb": round(db_size_bytes / (1024 * 1024), 2),
                "file_types": extensions,
            }

    def clear_all(self) -> None:
        """Completely wipe the database for full rebuild."""
        with self.get_connection() as conn:
            conn.execute("DELETE FROM documents")
            conn.execute("DELETE FROM chunks")
            conn.execute("DELETE FROM symbols")
            conn.execute("DELETE FROM inverted_index")
            conn.execute("DELETE FROM term_stats")
            conn.execute("DELETE FROM doc_stats")
            conn.execute("VACUUM")
        logger.info("Database completely cleared and vacuumed.")
