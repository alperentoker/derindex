"""Unified parser registry for all supported document formats."""

from pathlib import Path
from typing import Optional, List

from .base import BaseParser, ParsedDocument, ParsedChunk, CodeSymbol
from .pdf_parser import PDFParser
from .markdown_parser import MarkdownParser
from .html_parser import HTMLParserImpl
from .code_parser import CodeParser
from .text_parser import TextParser

_PARSERS: List[BaseParser] = [
    PDFParser(),
    MarkdownParser(),
    HTMLParserImpl(),
    CodeParser(),
    TextParser(),
]


def get_parser_for_file(file_path: Path) -> Optional[BaseParser]:
    for p in _PARSERS:
        if p.can_parse(file_path):
            return p
    return None


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
    "get_parser_for_file",
]
