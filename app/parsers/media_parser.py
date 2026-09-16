"""Media parser for Audio, Video, Subtitles, and companion subtitle transcript extraction."""

import re
import logging
from pathlib import Path
from typing import List, Dict, Any, Optional

from .base import BaseParser, ParsedDocument, ParsedChunk
from .chunker import SmartChunker

logger = logging.getLogger(__name__)


class MediaParser(BaseParser):
    AUDIO_EXTENSIONS = {
        ".mp3", ".wav", ".flac", ".ogg", ".m4a", ".aac", ".opus", ".wma"
    }
    VIDEO_EXTENSIONS = {
        ".mp4", ".mkv", ".avi", ".mov", ".webm", ".flv", ".wmv", ".m4v"
    }
    SUBTITLE_EXTENSIONS = {
        ".srt", ".vtt", ".sub", ".ass"
    }
    SUPPORTED_EXTENSIONS = AUDIO_EXTENSIONS | VIDEO_EXTENSIONS | SUBTITLE_EXTENSIONS

    def __init__(self):
        self.chunker = SmartChunker(chunk_size=350, chunk_overlap=30)

    def can_parse(self, file_path: Path) -> bool:
        return file_path.suffix.lower() in self.SUPPORTED_EXTENSIONS

    def parse(self, file_path: Path) -> ParsedDocument:
        ext = file_path.suffix.lower()
        title = file_path.stem
        metadata: Dict[str, Any] = {"file_type": ext}

        if ext in self.SUBTITLE_EXTENSIONS:
            chunks = self._parse_subtitle(file_path, metadata)
        elif ext in self.AUDIO_EXTENSIONS:
            chunks = self._parse_audio(file_path, metadata)
        else:
            chunks = self._parse_video(file_path, metadata)

        return ParsedDocument(
            path=str(file_path.resolve()),
            filename=file_path.name,
            extension=ext,
            title=title,
            chunks=chunks,
            symbols=[],
            metadata=metadata
        )

    def _extract_path_tokens(self, file_path: Path) -> str:
        """Extracts natural keywords from parent folder hierarchy and filename."""
        stem_parts = re.split(r'[_.\s\-]+', file_path.stem)
        parent_parts = [p for p in file_path.parent.parts[-3:] if not p.startswith(".")]
        combined = list(dict.fromkeys(stem_parts + parent_parts))
        clean_tokens = [t for t in combined if len(t) > 1 and not t.isdigit()]
        return ", ".join(clean_tokens)

    def _format_duration(self, seconds: float) -> str:
        mins = int(seconds // 60)
        secs = int(seconds % 60)
        return f"{mins}:{secs:02d}"

    def _parse_audio(self, file_path: Path, metadata: Dict[str, Any]) -> List[ParsedChunk]:
        """Extracts ID3 and Vorbis tags from audio files (Title, Artist, Album, Year, Genre)."""
        tags_info: Dict[str, str] = {}
        duration_str = ""

        try:
            import mutagen
            audio = mutagen.File(str(file_path))
            if audio is not None:
                if hasattr(audio, "info") and hasattr(audio.info, "length"):
                    metadata["duration_seconds"] = audio.info.length
                    duration_str = self._format_duration(audio.info.length)

                # Mutagen tag extraction across formats
                if hasattr(audio, "tags") and audio.tags:
                    tag_map = {
                        "TIT2": "Title", "title": "Title", "\xa9nam": "Title",
                        "TPE1": "Artist", "artist": "Artist", "\xa9ART": "Artist",
                        "TALB": "Album", "album": "Album", "\xa9alb": "Album",
                        "TCON": "Genre", "genre": "Genre", "\xa9gen": "Genre",
                        "TDRC": "Year", "date": "Year", "\xa9day": "Year",
                    }
                    for raw_key, label in tag_map.items():
                        if raw_key in audio.tags:
                            val = audio.tags[raw_key]
                            if isinstance(val, list):
                                val = ", ".join(str(x) for x in val)
                            tags_info[label] = str(val).strip()
        except Exception as e:
            logger.debug(f"Mutagen audio read skipped for {file_path.name}: {e}")

        path_tokens = self._extract_path_tokens(file_path)

        # Build readable metadata chunk
        lines = [f"Ses Dosyası: {file_path.name}"]
        if "Title" in tags_info:
            lines.append(f"Parça Adı: {tags_info['Title']}")
        if "Artist" in tags_info:
            lines.append(f"Sanatçı: {tags_info['Artist']}")
        if "Album" in tags_info:
            lines.append(f"Albüm: {tags_info['Album']}")
        if "Genre" in tags_info:
            lines.append(f"Müzik Türü: {tags_info['Genre']}")
        if "Year" in tags_info:
            lines.append(f"Yıl: {tags_info['Year']}")
        if duration_str:
            lines.append(f"Süre: {duration_str}")
        if path_tokens:
            lines.append(f"Konum ve Etiketler: {path_tokens}")

        content = "\n".join(lines)
        return [ParsedChunk(
            text=content,
            section_title=tags_info.get("Title") or file_path.name,
            start_line=1,
            end_line=len(lines)
        )]

    def _parse_video(self, file_path: Path, metadata: Dict[str, Any]) -> List[ParsedChunk]:
        """Extracts video metadata and companion subtitle transcripts."""
        video_title = file_path.stem
        path_tokens = self._extract_path_tokens(file_path)
        chunks: List[ParsedChunk] = []

        lines = [f"Video Dosyası: {file_path.name}"]
        try:
            import mutagen
            vid = mutagen.File(str(file_path))
            if vid is not None and hasattr(vid, "info") and hasattr(vid.info, "length"):
                metadata["duration_seconds"] = vid.info.length
                lines.append(f"Süre: {self._format_duration(vid.info.length)}")
        except Exception:
            pass

        if path_tokens:
            lines.append(f"Video Etiketleri ve Konum: {path_tokens}")

        main_chunk = ParsedChunk(
            text="\n".join(lines),
            section_title=file_path.name,
            start_line=1,
            end_line=len(lines)
        )
        chunks.append(main_chunk)

        # Look for companion subtitle files in the same directory
        companion_subs = self._find_companion_subtitles(file_path)
        for sub_path in companion_subs:
            try:
                sub_chunks = self._parse_subtitle(sub_path, {})
                for sc in sub_chunks:
                    sc.section_title = f"{file_path.name} (Altyazı: {sub_path.name})"
                    chunks.append(sc)
                logger.info(f"Attached companion subtitle {sub_path.name} to video {file_path.name}")
            except Exception as e:
                logger.debug(f"Failed to read companion subtitle {sub_path}: {e}")

        return chunks

    def _find_companion_subtitles(self, video_path: Path) -> List[Path]:
        """Finds .srt/.vtt files matching the video name in the same folder."""
        matched: List[Path] = []
        base_stem = video_path.stem.lower()
        parent = video_path.parent

        try:
            for item in parent.iterdir():
                if item.is_file() and item.suffix.lower() in self.SUBTITLE_EXTENSIONS:
                    item_stem = item.stem.lower()
                    if item_stem == base_stem or item_stem.startswith(base_stem):
                        matched.append(item)
        except (PermissionError, FileNotFoundError):
            pass

        return matched

    def _parse_subtitle(self, file_path: Path, metadata: Dict[str, Any]) -> List[ParsedChunk]:
        """Extracts spoken transcript dialogue from subtitle files (.srt, .vtt, .ass)."""
        try:
            with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
        except Exception as e:
            logger.debug(f"Failed reading subtitle {file_path.name}: {e}")
            return []

        # Remove SRT timecodes: 00:00:20,000 --> 00:00:24,400
        clean = re.sub(r'\d{2}:\d{2}:\d{2}[,\.]\d{3}\s*-->\s*\d{2}:\d{2}:\d{2}[,\.]\d{3}', '', content)
        # Remove line numbers alone on a line
        clean = re.sub(r'^\s*\d+\s*$', '', clean, flags=re.MULTILINE)
        # Remove ASS/SSA formatting overrides: {\an8}, etc.
        clean = re.sub(r'\{[^}]+\}', '', clean)
        # Remove HTML-style tags in subtitles: <i>, </b>, <font...>
        clean = re.sub(r'<[^>]+>', '', clean)

        # Normalize multiple spaces and blank lines
        clean = re.sub(r'[ \t]+', ' ', clean)
        clean = re.sub(r'\n\s*\n+', '\n\n', clean).strip()

        if not clean:
            return []

        chunks = self.chunker.chunk_text(
            text=clean,
            section_title=f"Altyazı: {file_path.name}",
            start_line_offset=1
        )
        return chunks
