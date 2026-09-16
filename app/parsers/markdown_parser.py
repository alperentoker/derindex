"""Markdown parser with section heading hierarchy and code block extraction."""

import re
from pathlib import Path
from typing import List, Tuple
from .base import BaseParser, ParsedDocument, ParsedChunk
from .chunker import SmartChunker


class MarkdownParser(BaseParser):
    def __init__(self):
        self.chunker = SmartChunker()

    def can_parse(self, file_path: Path) -> bool:
        return file_path.suffix.lower() in {".md", ".markdown"}

    def parse(self, file_path: Path) -> ParsedDocument:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()

        doc_title = file_path.stem
        sections: List[Tuple[str, List[Tuple[int, str]]]] = []
        # Current section stack: list of (level, title)
        heading_stack: List[Tuple[int, str]] = []
        current_section_lines: List[Tuple[int, str]] = []
        current_section_name = "Introduction"

        for line_idx, line in enumerate(lines, start=1):
            heading_match = re.match(r'^(#{1,6})\s+(.+)$', line.strip())
            if heading_match:
                # Flush previous section
                if current_section_lines:
                    sections.append((current_section_name, current_section_lines))
                    current_section_lines = []

                level = len(heading_match.group(1))
                title = heading_match.group(2).strip()

                if level == 1 and not doc_title:
                    doc_title = title

                # Pop headings of equal or deeper level
                while heading_stack and heading_stack[-1][0] >= level:
                    heading_stack.pop()

                heading_stack.append((level, title))
                current_section_name = " > ".join(h[1] for h in heading_stack)
            else:
                current_section_lines.append((line_idx, line))

        if current_section_lines:
            sections.append((current_section_name, current_section_lines))

        # Chunk each section separately so section headers are attached properly
        all_chunks: List[ParsedChunk] = []
        for section_name, sec_lines in sections:
            if not sec_lines:
                continue
            section_text = "".join(l[1] for l in sec_lines).strip()
            if not section_text:
                continue

            start_line = sec_lines[0][0]
            sec_chunks = self.chunker.chunk_text(
                text=section_text,
                section_title=section_name,
                start_line_offset=start_line
            )
            all_chunks.extend(sec_chunks)

        return ParsedDocument(
            path=str(file_path.resolve()),
            filename=file_path.name,
            extension=file_path.suffix.lower(),
            title=doc_title,
            chunks=all_chunks,
            symbols=[],
            metadata={"sections_count": len(sections)}
        )
