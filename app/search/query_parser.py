"""Intelligent search query syntax parser supporting operators and filters.

Supported operators:
  - ext:pdf,py,ts     Include specific file extensions
  - -ext:log,tmp      Exclude specific file extensions
  - path:app/search   Filter documents containing path substring
  - type:code|doc|img Filter by file category (code, document, image)
  - after:YYYY-MM-DD  Filter documents modified after date
  - before:YYYY-MM-DD Filter documents modified before date
  - symbol:my_func    Filter chunks containing specific code symbol

The parser is completely language-agnostic: terms in English, Turkish,
or programming languages remain unaltered in clean_query.
"""

import re
import time
from datetime import datetime
from dataclasses import dataclass, field
from typing import Set, Optional, List, Tuple


@dataclass
class ParsedQuery:
    raw_query: str
    clean_query: str
    extensions: Set[str] = field(default_factory=set)
    exclude_extensions: Set[str] = field(default_factory=set)
    path_pattern: Optional[str] = None
    file_type: Optional[str] = None  # 'code', 'doc', 'image'
    after_timestamp: Optional[float] = None
    before_timestamp: Optional[float] = None
    symbol_filter: Optional[str] = None

    @property
    def has_filters(self) -> bool:
        return bool(
            self.extensions
            or self.exclude_extensions
            or self.path_pattern
            or self.file_type
            or self.after_timestamp is not None
            or self.before_timestamp is not None
            or self.symbol_filter
        )


