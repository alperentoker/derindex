"""Crawler and incremental indexing manager with SHA-256 change detection."""

import os
import hashlib
import logging
from pathlib import Path
from typing import List, Dict, Set, Tuple, Optional
from datetime import datetime

from config import config
from app.database.db import Database
from app.database.models import DocumentRecord, ChunkRecord, SymbolRecord
from app.parsers import get_parser_for_file
from app.indexing.inverted_index import InvertedIndex
from app.embeddings.embedder import LocalEmbedder
from app.embeddings.vector_store import VectorStore

logger = logging.getLogger(__name__)


def compute_sha256(file_path: Path) -> str:
    """Computes SHA-256 hash of a file in streaming chunks."""
    hasher = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest()


class Crawler:
    def __init__(
        self,
        db: Optional[Database] = None,
        vector_store: Optional[VectorStore] = None,
        embedder: Optional[LocalEmbedder] = None
    ):
        self.db = db or Database()
        self.vector_store = vector_store or VectorStore()
        self.embedder = embedder or LocalEmbedder.get_instance()
        self.inverted_index = InvertedIndex(self.db)

    def is_safe_and_supported(self, file_path: Path) -> bool:
        """Validates that file is not inside sensitive dirs, matches extensions, and is under size limit."""
        # Check parent directory names against blacklist
        for part in file_path.parts:
            if part in config.IGNORED_DIRS:
                return False

        # Check filename against sensitive patterns
        name_lower = file_path.name.lower()
        for pattern in config.IGNORED_FILE_PATTERNS:
            if pattern in name_lower:
                return False

        # Check extension (if universal fallback is disabled)
        if not getattr(config, "ENABLE_UNIVERSAL_FALLBACK", False):
            if file_path.suffix.lower() not in config.SUPPORTED_EXTENSIONS:
                return False

        # Check file existence and size
        try:
            if file_path.is_file() and file_path.stat().st_size <= config.MAX_FILE_SIZE_BYTES:
                return True
        except (PermissionError, FileNotFoundError):
            return False

        return False

    def scan_directory(self, target_dir: Path) -> List[Path]:
        """Recursively scans directory and returns all indexable files."""
        matched_files: List[Path] = []
        target_dir = target_dir.resolve()

        if not target_dir.exists() or not target_dir.is_dir():
            logger.error(f"Directory {target_dir} does not exist or is not a directory.")
            return []

        for root, dirs, files in os.walk(str(target_dir), topdown=True, followlinks=False):
            # Prune ignored directories in-place
            dirs[:] = [d for d in dirs if d not in config.IGNORED_DIRS and not d.startswith(".")]

            for filename in files:
                f_path = Path(root) / filename
                if self.is_safe_and_supported(f_path):
                    matched_files.append(f_path)

        return matched_files

    def index_directory(self, target_dir: Path, progress_callback=None) -> Dict[str, int]:
        """
        Scans target_dir and incrementally indexes new/modified files,
        and removes files that have been deleted.
        """
        stats = {"scanned": 0, "indexed": 0, "skipped": 0, "deleted": 0, "failed": 0}
        target_dir = target_dir.resolve()
        candidate_files = self.scan_directory(target_dir)
        stats["scanned"] = len(candidate_files)

        # Get existing paths in DB that start with target_dir
        existing_docs = self.db.get_all_document_paths()
        dir_prefix = str(target_dir)
        tracked_paths = {p: h for p, h in existing_docs.items() if p.startswith(dir_prefix)}

        current_candidate_paths = set()

        for idx, f_path in enumerate(candidate_files, start=1):
            p_str = str(f_path.resolve())
            current_candidate_paths.add(p_str)

            try:
                current_sha = compute_sha256(f_path)
            except Exception as e:
                logger.warning(f"Failed to read/hash {f_path}: {e}")
                stats["failed"] += 1
                continue

            # Check if unchanged
            if p_str in tracked_paths and tracked_paths[p_str] == current_sha:
                stats["skipped"] += 1
                if progress_callback:
                    progress_callback(idx, len(candidate_files), f_path.name, "unchanged")
                continue

            # File is new or modified
            try:
                success = self.index_file(f_path, sha256_hash=current_sha)
                if success:
                    stats["indexed"] += 1
                    if progress_callback:
                        progress_callback(idx, len(candidate_files), f_path.name, "indexed")
                else:
                    stats["skipped"] += 1
            except Exception as e:
                logger.error(f"Error indexing {f_path}: {e}")
                stats["failed"] += 1

        # Check for deleted files
        for tracked_p in tracked_paths:
            if tracked_p not in current_candidate_paths:
                if not Path(tracked_p).exists():
                    self.remove_file(tracked_p)
                    stats["deleted"] += 1

        # Recalculate corpus-level term stats after batch indexing
        self.db.update_term_stats()
        return stats

    def index_file(self, file_path: Path, sha256_hash: Optional[str] = None) -> bool:
        """
        Parses, chunks, indexes, and computes embeddings for a single file.
        Updates inverted index and vector store.
        """
        file_path = file_path.resolve()
        if not self.is_safe_and_supported(file_path):
            return False

        parser = get_parser_for_file(file_path)
        if not parser:
            logger.debug(f"No parser available for {file_path.name}")
            return False

        sha = sha256_hash or compute_sha256(file_path)
        stat = file_path.stat()
        path_str = str(file_path)

        # 1. Parse document
        parsed_doc = parser.parse(file_path)
        if not parsed_doc.chunks:
            logger.debug(f"No content chunks extracted from {file_path.name}")
            return False

        # 2. Check and remove old document if modifying
        old_doc = self.db.get_document_by_path(path_str)
        if old_doc and old_doc.id is not None:
            # Delete old chunks and vectors
            with self.db.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT id FROM chunks WHERE doc_id = ?", (old_doc.id,))
                old_chunk_ids = {row[0] for row in cursor.fetchall()}
            self.vector_store.delete_vectors(old_chunk_ids)
            self.db.delete_document(old_doc.id)

        # 3. Insert Document Record
        doc_record = DocumentRecord(
            id=None,
            path=path_str,
            filename=file_path.name,
            extension=file_path.suffix.lower(),
            sha256=sha,
            size_bytes=stat.st_size,
            mtime=stat.st_mtime,
            indexed_at=datetime.utcnow().isoformat()
        )
        doc_id = self.db.insert_document(doc_record)

        # 4. Prepare & Insert Chunks
        chunk_records: List[ChunkRecord] = []
        chunk_texts: List[str] = []

        for idx, pc in enumerate(parsed_doc.chunks):
            t_count = self.inverted_index.tokenizer.tokenize(pc.text)
            chunk_rec = ChunkRecord(
                id=None,
                doc_id=doc_id,
                chunk_index=idx,
                text=pc.text,
                page_number=pc.page_number,
                section_title=pc.section_title,
                code_type=pc.code_type,
                symbol_name=pc.symbol_name,
                start_line=pc.start_line,
                end_line=pc.end_line,
                token_count=max(1, len(t_count))
            )
            chunk_records.append(chunk_rec)
            chunk_texts.append(pc.text)

        chunk_ids = self.db.insert_chunks(chunk_records)

        # 5. Insert Symbols (mapped to chunks)
        all_symbols: List[SymbolRecord] = []
        for idx, pc in enumerate(parsed_doc.chunks):
            cid = chunk_ids[idx]
            for sym in pc.symbols:
                all_symbols.append(SymbolRecord(
                    id=None,
                    doc_id=doc_id,
                    chunk_id=cid,
                    name=sym.name,
                    kind=sym.kind,
                    line_number=sym.line_number
                ))
        self.db.insert_symbols(all_symbols)

        # 6. Build & Insert Inverted Index Postings
        all_postings = []
        for idx, pc in enumerate(parsed_doc.chunks):
            cid = chunk_ids[idx]
            postings = self.inverted_index.index_chunk(doc_id=doc_id, chunk_id=cid, text=pc.text)
            all_postings.extend(postings)

        self.db.insert_postings(all_postings)

        # 7. Compute & Save Embeddings
        try:
            vectors = self.embedder.embed_texts(chunk_texts)
            if len(vectors) == len(chunk_ids):
                self.vector_store.add_vectors(chunk_ids, vectors)
        except Exception as e:
            logger.warning(f"Failed to generate embeddings for {file_path.name}: {e}")

        return True

    def remove_file(self, file_path_str: str) -> None:
        """Removes a deleted file from DB, cascades inverted index, and updates vector store."""
        doc = self.db.get_document_by_path(file_path_str)
        if not doc or doc.id is None:
            return

        # Fetch chunk IDs to delete from vector store
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id FROM chunks WHERE doc_id = ?", (doc.id,))
            chunk_ids = {row[0] for row in cursor.fetchall()}

        self.vector_store.delete_vectors(chunk_ids)
        self.db.delete_document(doc.id)
        logger.info(f"Removed file from index: {file_path_str}")

    def rebuild_all(self, target_dirs: List[Path], progress_callback=None) -> Dict[str, int]:
        """Clears database and vector store completely, then reindexes all specified directories."""
        self.db.clear_all()
        self.vector_store.clear()
        combined_stats = {"scanned": 0, "indexed": 0, "skipped": 0, "deleted": 0, "failed": 0}

        for d in target_dirs:
            st = self.index_directory(d, progress_callback=progress_callback)
            for k in combined_stats:
                combined_stats[k] += st.get(k, 0)

        return combined_stats
