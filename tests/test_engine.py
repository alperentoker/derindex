"""Unit tests for Derindex indexing, BM25, TF-IDF, AST parsing, and database."""

import os
import tempfile
import unittest
from pathlib import Path

from app.indexing.tokenizer import Tokenizer
from app.indexing.bm25 import BM25Engine
from app.indexing.tfidf import TFIDFEngine
from app.database.db import Database
from app.database.models import DocumentRecord, ChunkRecord, InvertedIndexRecord
from app.parsers.code_parser import CodeParser
from app.parsers.markdown_parser import MarkdownParser
from app.crawler.crawler import Crawler, compute_sha256
from app.search.hybrid_search import HybridSearcher


class TestTokenizer(unittest.TestCase):
    def setUp(self):
        self.tokenizer = Tokenizer(remove_stopwords=True)

    def test_basic_tokenization(self):
        tokens = self.tokenizer.tokenize("Linux kernel memory management architecture")
        self.assertIn("linux", tokens)
        self.assertIn("kernel", tokens)
        self.assertIn("memory", tokens)
        self.assertIn("management", tokens)

    def test_turkish_characters(self):
        tokens = self.tokenizer.tokenize("İşletim sistemi bellek tahsisi ve süreç yönetimi")
        self.assertIn("işletim", tokens)
        self.assertIn("sistemi", tokens)
        self.assertIn("bellek", tokens)
        # "ve" is a stopword and should be removed
        self.assertNotIn("ve", tokens)

    def test_code_symbol_splitting(self):
        tokens = self.tokenizer.tokenize("MemoryManager allocate_memory_block")
        self.assertIn("memorymanager", tokens)
        self.assertIn("memory", tokens)
        self.assertIn("allocate_memory_block", tokens)
        self.assertIn("allocate", tokens)
        self.assertIn("block", tokens)


class TestASTCodeParser(unittest.TestCase):
    def setUp(self):
        self.parser = CodeParser()
        self.temp_dir = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_python_ast_extraction(self):
        code_file = Path(self.temp_dir.name) / "test_module.py"
        code_content = """import os
from sys import path

class CacheManager:
    \"\"\"Manages LRU Cache storage.\"\"\"
    def __init__(self, size: int):
        self.size = size

    def evict_oldest(self):
        pass

def calculate_checksum(data: bytes) -> str:
    return "abc"
"""
        code_file.write_text(code_content, encoding="utf-8")
        parsed = self.parser.parse(code_file)

        self.assertEqual(parsed.filename, "test_module.py")
        symbol_names = [s.name for s in parsed.symbols]

        self.assertIn("os", symbol_names)
        self.assertIn("CacheManager", symbol_names)
        self.assertIn("calculate_checksum", symbol_names)

        # Check line ranges
        cache_chunk = next((c for c in parsed.chunks if c.symbol_name == "CacheManager"), None)
        self.assertIsNotNone(cache_chunk)
        self.assertGreaterEqual(cache_chunk.start_line, 4)


class TestMarkdownParser(unittest.TestCase):
    def setUp(self):
        self.parser = MarkdownParser()
        self.temp_dir = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_heading_hierarchy(self):
        md_file = Path(self.temp_dir.name) / "notes.md"
        content = """# Operating Systems

## Virtual Memory
Virtual memory provides an abstraction of storage.

### Paging Mechanism
Pages are mapped using page tables.
"""
        md_file.write_text(content, encoding="utf-8")
        parsed = self.parser.parse(md_file)

        self.assertGreater(len(parsed.chunks), 0)
        sections = [c.section_title for c in parsed.chunks if c.section_title]
        # Verify hierarchical heading inclusion
        self.assertTrue(any("Virtual Memory" in s for s in sections))


