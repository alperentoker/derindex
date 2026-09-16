"""PDF parser with page-level text extraction and page number preservation."""

import logging
from pathlib import Path
from typing import List
from pypdf import PdfReader
from .base import BaseParser, ParsedDocument, ParsedChunk
from .chunker import SmartChunker

logger = logging.getLogger(__name__)


class PDFParser(BaseParser):
    def __init__(self):
        self.chunker = SmartChunker()

    def can_parse(self, file_path: Path) -> bool:
        return file_path.suffix.lower() == ".pdf"

    def parse(self, file_path: Path) -> ParsedDocument:
        chunks: List[ParsedChunk] = []
        total_pages = 0
        doc_title = file_path.stem

        try:
            reader = PdfReader(str(file_path))
            total_pages = len(reader.pages)

            if reader.metadata and reader.metadata.title:
                doc_title = reader.metadata.title

            for page_idx, page in enumerate(reader.pages, start=1):
                try:
                    page_text = page.extract_text() or ""
                    page_text = page_text.strip()
                    if not page_text:
                        continue

                    page_chunks = self.chunker.chunk_text(
                        text=page_text,
                        page_number=page_idx,
                        section_title=f"Page {page_idx}",
                        start_line_offset=1
                    )
                    chunks.extend(page_chunks)
                except Exception as page_err:
                    logger.warning(f"Error extracting page {page_idx} from {file_path.name}: {page_err}")
                    continue

        except Exception as e:
            logger.error(f"Failed to read PDF file {file_path}: {e}")

        return ParsedDocument(
            path=str(file_path.resolve()),
            filename=file_path.name,
            extension=".pdf",
            title=doc_title,
            chunks=chunks,
            symbols=[],
            metadata={"total_pages": total_pages}
        )
