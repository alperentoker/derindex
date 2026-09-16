"""Base interfaces and data structures for document parsing."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Dict, Any


@dataclass
class CodeSymbol:
    name: str
    kind: str  # "function", "class", "method", "struct", "import"
    line_number: int
    docstring: Optional[str] = None


@dataclass
class ParsedChunk:
    text: str
    page_number: Optional[int] = None
    section_title: Optional[str] = None
    code_type: Optional[str] = None
    symbol_name: Optional[str] = None
    start_line: Optional[int] = None
    end_line: Optional[int] = None
    symbols: List[CodeSymbol] = field(default_factory=list)


@dataclass
class ParsedDocument:
    path: str
    filename: str
    extension: str
    title: Optional[str] = None
    chunks: List[ParsedChunk] = field(default_factory=list)
    symbols: List[CodeSymbol] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


class BaseParser(ABC):
    @abstractmethod
    def can_parse(self, file_path: Path) -> bool:
        """Check if parser handles this file type."""
        pass

    @abstractmethod
    def parse(self, file_path: Path) -> ParsedDocument:
        """Parse the file and return structured document with chunks and symbols."""
        pass