class TestDatabaseAndIR(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test.db"
        self.db = Database(self.db_path)
        self.bm25 = BM25Engine(self.db)
        self.tfidf = TFIDFEngine(self.db)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_document_and_bm25_ranking(self):
        # Insert test document 1
        d1 = DocumentRecord(None, "/path/doc1.txt", "doc1.txt", ".txt", "sha1", 100, 1.0, "2026-01-01")
        doc1_id = self.db.insert_document(d1)

        c1 = ChunkRecord(None, doc1_id, 0, "linux operating system kernel process scheduling", token_count=6)
        c1_id = self.db.insert_chunks([c1])[0]

        # Insert test document 2
        d2 = DocumentRecord(None, "/path/doc2.txt", "doc2.txt", ".txt", "sha2", 100, 1.0, "2026-01-01")
        doc2_id = self.db.insert_document(d2)

        c2 = ChunkRecord(None, doc2_id, 0, "python web development framework database query", token_count=6)
        c2_id = self.db.insert_chunks([c2])[0]

        # Insert inverted index entries
        postings = [
            InvertedIndexRecord("linux", doc1_id, c1_id, 2),
            InvertedIndexRecord("kernel", doc1_id, c1_id, 1),
            InvertedIndexRecord("process", doc1_id, c1_id, 1),
            InvertedIndexRecord("python", doc2_id, c2_id, 2),
            InvertedIndexRecord("database", doc2_id, c2_id, 1),
        ]
        self.db.insert_postings(postings)
        self.db.update_term_stats()

        # Score BM25 query for "linux kernel"
        bm25_scores = self.bm25.score_query("linux kernel")
        self.assertTrue(len(bm25_scores) > 0)
        top_chunk_id, top_score = bm25_scores[0]
        self.assertEqual(top_chunk_id, c1_id)
        self.assertGreater(top_score, 0.0)

        # Score TF-IDF query for "python"
        tfidf_scores = self.tfidf.score_query("python")
        self.assertTrue(len(tfidf_scores) > 0)
        self.assertEqual(tfidf_scores[0][0], c2_id)


class TestIncrementalHashing(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_sha256_detection(self):
        test_file = Path(self.temp_dir.name) / "sample.txt"
        test_file.write_text("initial content", encoding="utf-8")
        hash1 = compute_sha256(test_file)

        # Hash of identical content should match
        self.assertEqual(hash1, compute_sha256(test_file))

        # Modified content must change hash
        test_file.write_text("updated content", encoding="utf-8")
        hash2 = compute_sha256(test_file)
        self.assertNotEqual(hash1, hash2)


class TestExtendedParsers(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_shell_script_parsing(self):
        from app.parsers.code_parser import CodeParser
        parser = CodeParser()
        sh_file = self.root / "deploy.sh"
        sh_file.write_text("""#!/usr/bin/env bash
export APP_PORT=8000

backup_database() {
    echo "Backing up..."
}

function start_server {
    echo "Starting..."
}
""", encoding="utf-8")

        parsed = parser.parse(sh_file)
        self.assertEqual(parsed.filename, "deploy.sh")
        symbol_names = [s.name for s in parsed.symbols]
        self.assertIn("backup_database", symbol_names)
        self.assertIn("start_server", symbol_names)
        self.assertIn("APP_PORT", symbol_names)

    def test_office_docx_parsing(self):
        from app.parsers.office_parser import OfficeParser
        parser = OfficeParser()
        docx_file = self.root / "report.docx"

        # Build valid docx zip
        import zipfile
        with zipfile.ZipFile(docx_file, "w") as z:
            z.writestr("word/document.xml", """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
    <w:body>
        <w:p><w:r><w:t>Linux Kernel Memory Architecture</w:t></w:r></w:p>
        <w:p><w:r><w:t>Virtual memory paging and TLB caching.</w:t></w:r></w:p>
    </w:body>
</w:document>""")

        parsed = parser.parse(docx_file)
        self.assertGreater(len(parsed.chunks), 0)
        self.assertTrue(any("Linux Kernel Memory Architecture" in c.text for c in parsed.chunks))

    def test_office_pptx_parsing(self):
        from app.parsers.office_parser import OfficeParser
        parser = OfficeParser()
        pptx_file = self.root / "presentation.pptx"

        import zipfile
        with zipfile.ZipFile(pptx_file, "w") as z:
            z.writestr("ppt/slides/slide1.xml", """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">
    <a:t>High Performance Database Engines</a:t>
</p:sld>""")

        parsed = parser.parse(pptx_file)
        self.assertGreater(len(parsed.chunks), 0)
        self.assertTrue(any("High Performance Database Engines" in c.text for c in parsed.chunks))

    def test_image_svg_parsing(self):
        from app.parsers.image_parser import ImageParser
        parser = ImageParser()
        svg_file = self.root / "diagram.svg"
        svg_file.write_text("""<svg xmlns="http://www.w3.org/2000/svg">
    <title>Microservice Topology</title>
    <text x="20" y="40">Distributed Cache Cluster</text>
</svg>""", encoding="utf-8")

        parsed = parser.parse(svg_file)
        self.assertGreater(len(parsed.chunks), 0)
        self.assertTrue(any("Distributed Cache Cluster" in c.text for c in parsed.chunks))

    def test_image_bitmap_metadata(self):
        from app.parsers.image_parser import ImageParser
        from PIL import Image
        parser = ImageParser()
        png_file = self.root / "screenshot_dashboard_analytics.png"
        img = Image.new("RGB", (800, 600), color="blue")
        img.save(png_file)

        parsed = parser.parse(png_file)
        self.assertGreater(len(parsed.chunks), 0)
        chunk_text = parsed.chunks[0].text
        self.assertIn("800x600", chunk_text)
        self.assertIn("dashboard", chunk_text)
        self.assertIn("analytics", chunk_text)

    def test_csv_and_toml_parsing(self):
        from app.parsers.text_parser import TextParser
        parser = TextParser()

        # CSV test
        csv_file = self.root / "servers.csv"
        csv_file.write_text("""hostname,ip_address,role
srv-node-01,192.168.1.10,primary_database
srv-node-02,192.168.1.11,cache_replica
""", encoding="utf-8")
        parsed_csv = parser.parse(csv_file)
        self.assertTrue(any("primary_database" in c.text for c in parsed_csv.chunks))

        # TOML test
        toml_file = self.root / "config.toml"
        toml_file.write_text("""[database]
host = "localhost"
port = 5432
""", encoding="utf-8")
        parsed_toml = parser.parse(toml_file)
        self.assertTrue(any("localhost" in c.text for c in parsed_toml.chunks))


if __name__ == "__main__":
    unittest.main()

