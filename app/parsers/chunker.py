"""Smart chunking utilities for splitting text while preserving context and boundaries."""

import re
from typing import List, Tuple, Optional
from config import config
from .base import ParsedChunk, CodeSymbol


class SmartChunker:
    def __init__(
        self,
        chunk_size: int = config.CHUNK_SIZE_TOKENS,
        chunk_overlap: int = config.CHUNK_OVERLAP_TOKENS,
        min_chunk_size: int = config.MIN_CHUNK_TOKENS
    ):
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.min_chunk_size = min_chunk_size

    @staticmethod
    def estimate_tokens(text: str) -> int:
        """Estimate token count based on whitespace and punctuation splitting (~1.3 tokens per word)."""
        words = text.split()
        return max(1, int(len(words) * 1.3))

    def chunk_text(
        self,
        text: str,
        page_number: Optional[int] = None,
        section_title: Optional[str] = None,
        code_type: Optional[str] = None,
        symbol_name: Optional[str] = None,
        start_line_offset: int = 1,
        symbols: Optional[List[CodeSymbol]] = None
    ) -> List[ParsedChunk]:
        """
        Splits text into context-aware chunks, respecting paragraph and sentence boundaries.
        Tracks approximate line ranges for code or text chunks.
        """
        if not text or not text.strip():
            return []

        # Split into paragraphs first
        paragraphs = re.split(r'\n\s*\n', text)
        chunks: List[ParsedChunk] = []
        current_paragraphs: List[str] = []
        current_tokens = 0
        current_start_line = start_line_offset
        lines_consumed = 0

        for para in paragraphs:
            para = para.strip()
            if not para:
                continue

            para_tokens = self.estimate_tokens(para)

            # If single paragraph is larger than chunk size, split by sentences
            if para_tokens > self.chunk_size:
                # Flush accumulated paragraphs first
                if current_paragraphs:
                    chunk_str = "\n\n".join(current_paragraphs)
                    chunk_lines = chunk_str.count("\n") + 1
                    chunks.append(ParsedChunk(
                        text=chunk_str,
                        page_number=page_number,
                        section_title=section_title,
                        code_type=code_type,
                        symbol_name=symbol_name,
                        start_line=current_start_line,
                        end_line=current_start_line + chunk_lines - 1,
                        symbols=symbols or []
                    ))
                    current_start_line += chunk_lines
                    current_paragraphs = []
                    current_tokens = 0

                # Sentence split
                sentences = re.split(r'(?<=[.!?])\s+', para)
                sentence_buf: List[str] = []
                buf_tokens = 0
                sent_start_line = current_start_line

                for sent in sentences:
                    sent = sent.strip()
                    if not sent:
                        continue
                    sent_tokens = self.estimate_tokens(sent)

                    if buf_tokens + sent_tokens > self.chunk_size and sentence_buf:
                        sent_str = " ".join(sentence_buf)
                        sent_lines = sent_str.count("\n") + 1
                        chunks.append(ParsedChunk(
                            text=sent_str,
                            page_number=page_number,
                            section_title=section_title,
                            code_type=code_type,
                            symbol_name=symbol_name,
                            start_line=sent_start_line,
                            end_line=sent_start_line + sent_lines - 1,
                            symbols=symbols or []
                        ))
                        # Keep overlap
                        sent_start_line += sent_lines
                        overlap_sents = sentence_buf[-2:] if len(sentence_buf) >= 2 else sentence_buf[-1:]
                        sentence_buf = list(overlap_sents)
                        buf_tokens = sum(self.estimate_tokens(s) for s in sentence_buf)

                    sentence_buf.append(sent)
                    buf_tokens += sent_tokens

                if sentence_buf:
                    sent_str = " ".join(sentence_buf)
                    sent_lines = sent_str.count("\n") + 1
                    chunks.append(ParsedChunk(
                        text=sent_str,
                        page_number=page_number,
                        section_title=section_title,
                        code_type=code_type,
                        symbol_name=symbol_name,
                        start_line=sent_start_line,
                        end_line=sent_start_line + sent_lines - 1,
                        symbols=symbols or []
                    ))
                    current_start_line = sent_start_line + sent_lines

                continue

            # Standard paragraph accumulation
            if current_tokens + para_tokens > self.chunk_size and current_paragraphs:
                chunk_str = "\n\n".join(current_paragraphs)
                chunk_lines = chunk_str.count("\n") + 1
                chunks.append(ParsedChunk(
                    text=chunk_str,
                    page_number=page_number,
                    section_title=section_title,
                    code_type=code_type,
                    symbol_name=symbol_name,
                    start_line=current_start_line,
                    end_line=current_start_line + chunk_lines - 1,
                    symbols=symbols or []
                ))
                current_start_line += chunk_lines

                # Overlap: keep last paragraph if reasonable
                if len(current_paragraphs) > 1 and self.estimate_tokens(current_paragraphs[-1]) <= self.chunk_overlap:
                    current_paragraphs = [current_paragraphs[-1]]
                    current_tokens = self.estimate_tokens(current_paragraphs[0])
                else:
                    current_paragraphs = []
                    current_tokens = 0

            current_paragraphs.append(para)
            current_tokens += para_tokens

        # Flush remaining paragraphs
        if current_paragraphs:
            chunk_str = "\n\n".join(current_paragraphs)
            chunk_lines = chunk_str.count("\n") + 1
            chunks.append(ParsedChunk(
                text=chunk_str,
                page_number=page_number,
                section_title=section_title,
                code_type=code_type,
                symbol_name=symbol_name,
                start_line=current_start_line,
                end_line=current_start_line + chunk_lines - 1,
                symbols=symbols or []
            ))

        return chunks
