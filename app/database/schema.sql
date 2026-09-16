-- Derindex SQLite Database Schema
PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;

CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    path TEXT UNIQUE NOT NULL,
    filename TEXT NOT NULL,
    extension TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    mtime REAL NOT NULL,
    indexed_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_documents_path ON documents(path);
CREATE INDEX IF NOT EXISTS idx_documents_sha256 ON documents(sha256);

CREATE TABLE IF NOT EXISTS chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id INTEGER NOT NULL,
    chunk_index INTEGER NOT NULL,
    text TEXT NOT NULL,
    page_number INTEGER,
    section_title TEXT,
    code_type TEXT,
    symbol_name TEXT,
    start_line INTEGER,
    end_line INTEGER,
    token_count INTEGER NOT NULL,
    FOREIGN KEY(doc_id) REFERENCES documents(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_chunks_doc_id ON chunks(doc_id);
CREATE INDEX IF NOT EXISTS idx_chunks_symbol ON chunks(symbol_name);
CREATE INDEX IF NOT EXISTS idx_chunks_code_type ON chunks(code_type);

CREATE TABLE IF NOT EXISTS symbols (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id INTEGER NOT NULL,
    chunk_id INTEGER,
    name TEXT NOT NULL,
    kind TEXT NOT NULL,
    line_number INTEGER NOT NULL,
    FOREIGN KEY(doc_id) REFERENCES documents(id) ON DELETE CASCADE,
    FOREIGN KEY(chunk_id) REFERENCES chunks(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_symbols_name ON symbols(name);
CREATE INDEX IF NOT EXISTS idx_symbols_kind ON symbols(kind);

CREATE TABLE IF NOT EXISTS inverted_index (
    term TEXT NOT NULL,
    doc_id INTEGER NOT NULL,
    chunk_id INTEGER NOT NULL,
    term_freq INTEGER NOT NULL,
    PRIMARY KEY (term, chunk_id),
    FOREIGN KEY(doc_id) REFERENCES documents(id) ON DELETE CASCADE,
    FOREIGN KEY(chunk_id) REFERENCES chunks(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_inverted_term ON inverted_index(term);
CREATE INDEX IF NOT EXISTS idx_inverted_chunk_id ON inverted_index(chunk_id);

CREATE TABLE IF NOT EXISTS term_stats (
    term TEXT PRIMARY KEY,
    doc_freq INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS doc_stats (
    doc_id INTEGER PRIMARY KEY,
    total_tokens INTEGER NOT NULL,
    FOREIGN KEY(doc_id) REFERENCES documents(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS metadata_kv (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
