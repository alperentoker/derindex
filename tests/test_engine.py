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
        self.assertIn("manager", tokens)
        self.assertIn("allocate_memory_block", tokens)
        self.assertIn("allocate", tokens)
        self.assertIn("block", tokens)

        # Test Turkish camelCase
        tr_tokens = self.tokenizer.tokenize("İslemYoneticisi")
        self.assertIn("islem", tr_tokens)
        self.assertIn("yoneticisi", tr_tokens)


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

    def test_atomic_document_indexing(self):
        from app.database.models import SymbolRecord
        d = DocumentRecord(None, "/path/atomic_doc.py", "atomic_doc.py", ".py", "sha_atomic", 200, 1.0, "2026-01-01")
        chunks = [
            ChunkRecord(None, 0, 0, "def calculate(): return 42", token_count=5),
            ChunkRecord(None, 0, 1, "class Engine: pass", token_count=4),
        ]
        chunk_symbols = [
            [SymbolRecord(None, 0, 0, "calculate", "function", 1)],
            [SymbolRecord(None, 0, 0, "Engine", "class", 2)]
        ]
        chunk_postings = [
            [InvertedIndexRecord("calculate", 0, 0, 1)],
            [InvertedIndexRecord("engine", 0, 0, 1)]
        ]

        doc_id, chunk_ids = self.db.save_indexed_document_atomic(d, chunks, chunk_symbols, chunk_postings)
        self.assertIsNotNone(doc_id)
        self.assertEqual(len(chunk_ids), 2)

        # Verify symbols and postings exist
        symbols = self.db.find_symbols("calculate")
        self.assertEqual(len(symbols), 1)
        self.assertEqual(symbols[0]["name"], "calculate")

    def test_atomic_reindex_chunk_cleanup(self):
        # When a file is modified and re-indexed, old chunks must not accumulate
        from app.database.models import SymbolRecord
        d = DocumentRecord(None, "/path/reindex_doc.py", "reindex_doc.py", ".py", "sha_v1", 100, 1.0, "2026-01-01")
        chunks_v1 = [ChunkRecord(None, 0, 0, "old chunk content", token_count=3)]
        doc_id, c_ids_v1 = self.db.save_indexed_document_atomic(d, chunks_v1, [[]], [[]])

        # Re-index with new content
        d.sha256 = "sha_v2"
        chunks_v2 = [
            ChunkRecord(None, 0, 0, "new chunk content 1", token_count=4),
            ChunkRecord(None, 0, 1, "new chunk content 2", token_count=4)
        ]
        doc_id_2, c_ids_v2 = self.db.save_indexed_document_atomic(d, chunks_v2, [[], []], [[], []])

        self.assertEqual(doc_id, doc_id_2)
        # Verify exactly 2 chunks exist for this doc, not 1 + 2 = 3
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM chunks WHERE doc_id = ?", (doc_id,))
            count = cursor.fetchone()[0]
            self.assertEqual(count, 2)


    def test_reranker_idempotence(self):
        from app.search.reranker import Reranker
        from app.database.models import SearchResult
        reranker = Reranker()
        results = [
            SearchResult(chunk_id=1, doc_id=1, filename="main.py", path="/main.py", score=0.4, matched_snippet="main snippet", symbol_name="İslemYoneticisi")
        ]
        reranker.rerank(results, "islem")
        first_score = results[0].score

        # Calling rerank multiple times must not inflate score
        reranker.rerank(results, "islem")
        reranker.rerank(results, "islem")
        self.assertEqual(results[0].score, first_score)

    def test_optimized_bm25_stats_and_chunk_lengths(self):
        # Insert a test doc with chunks
        d = DocumentRecord(None, "/path/opt_doc.txt", "opt_doc.txt", ".txt", "sha_opt", 100, 1.0, "2026-01-01")
        doc_id = self.db.insert_document(d)
        c1 = ChunkRecord(None, doc_id, 0, "token count test one", token_count=4)
        c2 = ChunkRecord(None, doc_id, 1, "token count test two extra", token_count=5)
        c_ids = self.db.insert_chunks([c1, c2])

        total_chunks, avgdl = self.db.get_bm25_corpus_summary()
        self.assertGreaterEqual(total_chunks, 2)
        self.assertGreater(avgdl, 0.0)

        # Verify selective chunk length lookup
        lengths = self.db.get_chunk_lengths([c_ids[0]])
        self.assertEqual(len(lengths), 1)
        self.assertEqual(lengths[c_ids[0]], 4)

    def test_incremental_term_stats(self):
        d = DocumentRecord(None, "/path/inc_doc.txt", "inc_doc.txt", ".txt", "sha_inc", 100, 1.0, "2026-01-01")
        doc_id = self.db.insert_document(d)
        c = ChunkRecord(None, doc_id, 0, "asynchronous concurrency", token_count=2)
        c_id = self.db.insert_chunks([c])[0]
        postings = [
            InvertedIndexRecord("asynchronous", doc_id, c_id, 1),
            InvertedIndexRecord("concurrency", doc_id, c_id, 1)
        ]
        self.db.insert_postings(postings)

        # Update stats ONLY for these terms
        self.db.update_term_stats_for_terms(["asynchronous", "concurrency"])
        dfs = self.db.get_term_doc_frequencies(["asynchronous", "concurrency"])
        self.assertEqual(dfs.get("asynchronous"), 1)
        self.assertEqual(dfs.get("concurrency"), 1)


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


