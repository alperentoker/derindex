"""Parsers for plain text, JSON, and YAML files."""

import json
import yaml
from pathlib import Path
from typing import List
from .base import BaseParser, ParsedDocument, ParsedChunk
from .chunker import SmartChunker


class TextParser(BaseParser):
    def __init__(self):
        self.chunker = SmartChunker()

    def can_parse(self, file_path: Path) -> bool:
        return file_path.suffix.lower() in {".txt", ".json", ".yaml", ".yml"}

    def parse(self, file_path: Path) -> ParsedDocument:
        ext = file_path.suffix.lower()

        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            raw_content = f.read()

        text_to_chunk = raw_content
        metadata = {"file_type": ext}

        if ext == ".json":
            try:
                data = json.loads(raw_content)
                # Formatted pretty JSON for better line-by-line reading
                text_to_chunk = json.dumps(data, indent=2, ensure_ascii=False)
                metadata["json_valid"] = True
            except Exception:
                metadata["json_valid"] = False

        elif ext in {".yaml", ".yml"}:
            try:
                data = yaml.safe_load(raw_content)
                text_to_chunk = yaml.dump(data, allow_unicode=True, default_flow_style=False)
                metadata["yaml_valid"] = True
            except Exception:
                metadata["yaml_valid"] = False

        chunks = self.chunker.chunk_text(
            text=text_to_chunk,
            section_title=file_path.stem,
            start_line_offset=1
        )

        return ParsedDocument(
            path=str(file_path.resolve()),
            filename=file_path.name,
            extension=ext,
            title=file_path.stem,
            chunks=chunks,
            symbols=[],
            metadata=metadata
        )
