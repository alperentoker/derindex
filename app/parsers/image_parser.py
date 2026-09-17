"""Image parser for extracting EXIF metadata, SVG text, and contextual tags."""

import re
import logging
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import List, Dict, Any, Optional

from .base import BaseParser, ParsedDocument, ParsedChunk

logger = logging.getLogger(__name__)


class ImageParser(BaseParser):
    SUPPORTED_EXTENSIONS = {
        ".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg", ".bmp", ".tiff"
    }

    def can_parse(self, file_path: Path) -> bool:
        return file_path.suffix.lower() in self.SUPPORTED_EXTENSIONS

    def parse(self, file_path: Path) -> ParsedDocument:
        ext = file_path.suffix.lower()
        title = file_path.stem
        metadata: Dict[str, Any] = {"file_type": ext}

        if ext == ".svg":
            chunks = self._parse_svg(file_path, metadata)
        else:
            chunks = self._parse_bitmap_image(file_path, metadata)

        return ParsedDocument(
            path=str(file_path.resolve()),
            filename=file_path.name,
            extension=ext,
            title=title,
            chunks=chunks,
            symbols=[],
            metadata=metadata
        )

    def _parse_svg(self, file_path: Path, metadata: Dict[str, Any]) -> List[ParsedChunk]:
        """Parses vector SVG files and extracts all text, titles, and descriptions."""
        text_elements: List[str] = []
        try:
            tree = ET.parse(str(file_path))
            root = tree.getroot()

            # Iterate all elements to find text content
            for elem in root.iter():
                tag = elem.tag.split("}")[-1].lower() if "}" in elem.tag else elem.tag.lower()
                if tag in {"text", "tspan", "title", "desc"}:
                    if elem.text and elem.text.strip():
                        text_elements.append(elem.text.strip())

            metadata["svg_text_count"] = len(text_elements)
        except Exception as e:
            logger.debug(f"Failed to parse SVG {file_path.name}: {e}")

        # Add filename context
        clean_name = re.sub(r'[_.-]+', ' ', file_path.stem)
        text_parts = [f"SVG Vektör Görseli: {file_path.name}"]
        if clean_name:
            text_parts.append(f"İsim Etiketleri: {clean_name}")
        if text_elements:
            text_parts.append("İçerik Metinleri:\n" + "\n".join(text_elements))

        full_content = "\n\n".join(text_parts)
        return [ParsedChunk(
            text=full_content,
            section_title=file_path.name,
            start_line=1,
            end_line=len(text_elements) + 2
        )]

    def _parse_bitmap_image(self, file_path: Path, metadata: Dict[str, Any]) -> List[ParsedChunk]:
        """Extracts EXIF metadata, dimensions, color profile, and semantic file tags."""
        info_lines: List[str] = [f"Görsel Dosyası: {file_path.name}"]

        # Natural language tags from path and filename
        stem_tags = re.split(r'[_.\s\-]+', file_path.stem)
        parent_tags = [p for p in file_path.parent.parts[-3:] if not p.startswith(".")]
        all_tags = list(dict.fromkeys(stem_tags + parent_tags))
        all_tags_clean = [t for t in all_tags if len(t) > 1 and not t.isdigit()]

        if all_tags_clean:
            info_lines.append(f"Etiketler ve Konum: {', '.join(all_tags_clean)}")

        # Try to read image dimensions and EXIF with Pillow
        try:
            from PIL import Image, ExifTags
            with Image.open(file_path) as img:
                w, h = img.size
                mode = img.mode
                fmt = img.format or file_path.suffix.upper().lstrip(".")
                metadata["width"] = w
                metadata["height"] = h
                metadata["format"] = fmt
                info_lines.append(f"Çözünürlük: {w}x{h} ({fmt}, {mode})")

                # Extract EXIF if available
                exif_data = img.getexif() if hasattr(img, "getexif") else None
                if exif_data:
                    exif_dict = {}
                    for tag_id, val in exif_data.items():
                        tag_name = ExifTags.TAGS.get(tag_id, str(tag_id))
                        if isinstance(val, (str, int, float)) and str(val).strip():
                            exif_dict[tag_name] = str(val).strip()

                    # Highlight key EXIF attributes
                    if "Make" in exif_dict or "Model" in exif_dict:
                        make = exif_dict.get("Make", "")
                        model = exif_dict.get("Model", "")
                        info_lines.append(f"Cihaz: {make} {model}".strip())
                    if "DateTimeOriginal" in exif_dict or "DateTime" in exif_dict:
                        dt = exif_dict.get("DateTimeOriginal") or exif_dict.get("DateTime")
                        info_lines.append(f"Çekim Tarihi: {dt}")
                    if "ImageDescription" in exif_dict:
                        info_lines.append(f"Açıklama: {exif_dict['ImageDescription']}")
                    if "Software" in exif_dict:
                        info_lines.append(f"Yazılım: {exif_dict['Software']}")
                    if "Artist" in exif_dict:
                        info_lines.append(f"Sanatçı: {exif_dict['Artist']}")
        except Exception as e:
            logger.debug(f"Pillow EXIF extraction skipped for {file_path.name}: {e}")
        full_content = "\n".join(info_lines)

        # Attempt local OCR if available
        ocr_text = self._try_extract_ocr(file_path)
        if ocr_text:
            metadata["has_ocr"] = True
            full_content += f"\n\nGörsel İçi Okunan Metin (OCR):\n{ocr_text}"

        return [ParsedChunk(
            text=full_content,
            section_title=file_path.name,
            start_line=1,
            end_line=len(info_lines) + (ocr_text.count("\n") + 2 if ocr_text else 0)
        )]

    @staticmethod
    def _try_extract_ocr(file_path: Path) -> Optional[str]:
        """Attempts OCR text extraction using pytesseract if installed, otherwise gracefully returns None."""
        try:
            import pytesseract
            from PIL import Image
            with Image.open(file_path) as img:
                if max(img.size) > 2000:
                    img.thumbnail((2000, 2000))
                text = pytesseract.image_to_string(img, timeout=5)
                text_clean = text.strip()
                if len(text_clean) >= 10:
                    return text_clean[:3000]
        except Exception:
            pass
        return None