class TestMediaAndUniversalParsers(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_audio_metadata_parsing(self):
        from app.parsers.media_parser import MediaParser
        parser = MediaParser()
        audio_file = self.root / "rock_duman_haberin_yok.mp3"
        audio_file.write_bytes(b"ID3\x03\x00\x00\x00\x00\x00\x00" + b"\x00" * 100)

        parsed = parser.parse(audio_file)
        self.assertEqual(parsed.filename, "rock_duman_haberin_yok.mp3")
        self.assertGreater(len(parsed.chunks), 0)
        chunk_text = parsed.chunks[0].text
        self.assertIn("duman", chunk_text.lower())
        self.assertIn("haberin", chunk_text.lower())

    def test_video_and_companion_subtitles(self):
        from app.parsers.media_parser import MediaParser
        parser = MediaParser()

        # Create dummy video file
        vid_file = self.root / "linux_kernel_lecture.mp4"
        vid_file.write_bytes(b"\x00" * 50)

        # Create matching companion subtitle file
        sub_file = self.root / "linux_kernel_lecture.srt"
        sub_file.write_text("""1
00:00:05,000 --> 00:00:10,000
Bugun sanal bellek ve page table mimarisini inceliyoruz.

2
00:00:11,000 --> 00:00:16,000
Translation Lookaside Buffer erisim surelerini kisaltir.
""", encoding="utf-8")

        parsed = parser.parse(vid_file)
        self.assertGreaterEqual(len(parsed.chunks), 2)
        all_text = " ".join(c.text for c in parsed.chunks)
        self.assertIn("sanal bellek", all_text)
        self.assertIn("Translation Lookaside Buffer", all_text)

    def test_archive_toc_extraction(self):
        from app.parsers.archive_parser import ArchiveParser
        parser = ArchiveParser()

        zip_file = self.root / "source_backup.zip"
        import zipfile
        with zipfile.ZipFile(zip_file, "w") as z:
            z.writestr("backend/api/server.py", "print('hello')")
            z.writestr("frontend/src/index.html", "<h1>App</h1>")
            z.writestr("database/schema.sql", "CREATE TABLE users;")

        parsed = parser.parse(zip_file)
        self.assertGreater(len(parsed.chunks), 0)
        chunk_text = parsed.chunks[0].text
        self.assertIn("backend/api/server.py", chunk_text)
        self.assertIn("database/schema.sql", chunk_text)

    def test_universal_fallback_arbitrary_file(self):
        from app.parsers import get_parser_for_file
        blend_file = self.root / "karakter_animasyon_robot.blend"
        blend_file.write_bytes(b"BLENDER_V300" + b"\x00" * 100)

        parser = get_parser_for_file(blend_file)
        self.assertIsNotNone(parser)
        parsed = parser.parse(blend_file)
        self.assertGreater(len(parsed.chunks), 0)
        chunk_text = parsed.chunks[0].text
        self.assertIn("Blender", chunk_text)
        self.assertIn("karakter", chunk_text.lower())
        self.assertIn("animasyon", chunk_text.lower())


class TestQueryParser(unittest.TestCase):
    def test_syntax_operators_parsing(self):
        from app.search.query_parser import QueryParser
        raw = 'auth memory allocation ext:py,ts -ext:tmp path:"app/services" type:code after:2026-01-01 symbol:AuthHandler'
        parsed = QueryParser.parse(raw)

        self.assertEqual(parsed.clean_query, "auth memory allocation")
        self.assertEqual(parsed.extensions, {".py", ".ts"})
        self.assertEqual(parsed.exclude_extensions, {".tmp"})
        self.assertEqual(parsed.path_pattern, "app/services")
        self.assertEqual(parsed.file_type, "code")
        self.assertEqual(parsed.symbol_filter, "AuthHandler")
        self.assertIsNotNone(parsed.after_timestamp)

    def test_multilingual_clean_query_preservation(self):
        from app.search.query_parser import QueryParser
        # Test English, Turkish, and camelCase together
        raw = "İşlemHavuzu workerThreadPool ext:py"
        parsed = QueryParser.parse(raw)
        self.assertEqual(parsed.clean_query, "İşlemHavuzu workerThreadPool")
        self.assertEqual(parsed.extensions, {".py"})

    def test_matches_filters_logic(self):
        from app.search.query_parser import QueryParser
        raw = "ext:pdf path:docs/ after:2026-01-01"
        parsed = QueryParser.parse(raw)

        # Match case
        self.assertTrue(QueryParser.matches_filters(
            parsed_query=parsed,
            doc_path="/home/user/docs/manual.pdf",
            doc_extension=".pdf",
            doc_mtime=1800000000.0
        ))

        # Extension mismatch
        self.assertFalse(QueryParser.matches_filters(
            parsed_query=parsed,
            doc_path="/home/user/docs/manual.docx",
            doc_extension=".docx",
            doc_mtime=1800000000.0
        ))

        # Path mismatch
        self.assertFalse(QueryParser.matches_filters(
            parsed_query=parsed,
            doc_path="/home/user/images/manual.pdf",
            doc_extension=".pdf",
            doc_mtime=1800000000.0
        ))


class TestVectorStoreScaling(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store_path = Path(self.temp_dir.name) / "test_vectors.npz"

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_l2_normalization_on_add(self):
        import numpy as np
        from app.embeddings.vector_store import VectorStore

        store = VectorStore(storage_path=self.store_path)
        # Add unnormalized vectors
        raw_vecs = np.array([[3.0, 4.0] + [0.0] * 382, [0.0, 5.0] + [0.0] * 382], dtype=np.float32)
        chunk_ids = [101, 102]
        store.add_vectors(chunk_ids, raw_vecs, save_to_disk=False)

        # Confirm stored vectors have unit norm (approx 1.0)
        norm_1 = np.linalg.norm(store.matrix[0])
        norm_2 = np.linalg.norm(store.matrix[1])
        self.assertAlmostEqual(float(norm_1), 1.0, places=5)
        self.assertAlmostEqual(float(norm_2), 1.0, places=5)

    def test_candidate_pre_filtering_search(self):
        import numpy as np
        from app.embeddings.vector_store import VectorStore

        store = VectorStore(storage_path=self.store_path)
        vecs = np.array([
            [1.0, 0.0] + [0.0] * 382,
            [0.0, 1.0] + [0.0] * 382,
            [0.707, 0.707] + [0.0] * 382
        ], dtype=np.float32)
        store.add_vectors([1, 2, 3], vecs, save_to_disk=False)

        query_vec = np.array([1.0, 0.0] + [0.0] * 382, dtype=np.float32)

        # Search with candidate filter restricting to chunk 2 only
        results = store.search(query_vec, top_k=5, candidate_ids={2})
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0][0], 2)


