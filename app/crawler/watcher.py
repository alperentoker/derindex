"""Real-time filesystem watcher using watchdog with event debouncing and incremental updates."""

import time
import logging
import threading
from pathlib import Path
from typing import Optional, Dict, List, Tuple, Union

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler, FileSystemEvent

from config import config
from .crawler import Crawler

logger = logging.getLogger(__name__)


class DebouncedIndexHandler(FileSystemEventHandler):
    def __init__(self, crawler: Crawler, debounce_seconds: float = 1.0):
        super().__init__()
        self.crawler = crawler
        self.debounce_seconds = debounce_seconds
        self._pending_events: Dict[str, Tuple[str, float]] = {}  # path -> (event_type, timestamp)
        self._lock = threading.Lock()
        self._running = True
        self._worker_thread = threading.Thread(target=self._process_events_loop, daemon=True)
        self._worker_thread.start()

    def on_created(self, event: FileSystemEvent):
        if not event.is_directory:
            self._schedule(event.src_path, "created")

    def on_modified(self, event: FileSystemEvent):
        if not event.is_directory:
            self._schedule(event.src_path, "modified")

    def on_deleted(self, event: FileSystemEvent):
        if not event.is_directory:
            self._schedule(event.src_path, "deleted")

    def on_moved(self, event: FileSystemEvent):
        if not event.is_directory:
            self._schedule(event.src_path, "deleted")
            if hasattr(event, "dest_path"):
                self._schedule(event.dest_path, "created")

    def _schedule(self, path: str, event_type: str):
        p = Path(path)
        if not self.crawler.is_safe_and_supported(p) and event_type != "deleted":
            return
        with self._lock:
            self._pending_events[path] = (event_type, time.time())

    def _process_events_loop(self):
        while self._running:
            time.sleep(0.5)
            now = time.time()
            to_process = []

            with self._lock:
                for path, (ev_type, ts) in list(self._pending_events.items()):
                    if now - ts >= self.debounce_seconds:
                        to_process.append((path, ev_type))
                        del self._pending_events[path]

            for path, ev_type in to_process:
                self._handle_single_event(path, ev_type)

    def _handle_single_event(self, path: str, ev_type: str):
        p = Path(path)
        try:
            if ev_type == "deleted" or not p.exists():
                logger.info(f"[WATCHER] File deleted: {p.name}")
                resolved_str = str(p.absolute())
                try:
                    resolved_str = str(p.resolve())
                except Exception:
                    pass
                self.crawler.remove_file(resolved_str, save_vector_store=True)
            else:

                logger.info(f"[WATCHER] File changed/created: {p.name}")
                success = self.crawler.index_file(p, save_vector_store=True, update_term_stats=True)
                if success:
                    logger.info(f"[WATCHER] Successfully updated index for {p.name}")
        except Exception as e:
            logger.error(f"[WATCHER] Error processing file {path}: {e}")

    def stop(self):
        self._running = False
        if self._worker_thread.is_alive():
            self._worker_thread.join(timeout=2.0)


class FileWatcher:
    def __init__(self, crawler: Optional[Crawler] = None):
        self.crawler = crawler or Crawler()
        self.observer = Observer()
        self.handler = DebouncedIndexHandler(self.crawler)

    def watch(self, target_dirs: Union[Path, str, List[Union[Path, str]]]):
        """Starts watching one or more target directories recursively."""
        from typing import Union as _Union
        if isinstance(target_dirs, (str, Path)):
            if isinstance(target_dirs, str) and "," in target_dirs:
                dirs = [Path(p.strip()) for p in target_dirs.split(",") if p.strip()]
            else:
                dirs = [Path(target_dirs)]
        else:
            dirs = [Path(d) for d in target_dirs]

        valid_count = 0
        for d in dirs:
            p = d.expanduser().resolve()
            if p.exists() and p.is_dir():
                logger.info(f"Starting file watcher on: {p}")
                self.observer.schedule(self.handler, str(p), recursive=True)
                valid_count += 1
            else:
                logger.warning(f"Directory {d} does not exist or is not a directory.")

        if valid_count == 0:
            raise ValueError("No valid directories provided to watch.")

        self.observer.start()

        try:
            while self.observer.is_alive():
                time.sleep(1)
        except KeyboardInterrupt:
            logger.info("Stopping watcher...")
        finally:
            self.stop()

    def stop(self):
        self.handler.stop()
        self.observer.stop()
        self.observer.join()
