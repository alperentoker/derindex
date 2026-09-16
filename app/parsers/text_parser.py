"""Parsers for plain text, JSON, YAML, TOML, XML, CSV, TSV, INI, and Logs."""

import csv
import json
import logging
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import List, Dict, Any

from .base import BaseParser, ParsedDocument, ParsedChunk
from .chunker import SmartChunker

logger = logging.getLogger(__name__)


class TextParser(BaseParser):
    SUPPORTED_EXTENSIONS = {
        ".txt", ".json", ".yaml", ".yml",
        ".toml", ".ini", ".cfg", ".conf",
        ".xml", ".csv", ".tsv", ".log",
        ".tex", ".rst"
    }

    def __init__(self):
        self.chunker = SmartChunker(chunk_size=400, chunk_overlap=40)

    def can_parse(self, file_path: Path) -> bool:
        return file_path.suffix.lower() in self.SUPPORTED_EXTENSIONS

    def parse(self, file_path: Path) -> ParsedDocument:
        ext = file_path.suffix.lower()

        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            raw_content = f.read()

        text_to_chunk = raw_content
        metadata: Dict[str, Any] = {"file_type": ext}

        if ext == ".json":
            try:
                data = json.loads(raw_content)
                text_to_chunk = json.dumps(data, indent=2, ensure_ascii=False)
                metadata["json_valid"] = True
            except Exception:
                metadata["json_valid"] = False

        elif ext in {".yaml", ".yml"}:
            try:
                import yaml
                data = yaml.safe_load(raw_content)
                text_to_chunk = yaml.dump(data, allow_unicode=True, default_flow_style=False)
                metadata["yaml_valid"] = True
            except Exception:
                metadata["yaml_valid"] = False

        elif ext == ".toml":
            try:
                import tomllib
                data = tomllib.loads(raw_content)
                text_to_chunk = json.dumps(data, indent=2, ensure_ascii=False)
                metadata["toml_valid"] = True
            except Exception:
                # Keep raw_content on parse error
                metadata["toml_valid"] = False

        elif ext in {".csv", ".tsv"}:
            text_to_chunk = self._format_tabular(raw_content, delimiter="\t" if ext == ".tsv" else ",")

        elif ext == ".xml":
            try:
                tree = ET.fromstring(raw_content)
                text_nodes = [elem.text.strip() for elem in tree.iter() if elem.text and elem.text.strip()]
                if text_nodes:
                    text_to_chunk = "\n".join(text_nodes)
            except Exception:
                pass

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

    def _format_tabular(self, raw_content: str, delimiter: str = ",") -> str:
        """Formats CSV/TSV rows into readable structured text for lexical and semantic indexing."""
        lines = raw_content.splitlines()
        if not lines:
            return ""

        reader = csv.reader(lines[:500], delimiter=delimiter)
        rows = list(reader)
        if not rows:
            return raw_content

        headers = [h.strip() for h in rows[0]]
        formatted_rows = []

        for row_idx, row in enumerate(rows[1:], start=1):
            cell_pairs = []
            for col_idx, cell in enumerate(row):
                header_name = headers[col_idx] if col_idx < len(headers) else f"Col_{col_idx}"
                cell_val = cell.strip()
                if cell_val:
                    cell_pairs.append(f"{header_name}: {cell_val}")
            if cell_pairs:
                formatted_rows.append(f"Row {row_idx}: " + " | ".join(cell_pairs))

        return "\n".join(formatted_rows) if formatted_rows else raw_content
