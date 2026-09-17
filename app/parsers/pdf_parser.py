import io
import logging
from pathlib import Path
from typing import List, Optional
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

                    # Fallback to local OCR if page has almost no extracted text (scanned PDF)
                    if len(page_text) < 30:
                        ocr_text = self._try_extract_ocr_from_page(page)
                        if ocr_text:
                            page_text = f"{page_text}\n\n[OCR Sayfa Metni]:\n{ocr_text}".strip()

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

    @staticmethod
    def _try_extract_ocr_from_page(page) -> Optional[str]:
        """Attempts OCR on embedded images of a scanned PDF page if pytesseract is available."""
        try:
            if not hasattr(page, "images") or not page.images:
                return None
            import pytesseract
            from PIL import Image

            ocr_results = []
            for img_file in page.images:
                try:
                    with Image.open(io.BytesIO(img_file.data)) as img:
                        if max(img.size) > 2000:
                            img.thumbnail((2000, 2000))
                        text = pytesseract.image_to_string(img, timeout=5).strip()
                        if len(text) >= 15:
                            ocr_results.append(text[:2000])
                except Exception:
                    continue

            if ocr_results:
                return "\n".join(ocr_results)[:4000]
        except Exception:
            pass
        return None

