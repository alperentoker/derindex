"""Unified parser registry for all supported document and media formats."""

from pathlib import Path
from typing import Optional, List

from .base import BaseParser, ParsedDocument, ParsedChunk, CodeSymbol
from .pdf_parser import PDFParser
from .markdown_parser import MarkdownParser
from .html_parser import HTMLParserImpl
from .code_parser import CodeParser
from .text_parser import TextParser
from .office_parser import OfficeParser
from .image_parser import ImageParser
from .media_parser import MediaParser
from .archive_parser import ArchiveParser
from .universal_parser import UniversalFallbackParser

_PARSERS: List[BaseParser] = [
    PDFParser(),
    MarkdownParser(),
    HTMLParserImpl(),
    OfficeParser(),
    ImageParser(),
    MediaParser(),
    ArchiveParser(),
    CodeParser(),
    TextParser(),
]

_UNIVERSAL_FALLBACK = UniversalFallbackParser()


def get_parser_for_file(file_path: Path) -> Optional[BaseParser]:
    for p in _PARSERS:
        if p.can_parse(file_path):
            return p
    # Universal fallback for any arbitrary file
    return _UNIVERSAL_FALLBACK


__all__ = [
    "BaseParser",
    "ParsedDocument",
    "ParsedChunk",
    "CodeSymbol",
    "PDFParser",
    "MarkdownParser",
    "HTMLParserImpl",
    "CodeParser",
    "TextParser",
    "OfficeParser",
    "ImageParser",
    "MediaParser",
    "ArchiveParser",
    "UniversalFallbackParser",
    "get_parser_for_file",
]
