"""SQLite database manager for documents, chunks, symbols, and inverted index. Thread-safe."""

import sqlite3
import logging
import threading
from pathlib import Path
from typing import Optional, List, Dict, Tuple, Any, Set
from contextlib import contextmanager

try:
    import numpy as np
    sqlite3.register_adapter(np.int64, int)
    sqlite3.register_adapter(np.int32, int)
    sqlite3.register_adapter(np.float64, float)
    sqlite3.register_adapter(np.float32, float)
except (ImportError, AttributeError):
    pass

from config import config
from .models import DocumentRecord, ChunkRecord, SymbolRecord, InvertedIndexRecord

logger = logging.getLogger(__name__)


class Database:
    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or config.DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._write_lock = threading.Lock()
        self._init_db()

    @contextmanager
    def get_connection(self, exclusive: bool = False):
        """Context manager for DB connections. Use exclusive=True for write operations."""
        if exclusive:
            self._write_lock.acquire()
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
            if exclusive:
                self._write_lock.release()

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
        with self.get_connection(exclusive=True) as conn:
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
            # BUG-03 fix: lastrowid is unreliable after UPSERT, always SELECT the correct id
            cursor.execute("SELECT id FROM documents WHERE path = ?", (doc.path,))
            row = cursor.fetchone()
            return row["id"]

    def delete_document(self, doc_id: int) -> None:
        with self.get_connection(exclusive=True) as conn:
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
        with self.get_connection(exclusive=True) as conn:
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

    def save_indexed_document_atomic(
        self,
        doc: DocumentRecord,
        chunks: List[ChunkRecord],
        chunk_symbols: List[List[SymbolRecord]],
        chunk_postings: List[List[InvertedIndexRecord]]
    ) -> Tuple[int, List[int]]:
        """
        Atomically saves document, its chunks, associated symbols, and inverted postings
        within a single exclusive transaction. If any step fails, entire operation rolls back.
        """
        with self.get_connection(exclusive=True) as conn:
            cursor = conn.cursor()
            # 1. Document UPSERT
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
            cursor.execute("SELECT id FROM documents WHERE path = ?", (doc.path,))
            doc_id = cursor.fetchone()["id"]

            # Clear any existing chunks for this document to prevent duplicate chunk accumulation
            cursor.execute("DELETE FROM chunks WHERE doc_id = ?", (doc_id,))

            # 2. Insert Chunks

            chunk_ids = []
            for chunk in chunks:
                cursor.execute(
                    """
                    INSERT INTO chunks (doc_id, chunk_index, text, page_number, section_title, code_type, symbol_name, start_line, end_line, token_count)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        doc_id, chunk.chunk_index, chunk.text,
                        chunk.page_number, chunk.section_title, chunk.code_type,
                        chunk.symbol_name, chunk.start_line, chunk.end_line,
                        chunk.token_count
                    )
                )
                chunk_ids.append(cursor.lastrowid)

            # 3. Insert Symbols
            for idx, sym_list in enumerate(chunk_symbols):
                cid = chunk_ids[idx]
                for sym in sym_list:
                    cursor.execute(
                        "INSERT INTO symbols (doc_id, chunk_id, name, kind, line_number) VALUES (?, ?, ?, ?, ?)",
                        (doc_id, cid, sym.name, sym.kind, sym.line_number)
                    )

            # 4. Insert Postings
            for idx, post_list in enumerate(chunk_postings):
                cid = chunk_ids[idx]
                for p in post_list:
                    cursor.execute(
                        """
                        INSERT INTO inverted_index (term, doc_id, chunk_id, term_freq)
                        VALUES (?, ?, ?, ?)
                        ON CONFLICT(term, chunk_id) DO UPDATE SET
                            term_freq = excluded.term_freq
                        """,
                        (p.term, doc_id, cid, p.term_freq)
                    )

            return doc_id, chunk_ids

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

    def get_candidate_chunk_ids_by_filters(
        self,
        extensions: Optional[Set[str]] = None,
        exclude_extensions: Optional[Set[str]] = None,
        path_pattern: Optional[str] = None,
        allowed_type_extensions: Optional[Set[str]] = None,
        code_only: bool = False,
        after_timestamp: Optional[float] = None,
        before_timestamp: Optional[float] = None,
        symbol_filter: Optional[str] = None,
        limit: Optional[int] = None
    ) -> List[int]:
        """Returns ordered list of chunk IDs (by mtime DESC) satisfying the given filters."""
        query_parts = ["SELECT c.id FROM chunks c JOIN documents d ON c.doc_id = d.id WHERE 1=1"]
        params: List[Any] = []

        if extensions:
            placeholders = ",".join("?" for _ in extensions)
            query_parts.append(f"AND LOWER(d.extension) IN ({placeholders})")
            params.extend([ext.lower() for ext in extensions])

        if exclude_extensions:
            placeholders = ",".join("?" for _ in exclude_extensions)
            query_parts.append(f"AND LOWER(d.extension) NOT IN ({placeholders})")
            params.extend([ext.lower() for ext in exclude_extensions])

        if path_pattern:
            escaped = self._escape_like(path_pattern.replace("\\", "/").lower())
            query_parts.append("AND LOWER(d.path) LIKE ? ESCAPE '\\'")
            params.append(f"%{escaped}%")

        if code_only:
            query_parts.append("AND c.code_type IS NOT NULL")
        elif allowed_type_extensions:
            placeholders = ",".join("?" for _ in allowed_type_extensions)
            query_parts.append(f"AND LOWER(d.extension) IN ({placeholders})")
            params.extend([ext.lower() for ext in allowed_type_extensions])

        if after_timestamp is not None:
            query_parts.append("AND d.mtime >= ?")
            params.append(after_timestamp)

        if before_timestamp is not None:
            query_parts.append("AND d.mtime <= ?")
            params.append(before_timestamp)

        if symbol_filter:
            escaped_sym = self._escape_like(symbol_filter.lower())
            query_parts.append("AND LOWER(c.symbol_name) LIKE ? ESCAPE '\\'")
            params.append(f"%{escaped_sym}%")

        query_parts.append("ORDER BY d.mtime DESC")
        if limit:
            query_parts.append(f"LIMIT {int(limit)}")

        sql = " ".join(query_parts)
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(sql, params)
            return [row["id"] for row in cursor.fetchall()]


    # Symbol Operations
    def insert_symbols(self, symbols: List[SymbolRecord]) -> None:
        if not symbols:
            return
        with self.get_connection(exclusive=True) as conn:
            cursor = conn.cursor()
            cursor.executemany(
                "INSERT INTO symbols (doc_id, chunk_id, name, kind, line_number) VALUES (?, ?, ?, ?, ?)",
                [(s.doc_id, s.chunk_id, s.name, s.kind, s.line_number) for s in symbols]
            )

    @staticmethod
    def _escape_like(query: str) -> str:
        """Escapes LIKE wildcard characters (%, _, \\) for safe pattern matching."""
        return query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")

    def find_symbols(self, query: str, limit: int = 20) -> List[Dict[str, Any]]:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            pattern = f"%{self._escape_like(query)}%"
            cursor.execute(
                """
                SELECT s.id, s.name, s.kind, s.line_number, s.chunk_id,
                       d.path, d.filename, c.text, c.start_line, c.end_line
                FROM symbols s
                JOIN documents d ON s.doc_id = d.id
                LEFT JOIN chunks c ON s.chunk_id = c.id
                WHERE s.name LIKE ? ESCAPE '\\'
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
        pattern = f"%{self._escape_like(q_clean)}%"
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT c.id, d.filename
                FROM documents d
                JOIN chunks c ON c.doc_id = d.id AND c.chunk_index = 0
                WHERE d.filename LIKE ? ESCAPE '\\' OR d.path LIKE ? ESCAPE '\\'
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
        pattern = f"%{self._escape_like(q_clean)}%"
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT DISTINCT c.id, s.name
                FROM symbols s
                JOIN chunks c ON s.chunk_id = c.id
                WHERE s.name LIKE ? ESCAPE '\\'
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
        with self.get_connection(exclusive=True) as conn:
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
        with self.get_connection(exclusive=True) as conn:
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

    def update_term_stats_for_terms(self, terms: List[str]) -> None:
        """Incrementally recalculates term document frequencies for a specific subset of terms."""
        if not terms:
            return
        unique_terms = list(set(terms))
        placeholders = ",".join("?" for _ in unique_terms)
        with self.get_connection(exclusive=True) as conn:
            cursor = conn.cursor()
            cursor.execute(f"DELETE FROM term_stats WHERE term IN ({placeholders})", unique_terms)
            cursor.execute(
                f"""
                INSERT INTO term_stats (term, doc_freq)
                SELECT term, COUNT(DISTINCT chunk_id) as doc_freq
                FROM inverted_index
                WHERE term IN ({placeholders})
                GROUP BY term
                """,
                unique_terms
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

    def get_bm25_corpus_summary(self) -> Tuple[int, float]:
        """
        Fast O(1) SQL aggregate returning total chunks count and average chunk length in tokens.
        Avoids loading entire corpus into Python RAM.
        """
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*), COALESCE(AVG(token_count), 0.0) FROM chunks")
            row = cursor.fetchone()
            if not row or row[0] == 0:
                return 0, 0.0
            return int(row[0]), float(row[1])

    def get_chunk_lengths(self, chunk_ids: List[int]) -> Dict[int, int]:
        """Fetches token counts ONLY for requested matching chunk IDs."""
        if not chunk_ids:
            return {}
        chunk_ids = [int(cid) for cid in chunk_ids]
        placeholders = ",".join("?" for _ in chunk_ids)
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                f"SELECT id, token_count FROM chunks WHERE id IN ({placeholders})",
                chunk_ids
            )
            return {row["id"]: row["token_count"] for row in cursor.fetchall()}

    def get_bm25_corpus_stats(self) -> Tuple[int, float, Dict[int, int]]:
        """
        Backwards-compatible corpus stats.
        """
        total_chunks, avgdl = self.get_bm25_corpus_summary()
        return total_chunks, avgdl, {}

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
        with self.get_connection(exclusive=True) as conn:
            # CASCADE handles chunks, symbols, inverted_index, doc_stats
            conn.execute("DELETE FROM documents")
            conn.execute("DELETE FROM term_stats")
            conn.execute("VACUUM")
        logger.info("Database completely cleared and vacuumed.")
