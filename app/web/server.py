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
from app.embeddings.vector_store import VectorStore
from app.embeddings.embedder import LocalEmbedder
from app.search.hybrid_search import HybridSearcher
from app.search.query_parser import QueryParser
from app.crawler.crawler import Crawler
from app.crawler.watcher import FileWatcher


from contextlib import asynccontextmanager


# Shared singletons to prevent memory de-synchronization
db = Database()
vector_store = VectorStore()
embedder = LocalEmbedder.get_instance()
searcher = HybridSearcher(db=db, vector_store=vector_store, embedder=embedder)
crawler = Crawler(db=db, vector_store=vector_store, embedder=embedder)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initializes background file watcher on startup and shuts it down on exit."""
    watcher = FileWatcher.get_instance(crawler=crawler)
    saved_folders = db.get_watched_folders()
    for folder in saved_folders:
        p = Path(folder).expanduser().resolve()
        if p.exists() and p.is_dir():
            watcher.add_watch_directory(p)
    if watcher.watched_paths and not watcher.observer.is_alive():
        watcher.observer.start()
    yield
    watcher = FileWatcher.get_instance(crawler=crawler)
    watcher.stop()


app = FastAPI(
    title="Derindex API",
    description="Derindex: Personal Search Engine & Semantic Search API",
    version="1.0.0",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Static files directory
STATIC_DIR = Path(__file__).resolve().parent / "static"
STATIC_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


class IndexRequest(BaseModel):
    path: str


@app.api_route("/", methods=["GET", "HEAD"])
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
    page: int = Query(1, ge=1),
    limit: int = Query(15, ge=1, le=100),
    code_only: bool = Query(False)
):
    """Hybrid search endpoint with syntax parsing and pagination."""
    parsed = QueryParser.parse(q)
    results, total_count = searcher.search_paginated(
        query=q,
        page=page,
        limit=limit,
        alpha=alpha,
        code_only=code_only
    )
    total_pages = (total_count + limit - 1) // limit if total_count > 0 else 0
    return {
        "query": q,
        "clean_query": parsed.clean_query,
        "filters": {
            "extensions": sorted(list(parsed.extensions)),
            "exclude_extensions": sorted(list(parsed.exclude_extensions)),
            "path": parsed.path_pattern,
            "type": parsed.file_type,
            "after": parsed.after_timestamp,
            "before": parsed.before_timestamp,
            "symbol": parsed.symbol_filter
        } if parsed.has_filters else None,
        "alpha": alpha,
        "page": page,
        "limit": limit,
        "total": total_count,
        "total_pages": total_pages,
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
    """Trigger indexing for a folder and dynamically register it for automatic background watching."""
    target = Path(req.path).expanduser().resolve()
    if not target.exists() or not target.is_dir():
        raise HTTPException(status_code=400, detail=f"Directory does not exist: {req.path}")

    # 1. Perform initial crawl/index
    stats = crawler.index_directory(target)

    # 2. Persist to database so it stays watched across system reboots
    db.add_watched_folder(str(target))

    # 3. Dynamically register in active watchdog observer
    watcher = FileWatcher.get_instance(crawler=crawler)
    watcher.add_watch_directory(target)

    return {
        "status": "success",
        "indexed_path": str(target),
        "watched": True,
        "stats": stats
    }


@app.get("/api/watched-folders")
def api_get_watched_folders():
    """Retrieve all folders that are persistently registered and actively monitored."""
    watcher = FileWatcher.get_instance(crawler=crawler)
    active_paths = set(watcher.get_watched_paths())
    saved_folders = db.get_watched_folders()

    result = []
    for f_str in saved_folders:
        is_active = False
        # A folder is active if it or any of its parents is in active_paths
        for act in active_paths:
            if f_str == act or f_str.startswith(act.rstrip("/") + "/"):
                is_active = True
                break
        result.append({
            "path": f_str,
            "active": is_active
        })

    return {
        "watched_folders": result,
        "active_roots": sorted(list(active_paths))
    }


@app.delete("/api/watched-folders")
def api_remove_watched_folder(path: str = Query(...)):
    """Remove a folder from persistent watch list and active watchdog observer."""
    target = Path(path).expanduser().resolve()
    p_str = str(target)
    watcher = FileWatcher.get_instance(crawler=crawler)
    watcher.remove_watch_directory(p_str)
    removed = db.remove_watched_folder(p_str)
    return {"status": "success" if removed else "not_found", "removed_path": p_str}
