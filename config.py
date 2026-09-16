"""Configuration settings for Derindex Personal Search Engine."""

import os
from pathlib import Path
from dataclasses import dataclass, field
from typing import Set


@dataclass
class Config:
    # Base paths
    PROJECT_ROOT: Path = Path(__file__).resolve().parent
    DATA_DIR: Path = PROJECT_ROOT / "data"
    DB_PATH: Path = DATA_DIR / "search_index.db"
    VECTORS_PATH: Path = DATA_DIR / "vectors.npz"

    # Supported File Extensions
    SUPPORTED_EXTENSIONS: Set[str] = field(default_factory=lambda: {
        # Documents & Office
        ".pdf",
        ".docx",
        ".doc",
        ".pptx",
        ".ppt",
        ".xlsx",
        ".xls",
        ".odt",
        ".odp",
        ".ods",
        ".rtf",
        ".tex",
        # Text & Markdown
        ".txt",
        ".md",
        ".markdown",
        ".html",
        ".htm",
        ".rst",
        # Code & Scripts
        ".py",
        ".sh",
        ".bash",
        ".zsh",
        ".fish",
        ".c",
        ".cpp",
        ".cc",
        ".cxx",
        ".h",
        ".hpp",
        ".js",
        ".jsx",
        ".ts",
        ".tsx",
        ".rs",
        ".go",
        ".java",
        ".kt",
        ".sql",
        ".ipynb",
        # Config & Data
        ".json",
        ".yaml",
        ".yml",
        ".toml",
        ".ini",
        ".cfg",
        ".conf",
        ".xml",
        ".csv",
        ".tsv",
        ".log",
        # Images & Vectors
        ".png",
        ".jpg",
        ".jpeg",
        ".webp",
        ".gif",
        ".svg",
        ".bmp",
        ".tiff",
    })

    # Sensitive/Ignored Directories (Security & Privacy)
    IGNORED_DIRS: Set[str] = field(default_factory=lambda: {
        ".git",
        ".svn",
        ".hg",
        ".ssh",
        ".gnupg",
        ".aws",
        ".docker",
        ".venv",
        "venv",
        "env",
        "node_modules",
        "__pycache__",
        ".cache",
        ".config",
        ".local",
        "site-packages",
        "dist",
        "build",
        ".idea",
        ".vscode",
    })

    # Sensitive/Ignored File Patterns
    IGNORED_FILE_PATTERNS: Set[str] = field(default_factory=lambda: {
        ".env",
        "id_rsa",
        "id_ed25519",
        "id_dsa",
        "known_hosts",
        "credentials",
        ".pem",
        ".key",
        ".pfx",
        ".p12",
        "authorized_keys",
        ".bash_history",
        ".zsh_history",
    })

    # Maximum file size to index (e.g. 50 MB)
    MAX_FILE_SIZE_BYTES: int = 50 * 1024 * 1024

    # Chunking Hyperparameters
    CHUNK_SIZE_TOKENS: int = 400
    CHUNK_OVERLAP_TOKENS: int = 60
    MIN_CHUNK_TOKENS: int = 20

    # BM25 Hyperparameters
    BM25_K1: float = 1.5
    BM25_B: float = 0.75

    # Hybrid Search Weights
    DEFAULT_ALPHA: float = 0.5  # alpha * bm25 + (1 - alpha) * semantic
    SEARCH_LIMIT_DEFAULT: int = 10

    # Local Embedding Model
    EMBEDDING_MODEL_NAME: str = "all-MiniLM-L6-v2"
    EMBEDDING_DIMENSION: int = 384
    EMBEDDING_BATCH_SIZE: int = 64

    # Web Server Settings
    WEB_HOST: str = "0.0.0.0"
    WEB_PORT: int = 8000

    def ensure_directories(self) -> None:
        """Ensure necessary runtime directories exist."""
        self.DATA_DIR.mkdir(parents=True, exist_ok=True)


config = Config()
config.ensure_directories()
