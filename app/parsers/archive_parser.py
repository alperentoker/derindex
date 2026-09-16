"""Archive and E-Book parser (ZIP, TAR, GZ, EPUB) inspecting contents without disk extraction."""

import re
import tarfile
import zipfile
import logging
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import List, Dict, Any

from .base import BaseParser, ParsedDocument, ParsedChunk
from .chunker import SmartChunker

logger = logging.getLogger(__name__)


class ArchiveParser(BaseParser):
    ARCHIVE_EXTENSIONS = {
        ".zip", ".tar", ".gz", ".tgz", ".bz2", ".xz", ".epub"
    }

    def __init__(self):
        self.chunker = SmartChunker(chunk_size=400, chunk_overlap=30)

    def can_parse(self, file_path: Path) -> bool:
        return file_path.suffix.lower() in self.ARCHIVE_EXTENSIONS

    def parse(self, file_path: Path) -> ParsedDocument:
        ext = file_path.suffix.lower()
        title = file_path.stem
        metadata: Dict[str, Any] = {"file_type": ext}

        if ext == ".epub":
            chunks = self._parse_epub(file_path, metadata)
        elif ext == ".zip":
            chunks = self._parse_zip_toc(file_path, metadata)
        else:
            chunks = self._parse_tar_toc(file_path, metadata)

        return ParsedDocument(
            path=str(file_path.resolve()),
            filename=file_path.name,
            extension=ext,
            title=title,
            chunks=chunks,
            symbols=[],
            metadata=metadata
        )

    def _parse_zip_toc(self, file_path: Path, metadata: Dict[str, Any]) -> List[ParsedChunk]:
        """Inspects ZIP Table of Contents without extracting."""
        try:
            with zipfile.ZipFile(file_path) as z:
                entries = []
                for info in z.infolist()[:1000]:  # Cap at 1000 items
                    if not info.is_dir():
                        size_kb = round(info.file_size / 1024, 1)
                        entries.append(f"• {info.filename} ({size_kb} KB)")

                metadata["archived_files_count"] = len(entries)
                header = [
                    f"Arşiv Dosyası: {file_path.name}",
                    f"Toplam İçerik: {len(entries)} dosya",
                    "Arşiv İçi Dosyalar (Fihrist):",
                    *entries
                ]
                text = "\n".join(header)
                return self.chunker.chunk_text(text, section_title=file_path.name)
        except Exception as e:
            logger.debug(f"Failed to read ZIP {file_path.name}: {e}")
            return []

    def _parse_tar_toc(self, file_path: Path, metadata: Dict[str, Any]) -> List[ParsedChunk]:
        """Inspects TAR / GZ / BZ2 Table of Contents without extracting."""
        try:
            mode = "r:*"
            with tarfile.open(file_path, mode) as t:
                entries = []
                for member in t.getmembers()[:1000]:
                    if member.isfile():
                        size_kb = round(member.size / 1024, 1)
                        entries.append(f"• {member.name} ({size_kb} KB)")

                metadata["archived_files_count"] = len(entries)
                header = [
                    f"Arşiv Dosyası: {file_path.name}",
                    f"Toplam İçerik: {len(entries)} dosya",
                    "Arşiv İçi Dosyalar (Fihrist):",
                    *entries
                ]
                text = "\n".join(header)
                return self.chunker.chunk_text(text, section_title=file_path.name)
        except Exception as e:
            logger.debug(f"Failed to read TAR {file_path.name}: {e}")
            return []

    def _parse_epub(self, file_path: Path, metadata: Dict[str, Any]) -> List[ParsedChunk]:
        """Extracts text chapters and metadata from EPUB e-books."""
        all_chunks: List[ParsedChunk] = []
        try:
            with zipfile.ZipFile(file_path) as z:
                # Find all (x)html files inside EPUB
                html_files = [n for n in z.namelist() if n.lower().endswith((".xhtml", ".html", ".htm"))]
                for idx, h_name in enumerate(html_files[:50], start=1):
                    raw_html = z.read(h_name).decode("utf-8", errors="replace")
                    # Simple text extraction from HTML
                    clean_text = re.sub(r'<[^>]+>', ' ', raw_html)
                    clean_text = re.sub(r'\s+', ' ', clean_text).strip()
                    if clean_text:
                        chunks = self.chunker.chunk_text(
                            clean_text,
                            page_number=idx,
                            section_title=f"Bölüm {idx} ({Path(h_name).stem})"
                        )
                        all_chunks.extend(chunks)
        except Exception as e:
            logger.debug(f"Failed to parse EPUB {file_path.name}: {e}")

        return all_chunks
