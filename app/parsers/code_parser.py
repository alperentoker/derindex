"""Code parser extracting AST symbols for Python, and lexical/regex structures for C/C++ and JS/TS."""

import ast
import re
import logging
from pathlib import Path
from typing import List, Optional, Tuple, Dict, Any

from .base import BaseParser, ParsedDocument, ParsedChunk, CodeSymbol
from .chunker import SmartChunker

logger = logging.getLogger(__name__)


class CodeParser(BaseParser):
    SUPPORTED_LANGUAGES: Dict[str, str] = {
        ".py": "python",
        ".c": "c",
        ".cpp": "cpp",
        ".cc": "cpp",
        ".h": "c_header",
        ".hpp": "cpp_header",
        ".js": "javascript",
        ".jsx": "javascript_react",
        ".ts": "typescript",
        ".tsx": "typescript_react",
    }

    def __init__(self):
        self.chunker = SmartChunker(chunk_size=350, chunk_overlap=30)

    def can_parse(self, file_path: Path) -> bool:
        return file_path.suffix.lower() in self.SUPPORTED_LANGUAGES

    def parse(self, file_path: Path) -> ParsedDocument:
        ext = file_path.suffix.lower()
        lang = self.SUPPORTED_LANGUAGES.get(ext, "code")

        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()

        lines = content.splitlines()

        if lang == "python":
            return self._parse_python(file_path, content, lines)
        elif lang in {"c", "cpp", "c_header", "cpp_header"}:
            return self._parse_c_cpp(file_path, content, lines, lang)
        elif lang in {"javascript", "javascript_react", "typescript", "typescript_react"}:
            return self._parse_javascript(file_path, content, lines, lang)
        else:
            return self._fallback_code_parse(file_path, content, lines, lang)

    def _parse_python(self, file_path: Path, content: str, lines: List[str]) -> ParsedDocument:
        symbols: List[CodeSymbol] = []
        chunks: List[ParsedChunk] = []

        try:
            tree = ast.parse(content, filename=str(file_path))
        except SyntaxError as e:
            logger.debug(f"AST parse syntax error in {file_path.name}: {e}. Falling back to line chunking.")
            return self._fallback_code_parse(file_path, content, lines, "python")

        # Top-level and class-level symbol extraction
        class ASTSymbolVisitor(ast.NodeVisitor):
            def __init__(self):
                self.extracted_symbols: List[CodeSymbol] = []
                self.blocks: List[Dict[str, Any]] = []

            def visit_Import(self, node: ast.Import):
                for alias in node.names:
                    self.extracted_symbols.append(CodeSymbol(
                        name=alias.name,
                        kind="import",
                        line_number=node.lineno
                    ))
                self.generic_visit(node)

            def visit_ImportFrom(self, node: ast.ImportFrom):
                mod = node.module or ""
                for alias in node.names:
                    name = f"{mod}.{alias.name}" if mod else alias.name
                    self.extracted_symbols.append(CodeSymbol(
                        name=name,
                        kind="import",
                        line_number=node.lineno
                    ))
                self.generic_visit(node)

            def visit_ClassDef(self, node: ast.ClassDef):
                doc = ast.get_docstring(node)
                sym = CodeSymbol(
                    name=node.name,
                    kind="class",
                    line_number=node.lineno,
                    docstring=doc
                )
                self.extracted_symbols.append(sym)
                end_lineno = getattr(node, "end_lineno", node.lineno + 10)
                self.blocks.append({
                    "name": node.name,
                    "kind": "class",
                    "start_line": node.lineno,
                    "end_line": end_lineno,
                    "symbol": sym
                })
                self.generic_visit(node)

            def visit_FunctionDef(self, node: ast.FunctionDef):
                self._handle_func(node, is_async=False)

            def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef):
                self._handle_func(node, is_async=True)

            def _handle_func(self, node, is_async: bool):
                doc = ast.get_docstring(node)
                kind = "async_function" if is_async else "function"
                sym = CodeSymbol(
                    name=node.name,
                    kind=kind,
                    line_number=node.lineno,
                    docstring=doc
                )
                self.extracted_symbols.append(sym)
                end_lineno = getattr(node, "end_lineno", node.lineno + 5)
                self.blocks.append({
                    "name": node.name,
                    "kind": kind,
                    "start_line": node.lineno,
                    "end_line": end_lineno,
                    "symbol": sym
                })

        visitor = ASTSymbolVisitor()
        visitor.visit(tree)
        symbols = visitor.extracted_symbols

        # Convert AST blocks into meaningful code chunks
        covered_lines = set()
        for blk in visitor.blocks:
            s_line = blk["start_line"]
            e_line = blk["end_line"]
            block_lines = lines[s_line - 1:e_line]
            block_text = "\n".join(block_lines).strip()
            if block_text:
                chunks.append(ParsedChunk(
                    text=block_text,
                    code_type="python",
                    symbol_name=blk["name"],
                    start_line=s_line,
                    end_line=e_line,
                    symbols=[blk["symbol"]]
                ))
                for l in range(s_line, e_line + 1):
                    covered_lines.add(l)

        # Fallback for remaining lines (module header, global vars, unvisited scripts)
        uncovered_blocks: List[Tuple[int, List[str]]] = []
        current_block: List[str] = []
        cur_start = 1

        for idx, line in enumerate(lines, start=1):
            if idx not in covered_lines:
                if not current_block:
                    cur_start = idx
                current_block.append(line)
            else:
                if current_block:
                    uncovered_blocks.append((cur_start, current_block))
                    current_block = []

        if current_block:
            uncovered_blocks.append((cur_start, current_block))

        for start_idx, blk_lines in uncovered_blocks:
            blk_text = "\n".join(blk_lines).strip()
            if len(blk_text) > 20:
                sub_chunks = self.chunker.chunk_text(
                    text=blk_text,
                    code_type="python",
                    start_line_offset=start_idx
                )
                chunks.extend(sub_chunks)

        # If no AST chunks were created (e.g. simple script), fallback chunk whole file
        if not chunks and lines:
            chunks = self.chunker.chunk_text(content, code_type="python", start_line_offset=1)

        return ParsedDocument(
            path=str(file_path.resolve()),
            filename=file_path.name,
            extension=".py",
            title=file_path.stem,
            chunks=chunks,
            symbols=symbols,
            metadata={"language": "python", "symbols_count": len(symbols)}
        )

    def _parse_c_cpp(self, file_path: Path, content: str, lines: List[str], lang: str) -> ParsedDocument:
        symbols: List[CodeSymbol] = []
        chunks: List[ParsedChunk] = []

        # Regex for C/C++ includes: #include <header.h> or #include "header.h"
        include_pattern = re.compile(r'^\s*#include\s+["<]([^">]+)[">]')
        # Regex for class/struct: class/struct Name { ...
        class_pattern = re.compile(r'^\s*(class|struct)\s+([A-Za-z0-9_]+)')
        # Heuristic for functions: ReturnType FuncName(...) {
        func_pattern = re.compile(r'^\s*(?:[A-Za-z0-9_:<>]+\s+)+([A-Za-z0-9_]+)\s*\([^)]*\)\s*(?:const)?\s*\{')

        line_map = {}
        for idx, line in enumerate(lines, start=1):
            inc_match = include_pattern.match(line)
            if inc_match:
                symbols.append(CodeSymbol(name=inc_match.group(1), kind="import", line_number=idx))
                continue

            cls_match = class_pattern.match(line)
            if cls_match:
                symbols.append(CodeSymbol(name=cls_match.group(2), kind=cls_match.group(1), line_number=idx))
                continue

            fn_match = func_pattern.match(line)
            if fn_match:
                fn_name = fn_match.group(1)
                if fn_name not in {"if", "for", "while", "switch"}:
                    symbols.append(CodeSymbol(name=fn_name, kind="function", line_number=idx))

        # Chunk using smart chunker with symbols tagged
        chunks = self.chunker.chunk_text(
            text=content,
            code_type=lang,
            start_line_offset=1,
            symbols=symbols
        )

        return ParsedDocument(
            path=str(file_path.resolve()),
            filename=file_path.name,
            extension=file_path.suffix.lower(),
            title=file_path.stem,
            chunks=chunks,
            symbols=symbols,
            metadata={"language": lang, "symbols_count": len(symbols)}
        )

    def _parse_javascript(self, file_path: Path, content: str, lines: List[str], lang: str) -> ParsedDocument:
        symbols: List[CodeSymbol] = []

        # JS/TS imports: import ... from '...' or require('...')
        import_pattern = re.compile(r'import\s+.*?\s+from\s+[\'"]([^\'"]+)[\'"]|const\s+.*?\s*=\s*require\([\'"]([^\'"]+)[\'"]')
        # JS/TS classes: class Name
        class_pattern = re.compile(r'class\s+([A-Za-z0-9_$]+)')
        # JS/TS functions: function name(...) or const name = (...) =>
        func_pattern = re.compile(r'function\s+([A-Za-z0-9_$]+)\s*\(|(?:const|let|var)\s+([A-Za-z0-9_$]+)\s*=\s*(?:async\s*)?\([^)]*\)\s*=>')

        for idx, line in enumerate(lines, start=1):
            for m in import_pattern.finditer(line):
                mod = m.group(1) or m.group(2)
                if mod:
                    symbols.append(CodeSymbol(name=mod, kind="import", line_number=idx))

            cls_match = class_pattern.search(line)
            if cls_match:
                symbols.append(CodeSymbol(name=cls_match.group(1), kind="class", line_number=idx))

            fn_match = func_pattern.search(line)
            if fn_match:
                fn_name = fn_match.group(1) or fn_match.group(2)
                if fn_name:
                    symbols.append(CodeSymbol(name=fn_name, kind="function", line_number=idx))

        chunks = self.chunker.chunk_text(
            text=content,
            code_type=lang,
            start_line_offset=1,
            symbols=symbols
        )

        return ParsedDocument(
            path=str(file_path.resolve()),
            filename=file_path.name,
            extension=file_path.suffix.lower(),
            title=file_path.stem,
            chunks=chunks,
            symbols=symbols,
            metadata={"language": lang, "symbols_count": len(symbols)}
        )

    def _fallback_code_parse(self, file_path: Path, content: str, lines: List[str], lang: str) -> ParsedDocument:
        chunks = self.chunker.chunk_text(content, code_type=lang, start_line_offset=1)
        return ParsedDocument(
            path=str(file_path.resolve()),
            filename=file_path.name,
            extension=file_path.suffix.lower(),
            title=file_path.stem,
            chunks=chunks,
            symbols=[],
            metadata={"language": lang}
        )