class QueryParser:
    """Parses advanced query syntax while extracting filters and clean search text."""

    TYPE_EXTENSIONS = {
        "code": {
            ".py", ".js", ".ts", ".jsx", ".tsx", ".java", ".c", ".cpp", ".h", ".hpp",
            ".cs", ".go", ".rs", ".rb", ".php", ".swift", ".kt", ".scala", ".sh",
            ".bash", ".sql", ".html", ".css", ".scss", ".json", ".yaml", ".yml",
            ".xml", ".toml", ".ini", ".env", ".lua", ".r", ".dart", ".proto", ".ipynb"
        },
        "doc": {
            ".pdf", ".docx", ".doc", ".txt", ".md", ".markdown", ".rst", ".rtf",
            ".epub", ".odt", ".pptx", ".xlsx", ".csv", ".tsv"
        },
        "image": {
            ".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg", ".bmp", ".tiff", ".ico"
        }
    }

    # Regex patterns for query operators
    # Matches: key:"value with spaces" or key:value
    _OPERATOR_PATTERN = re.compile(r'(-?[\w]+):("([^"]+)"|([^\s]+))')

    @classmethod
    def parse(cls, raw_query: str) -> ParsedQuery:
        if not raw_query or not raw_query.strip():
            return ParsedQuery(raw_query="", clean_query="")

        extensions: Set[str] = set()
        exclude_extensions: Set[str] = set()
        path_pattern: Optional[str] = None
        file_type: Optional[str] = None
        after_timestamp: Optional[float] = None
        before_timestamp: Optional[float] = None
        symbol_filter: Optional[str] = None

        # Extract operators
        tokens: List[str] = []
        last_end = 0

        for match in cls._OPERATOR_PATTERN.finditer(raw_query):
            start, end = match.span()
            # Append non-operator text preceding this match
            prefix = raw_query[last_end:start].strip()
            if prefix:
                tokens.append(prefix)
            last_end = end

            key = match.group(1).lower()
            val = match.group(3) if match.group(3) is not None else match.group(4)
            val = val.strip()

            if key == "ext":
                for ext in val.split(","):
                    ext = ext.strip().lower()
                    if ext:
                        if not ext.startswith("."):
                            ext = f".{ext}"
                        extensions.add(ext)

            elif key in ("-ext", "not:ext"):
                for ext in val.split(","):
                    ext = ext.strip().lower()
                    if ext:
                        if not ext.startswith("."):
                            ext = f".{ext}"
                        exclude_extensions.add(ext)

            elif key in ("path", "dir", "folder"):
                path_pattern = val.replace("\\", "/").strip("/")

            elif key in ("type", "kind"):
                val_lower = val.lower()
                if val_lower in ("code", "source", "dev", "kod"):
                    file_type = "code"
                elif val_lower in ("doc", "document", "belge", "text"):
                    file_type = "doc"
                elif val_lower in ("image", "img", "resim", "gorsel", "görsel"):
                    file_type = "image"

            elif key in ("after", "since", "from"):
                ts = cls._parse_date_to_timestamp(val, is_end_of_day=False)
                if ts is not None:
                    after_timestamp = ts

            elif key in ("before", "until", "to"):
                ts = cls._parse_date_to_timestamp(val, is_end_of_day=True)
                if ts is not None:
                    before_timestamp = ts

            elif key in ("symbol", "sym"):
                symbol_filter = val

            else:
                # Unrecognized operator -> keep as part of search query
                tokens.append(match.group(0))

        # Append any remaining trailing text
        suffix = raw_query[last_end:].strip()
        if suffix:
            tokens.append(suffix)

        clean_query = " ".join(tokens).strip()

        return ParsedQuery(
            raw_query=raw_query,
            clean_query=clean_query,
            extensions=extensions,
            exclude_extensions=exclude_extensions,
            path_pattern=path_pattern,
            file_type=file_type,
            after_timestamp=after_timestamp,
            before_timestamp=before_timestamp,
            symbol_filter=symbol_filter
        )

    @classmethod
    def matches_filters(
        cls,
        parsed_query: ParsedQuery,
        doc_path: str,
        doc_extension: str,
        doc_mtime: float,
        chunk_code_type: Optional[str] = None,
        chunk_symbol_name: Optional[str] = None
    ) -> bool:
        """Evaluates whether a candidate document/chunk satisfies all query filters."""
        if not parsed_query.has_filters:
            return True

        ext = doc_extension.lower() if doc_extension else ""

        # 1. Extensions include filter
        if parsed_query.extensions and ext not in parsed_query.extensions:
            return False

        # 2. Extensions exclude filter
        if parsed_query.exclude_extensions and ext in parsed_query.exclude_extensions:
            return False

        # 3. Path substring filter
        if parsed_query.path_pattern:
            normalized_path = doc_path.replace("\\", "/").lower()
            if parsed_query.path_pattern.lower() not in normalized_path:
                return False

        # 4. File type category filter
        if parsed_query.file_type:
            allowed_exts = cls.TYPE_EXTENSIONS.get(parsed_query.file_type, set())
            if parsed_query.file_type == "code":
                if not (chunk_code_type or ext in allowed_exts):
                    return False
            else:
                if ext not in allowed_exts:
                    return False

        # 5. Date filters (after / before)
        if parsed_query.after_timestamp is not None and doc_mtime < parsed_query.after_timestamp:
            return False
        if parsed_query.before_timestamp is not None and doc_mtime > parsed_query.before_timestamp:
            return False

        # 6. Symbol filter
        if parsed_query.symbol_filter:
            target_sym = parsed_query.symbol_filter.lower()
            if not chunk_symbol_name or target_sym not in chunk_symbol_name.lower():
                return False

        return True

    @staticmethod
    def _parse_date_to_timestamp(date_str: str, is_end_of_day: bool = False) -> Optional[float]:
        """Parses common date formats (YYYY-MM-DD, YYYY/MM/DD) to POSIX timestamp."""
        formats = [
            "%Y-%m-%d",
            "%Y/%m/%d",
            "%Y.%m.%d",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%dT%H:%M:%S",
            "%d.%m.%Y",
            "%d-%m-%Y"
        ]
        for fmt in formats:
            try:
                dt = datetime.strptime(date_str, fmt)
                if is_end_of_day and fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%d.%m.%Y", "%d-%m-%Y"):
                    dt = dt.replace(hour=23, minute=59, second=59)
                return dt.timestamp()
            except ValueError:
                continue
        return None
