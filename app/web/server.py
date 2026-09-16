"""FastAPI backend server for Derindex search and indexing."""

from pathlib import Path
from typing import Optional, List, Dict, Any
from pydantic import BaseModel

from fastapi import FastAPI, Query, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware

from config import config
from app.database.db import Database
from app.search.hybrid_search import HybridSearcher
from app.crawler.crawler import Crawler

app = FastAPI(
    title="Derindex API",
    description="Derindex: Personal Search Engine & Semantic Search API",
    version="1.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Shared singletons
db = Database()
searcher = HybridSearcher(db=db)
crawler = Crawler(db=db)

# Static files directory
STATIC_DIR = Path(__file__).resolve().parent / "static"
STATIC_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


class IndexRequest(BaseModel):
    path: str


@app.get("/")
def serve_index():
    index_file = STATIC_DIR / "index.html"
    if not index_file.exists():
        raise HTTPException(status_code=404, detail="Web UI static files not found")
    return FileResponse(index_file)


@app.api_route("/favicon.ico", methods=["GET", "HEAD"])
def serve_favicon():
    favicon_file = STATIC_DIR / "favicon.ico"
    if not favicon_file.exists():
        raise HTTPException(status_code=404, detail="Favicon not found")
    return FileResponse(favicon_file, media_type="image/x-icon")


@app.get("/api/search")
def api_search(
    q: str = Query(..., min_length=1),
    alpha: float = Query(0.5, ge=0.0, le=1.0),
    limit: int = Query(10, ge=1, le=100),
    code_only: bool = Query(False)
):
    """Hybrid search endpoint."""
    results = searcher.search(query=q, alpha=alpha, limit=limit, code_only=code_only)
    return {
        "query": q,
        "alpha": alpha,
        "count": len(results),
        "results": [r.to_dict() for r in results]
    }


@app.get("/api/code")
def api_code_search(
    q: str = Query(..., min_length=1),
    limit: int = Query(10, ge=1, le=50)
):
    """Code-specific search endpoint."""
    results = searcher.search_code(query=q, limit=limit)
    return {
        "query": q,
        "count": len(results),
        "results": [r.to_dict() for r in results]
    }


@app.get("/api/symbol")
def api_symbol_search(
    q: str = Query(..., min_length=1),
    limit: int = Query(20, ge=1, le=50)
):
    """Direct symbol search endpoint."""
    symbols = searcher.search_symbol(symbol_name=q, limit=limit)
    return {
        "query": q,
        "count": len(symbols),
        "symbols": symbols
    }


@app.get("/api/stats")
def api_stats():
    """System and index statistics."""
    st = db.get_stats()
    return {"stats": st}


@app.post("/api/index")
def api_index_directory(req: IndexRequest):
    """Trigger indexing for a folder."""
    target = Path(req.path).expanduser().resolve()
    if not target.exists() or not target.is_dir():
        raise HTTPException(status_code=400, detail=f"Directory does not exist: {req.path}")
    stats = crawler.index_directory(target)
    return {"status": "success", "indexed_path": str(target), "stats": stats}
