"""HTML parser extracting titles, semantic headers, and clean body text."""

import re
from html.parser import HTMLParser
from pathlib import Path
from typing import List, Optional
from .base import BaseParser, ParsedDocument, ParsedChunk
from .chunker import SmartChunker


class _HTMLTextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.title: Optional[str] = None
        self._in_title = False
        self._in_ignored = False
        self._ignored_tags = {"script", "style", "noscript", "svg"}
        self.text_parts: List[str] = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() == "title":
            self._in_title = True
        elif tag.lower() in self._ignored_tags:
            self._in_ignored = True
        elif tag.lower() in {"p", "div", "h1", "h2", "h3", "h4", "h5", "h6", "li", "tr", "br"}:
            self.text_parts.append("\n")

    def handle_endtag(self, tag):
        if tag.lower() == "title":
            self._in_title = False
        elif tag.lower() in self._ignored_tags:
            self._in_ignored = False
        elif tag.lower() in {"p", "div", "h1", "h2", "h3", "h4", "h5", "h6", "li", "tr"}:
            self.text_parts.append("\n")

    def handle_data(self, data):
        if self._in_ignored:
            return
        if self._in_title:
            self.title = (self.title or "") + data
        else:
            self.text_parts.append(data)

    def get_text(self) -> str:
        raw = "".join(self.text_parts)
        # Normalize multiple spaces and blank lines
        clean = re.sub(r'[ \t]+', ' ', raw)
        clean = re.sub(r'\n\s*\n+', '\n\n', clean)
        return clean.strip()


class HTMLParserImpl(BaseParser):
    def __init__(self):
        self.chunker = SmartChunker()

    def can_parse(self, file_path: Path) -> bool:
        return file_path.suffix.lower() in {".html", ".htm"}

    def parse(self, file_path: Path) -> ParsedDocument:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()

        extractor = _HTMLTextExtractor()
        try:
            extractor.feed(content)
        except Exception:
            pass

        title = extractor.title.strip() if extractor.title else file_path.stem
        body_text = extractor.get_text()

        chunks = self.chunker.chunk_text(
            text=body_text,
            section_title=title,
            start_line_offset=1
        )

        return ParsedDocument(
            path=str(file_path.resolve()),
            filename=file_path.name,
            extension=file_path.suffix.lower(),
            title=title,
            chunks=chunks,
            symbols=[],
            metadata={"raw_length": len(content)}
        )
