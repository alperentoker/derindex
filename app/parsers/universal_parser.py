"""Universal Fallback Parser extracting deep semantic metadata for any arbitrary file on the system."""

import re
import os
import mimetypes
import logging
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any

from .base import BaseParser, ParsedDocument, ParsedChunk

logger = logging.getLogger(__name__)

# Common friendly format names for arbitrary extensions
KNOWN_FORMAT_NAMES = {
    ".blend": "Blender 3D Projesi / Modeli",
    ".psd": "Adobe Photoshop Belgesi",
    ".ai": "Adobe Illustrator Vektör Dosyası",
    ".sketch": "Sketch Tasarım Dosyası",
    ".fig": "Figma Tasarım Dosyası",
    ".iso": "CD/DVD Disk İmajı Dosyası",
    ".torrent": "BitTorrent İndirme Meta Dosyası",
    ".apk": "Android Uygulama Paketi (APK)",
    ".deb": "Debian / Ubuntu Kurulum Paketi",
    ".rpm": "RedHat / Fedora Kurulum Paketi",
    ".AppImage": "Linux Taşınabilir Uygulama Paketi",
    ".cad": "CAD Çizim ve Tasarım Dosyası",
    ".dwg": "AutoCAD Teknik Çizim Dosyası",
    ".dxf": "AutoCAD Değişim Formatı",
    ".stl": "3D Yazıcı / Mesh Model Dosyası",
    ".obj": "3D Wavefront Nesne Modeli",
    ".fbx": "Autodesk 3D Animasyon Modeli",
    ".gltf": "GLTF 3D Vektör Sahnesi",
    ".glb": "GLB İkili 3D Model",
    ".unitypackage": "Unity Oyun Motoru Paketi",
    ".unreal": "Unreal Engine Proje Varlığı",
    ".exe": "Windows Çalıştırılabilir Program",
    ".msi": "Windows Kurulum Paketi",
    ".dmg": "macOS Disk İmajı",
    ".pkg": "macOS / BSD Kurulum Paketi",
    ".sqlite": "SQLite Veritabanı Dosyası",
    ".db": "Veritabanı Dosyası",
    ".bak": "Sistem / Veri Yedek Dosyası",
    ".dump": "Veritabanı Dökümü",
    ".cert": "Güvenlik Sertifikası",
    ".crt": "SSL / TLS Sertifika Dosyası",
}


class UniversalFallbackParser(BaseParser):
    def can_parse(self, file_path: Path) -> bool:
        # Accepts any file that exists
        return file_path.is_file()

    def parse(self, file_path: Path) -> ParsedDocument:
        ext = file_path.suffix.lower()
        title = file_path.stem

        # 1. File size formatting
        try:
            stat = file_path.stat()
            size_bytes = stat.st_size
            mtime_dt = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            size_bytes = 0
            mtime_dt = "Bilinmiyor"

        if size_bytes >= 1024 * 1024 * 1024:
            size_str = f"{size_bytes / (1024 ** 3):.2f} GB"
        elif size_bytes >= 1024 * 1024:
            size_str = f"{size_bytes / (1024 ** 2):.1f} MB"
        elif size_bytes >= 1024:
            size_str = f"{size_bytes / 1024:.1f} KB"
        else:
            size_str = f"{size_bytes} Bayt"

        # 2. Friendly format / MIME name
        friendly_fmt = KNOWN_FORMAT_NAMES.get(ext)
        if not friendly_fmt:
            mime, _ = mimetypes.guess_type(str(file_path))
            if mime:
                friendly_fmt = f"{mime} ({ext})"
            else:
                friendly_fmt = f"{ext.upper().lstrip('.')} Dosyası" if ext else "Bilinmeyen Dosya"

        # 3. Decompose filename & path into semantic natural keywords
        stem_raw = file_path.stem
        # Split camelCase and snake_case
        sub_tokens = re.findall(r'[A-Z]?[a-z0-9ğüşıöç]+|[A-Z]+(?=[A-Z][a-z]|\b)', stem_raw)
        path_folders = [p for p in file_path.parent.parts[-4:] if not p.startswith(".")]

        all_keywords = list(dict.fromkeys([t.lower() for t in sub_tokens + path_folders if len(t) > 1 and not t.isdigit()]))
        keywords_str = ", ".join(all_keywords)

        folder_hierarchy = " > ".join(path_folders) if path_folders else "Kök Dizin"

        # 4. Check if file is small and text-readable (peek up to 2KB)
        sample_text = ""
        if size_bytes <= 1024 * 1024:  # Under 1MB
            try:
                with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                    snippet = f.read(2048).strip()
                    # Check if mostly printable text
                    printable_ratio = sum(c.isprintable() or c.isspace() for c in snippet) / max(1, len(snippet))
                    if printable_ratio > 0.90 and len(snippet) > 10:
                        sample_text = re.sub(r'\s+', ' ', snippet)[:500]
            except Exception:
                pass

        # 5. Build rich semantic content chunk
        lines = [
            f"Dosya Adı: {file_path.name}",
            f"Dosya Türü: {friendly_fmt}",
            f"Klasör Konumu: {folder_hierarchy}",
            f"Boyut: {size_str} | Son Değiştirilme: {mtime_dt}",
        ]
        if keywords_str:
            lines.append(f"Anlamsal Etiketler: {keywords_str}")
        if sample_text:
            lines.append(f"İçerik Özeti: {sample_text}")

        content = "\n".join(lines)
        chunk = ParsedChunk(
            text=content,
            section_title=file_path.name,
            start_line=1,
            end_line=len(lines)
        )

        return ParsedDocument(
            path=str(file_path.resolve()),
            filename=file_path.name,
            extension=ext,
            title=title,
            chunks=[chunk],
            symbols=[],
            metadata={"format": friendly_fmt, "size_str": size_str}
        )