class TestHybridSearchFiltersAndRecency(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(self.temp_dir.name) / "test.db"
        self.db = Database(db_path=db_path)
        self.searcher = HybridSearcher(db=self.db)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_hybrid_search_filter_and_recency(self):
        import time
        now = time.time()

        # Insert recent python document
        doc_py = DocumentRecord(
            id=None,
            path="/project/src/cache.py",
            filename="cache.py",
            extension=".py",
            sha256="sha_py",
            size_bytes=500,
            mtime=now - 3600,  # 1 hour ago (within 7 days)
            indexed_at="2026-09-17"
        )
        pid = self.db.insert_document(doc_py)
        c_py = ChunkRecord(
            id=None,
            doc_id=pid,
            chunk_index=0,
            text="class FastCache: def get(key): return key",
            code_type="python",
            symbol_name="FastCache"
        )
        c_py_id = self.db.insert_chunks([c_py])[0]

        # Insert older docx document
        doc_old = DocumentRecord(
            id=None,
            path="/project/docs/cache_spec.docx",
            filename="cache_spec.docx",
            extension=".docx",
            sha256="sha_docx",
            size_bytes=500,
            mtime=now - (60 * 86400),  # 60 days ago
            indexed_at="2026-09-17"
        )
        oid = self.db.insert_document(doc_old)
        c_old = ChunkRecord(
            id=None,
            doc_id=oid,
            chunk_index=0,
            text="Cache specification and architecture overview",
            code_type=None
        )
        c_old_id = self.db.insert_chunks([c_old])[0]

        # Add BM25 term index
        self.db.insert_postings([
            InvertedIndexRecord(term="cache", doc_id=pid, chunk_id=c_py_id, term_freq=2),
            InvertedIndexRecord(term="cache", doc_id=oid, chunk_id=c_old_id, term_freq=2)
        ])
        self.db.update_term_stats_for_terms({"cache"})


        # Search with extension filter
        res_filtered, count = self.searcher.search_paginated("cache ext:py")
        self.assertEqual(count, 1)
        self.assertEqual(res_filtered[0].filename, "cache.py")
        # Check recency boost was applied
        self.assertGreaterEqual(res_filtered[0].recency_boost, 0.08)


class TestWatchedFoldersAndDynamicWatcher(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)

        from app.database.db import Database
        self.db = Database(db_path=self.root / "test.db")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_watched_folders_db_operations(self):
        folder1 = str(self.root / "docs")
        folder2 = str(self.root / "projects")

        self.db.add_watched_folder(folder1)
        self.db.add_watched_folder(folder2)

        watched = self.db.get_watched_folders()
        self.assertEqual(len(watched), 2)
        self.assertIn(str(Path(folder1).resolve()), watched)

        # Duplicate should be ignored
        self.db.add_watched_folder(folder1)
        self.assertEqual(len(self.db.get_watched_folders()), 2)

        # Remove
        self.db.remove_watched_folder(folder1)
        self.assertEqual(len(self.db.get_watched_folders()), 1)

    def test_dynamic_watcher_parent_child_subsumption(self):
        from app.crawler.watcher import FileWatcher
        from app.crawler.crawler import Crawler

        crawler = Crawler(db=self.db)
        watcher = FileWatcher(crawler=crawler)

        parent = self.root / "alperen"
        parent.mkdir(parents=True, exist_ok=True)
        child = parent / "Belgeler"
        child.mkdir(parents=True, exist_ok=True)

        # First add child
        success_child = watcher.add_watch_directory(child)
        self.assertTrue(success_child)
        self.assertIn(str(child.resolve()), watcher.get_watched_paths())

        # Now add parent - child watch should be subsumed
        success_parent = watcher.add_watch_directory(parent)
        self.assertTrue(success_parent)

        watched = watcher.get_watched_paths()
        self.assertIn(str(parent.resolve()), watched)
        # Child should have been removed since parent recursively covers it
        self.assertNotIn(str(child.resolve()), watched)

        watcher.stop()

    def test_api_endpoints_watched_folders(self):
        from fastapi.testclient import TestClient
        from app.web.server import app, db

        client = TestClient(app)

        test_folder = self.root / "api_test_docs"
        test_folder.mkdir(parents=True, exist_ok=True)
        (test_folder / "sample.txt").write_text("Hello dynamic watcher test", encoding="utf-8")

        # 1. Post index
        res = client.post("/api/index", json={"path": str(test_folder)})
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["status"], "success")
        self.assertTrue(data["watched"])

        # 2. Get watched folders
        res_get = client.get("/api/watched-folders")
        self.assertEqual(res_get.status_code, 200)
        watched_list = res_get.json()["watched_folders"]
        self.assertTrue(any(w["path"] == str(test_folder.resolve()) for w in watched_list))

        # 3. Delete watched folder
        res_del = client.delete(f"/api/watched-folders?path={test_folder.resolve()}")
        self.assertEqual(res_del.status_code, 200)
        self.assertEqual(res_del.json()["status"], "success")


if __name__ == "__main__":
    unittest.main()


