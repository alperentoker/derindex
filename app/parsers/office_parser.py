"""Parser for Microsoft Office (DOCX, PPTX, XLSX), OpenDocument (ODT, ODS, ODP), and RTF."""

import re
import logging
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import List, Optional

from .base import BaseParser, ParsedDocument, ParsedChunk
from .chunker import SmartChunker

logger = logging.getLogger(__name__)

# XML Namespaces commonly found in OpenXML formats
WORD_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
DRAWING_NS = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
SPREADSHEET_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
ODF_TEXT_NS = "{urn:oasis:names:tc:opendocument:xmlns:text:1.0}"


class OfficeParser(BaseParser):
    SUPPORTED_EXTENSIONS = {
        ".docx", ".doc",
        ".pptx", ".ppt",
        ".xlsx", ".xls",
        ".odt", ".ods", ".odp",
        ".rtf",
    }

    def __init__(self):
        self.chunker = SmartChunker(chunk_size=400, chunk_overlap=50)

    def can_parse(self, file_path: Path) -> bool:
        return file_path.suffix.lower() in self.SUPPORTED_EXTENSIONS

    def parse(self, file_path: Path) -> ParsedDocument:
        ext = file_path.suffix.lower()
        title = file_path.stem
        chunks: List[ParsedChunk] = []
        metadata = {"file_type": ext}

        try:
            if ext == ".docx":
                chunks = self._parse_docx(file_path)
            elif ext == ".pptx":
                chunks = self._parse_pptx(file_path)
            elif ext == ".xlsx":
                chunks = self._parse_xlsx(file_path)
            elif ext in {".odt", ".ods", ".odp"}:
                chunks = self._parse_opendocument(file_path)
            elif ext == ".rtf":
                chunks = self._parse_rtf(file_path)
            elif ext in {".doc", ".ppt", ".xls"}:
                chunks = self._parse_legacy_office(file_path)
        except Exception as e:
            logger.warning(f"Error parsing office document {file_path.name}: {e}")

        return ParsedDocument(
            path=str(file_path.resolve()),
            filename=file_path.name,
            extension=ext,
            title=title,
            chunks=chunks,
            symbols=[],
            metadata=metadata
        )

    def _parse_docx(self, file_path: Path) -> List[ParsedChunk]:
        """Parses Word .docx using python-docx if available, with built-in zipfile fallback."""
        # Try python-docx
        try:
            import docx
            doc = docx.Document(str(file_path))
            paragraphs = []
            for p in doc.paragraphs:
                text = p.text.strip()
                if text:
                    paragraphs.append(text)
            for table in doc.tables:
                for row in table.rows:
                    row_text = " | ".join(cell.text.strip() for cell in row.cells if cell.text.strip())
                    if row_text:
                        paragraphs.append(row_text)

            full_text = "\n\n".join(paragraphs)
            return self.chunker.chunk_text(full_text, section_title=file_path.stem)
        except Exception:
            pass

        # Fallback to direct zipfile XML parsing
        try:
            with zipfile.ZipFile(file_path) as z:
                xml_content = z.read("word/document.xml")
            tree = ET.fromstring(xml_content)
            paragraphs = []
            for p in tree.iter(f"{WORD_NS}p"):
                texts = [t.text for t in p.iter(f"{WORD_NS}t") if t.text]
                p_text = "".join(texts).strip()
                if p_text:
                    paragraphs.append(p_text)
            full_text = "\n\n".join(paragraphs)
            return self.chunker.chunk_text(full_text, section_title=file_path.stem)
        except Exception as e:
            logger.debug(f"Zipfile XML fallback for {file_path.name} failed: {e}")
            return []

    def _parse_pptx(self, file_path: Path) -> List[ParsedChunk]:
        """Parses PowerPoint .pptx slides and text boxes with slide number preservation."""
        all_chunks: List[ParsedChunk] = []
        try:
            with zipfile.ZipFile(file_path) as z:
                # Find all slide xml files
                slide_names = sorted(
                    [n for n in z.namelist() if re.match(r'ppt/slides/slide\d+\.xml', n)],
                    key=lambda x: int(re.search(r'\d+', x).group()) if re.search(r'\d+', x) else 0
                )

                for slide_idx, s_name in enumerate(slide_names, start=1):
                    slide_xml = z.read(s_name)
                    tree = ET.fromstring(slide_xml)
                    slide_texts = []
                    for t in tree.iter(f"{DRAWING_NS}t"):
                        if t.text and t.text.strip():
                            slide_texts.append(t.text.strip())

                    if slide_texts:
                        content = "\n".join(slide_texts)
                        chunks = self.chunker.chunk_text(
                            content,
                            page_number=slide_idx,
                            section_title=f"Slide {slide_idx}"
                        )
                        all_chunks.extend(chunks)
        except Exception as e:
            logger.debug(f"Failed to parse PPTX {file_path.name}: {e}")

        return all_chunks

    def _parse_xlsx(self, file_path: Path) -> List[ParsedChunk]:
        """Parses Excel .xlsx shared strings and cell texts."""
        try:
            with zipfile.ZipFile(file_path) as z:
                strings = []
                if "xl/sharedStrings.xml" in z.namelist():
                    tree = ET.fromstring(z.read("xl/sharedStrings.xml"))
                    for t in tree.iter(f"{SPREADSHEET_NS}t"):
                        if t.text and t.text.strip():
                            strings.append(t.text.strip())

                if strings:
                    content = "\n".join(strings)
                    return self.chunker.chunk_text(content, section_title=file_path.stem)
        except Exception as e:
            logger.debug(f"Failed to parse XLSX {file_path.name}: {e}")

        return []

    def _parse_opendocument(self, file_path: Path) -> List[ParsedChunk]:
        """Parses OpenDocument formats (.odt, .ods, .odp) via content.xml."""
        try:
            with zipfile.ZipFile(file_path) as z:
                if "content.xml" in z.namelist():
                    tree = ET.fromstring(z.read("content.xml"))
                    text_parts = []
                    for node in tree.iter():
                        if node.text and node.text.strip():
                            text_parts.append(node.text.strip())
                    content = "\n\n".join(text_parts)
                    return self.chunker.chunk_text(content, section_title=file_path.stem)
        except Exception as e:
            logger.debug(f"Failed to parse ODF {file_path.name}: {e}")

        return []

    def _parse_rtf(self, file_path: Path) -> List[ParsedChunk]:
        """Extracts text from Rich Text Format (.rtf) by stripping RTF control codes."""
        try:
            with open(file_path, "r", encoding="latin-1", errors="ignore") as f:
                raw = f.read()

            # Remove RTF control words (\word, \word123)
            clean = re.sub(r'\\[a-zA-Z]+(-?\d+)? ?', ' ', raw)
            # Remove RTF group braces
            clean = re.sub(r'[{}\\]', '', clean)
            clean = re.sub(r'\s+', ' ', clean).strip()

            if clean:
                return self.chunker.chunk_text(clean, section_title=file_path.stem)
        except Exception as e:
            logger.debug(f"Failed to parse RTF {file_path.name}: {e}")

        return []

    def _parse_legacy_office(self, file_path: Path) -> List[ParsedChunk]:
        """Extracts readable text streams from legacy binary Office files (.doc, .ppt, .xls)."""
        try:
            with open(file_path, "rb") as f:
                raw_bytes = f.read()

            # Extract unicode and ASCII word chunks
            ascii_strings = re.findall(rb'[a-zA-Z0-9_\x80-\xff.,\s\-]{5,}', raw_bytes)
            decoded_parts = []
            for b in ascii_strings:
                try:
                    dec = b.decode("utf-8", errors="ignore").strip()
                    if len(dec) >= 5 and not dec.isnumeric():
                        decoded_parts.append(dec)
                except Exception:
                    continue

            if decoded_parts:
                text = "\n".join(decoded_parts)
                return self.chunker.chunk_text(text, section_title=file_path.stem)
        except Exception as e:
            logger.debug(f"Failed legacy extraction on {file_path.name}: {e}")

        return []
