"""Real-time filesystem watcher using watchdog with event debouncing and incremental updates."""

import time
import logging
import threading
from pathlib import Path
from typing import Optional, Dict, List, Tuple, Union, Set, Any

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
        check_exists = (event_type != "deleted")
        if not self.crawler.is_safe_and_supported(p, check_exists=check_exists):
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


# Global active watcher instance
active_watcher: Optional['FileWatcher'] = None


class FileWatcher:
    _instance: Optional['FileWatcher'] = None
    _inst_lock = threading.Lock()

    @classmethod
    def get_instance(cls, crawler: Optional[Crawler] = None) -> 'FileWatcher':
        """Thread-safe singleton accessor for FileWatcher."""
        with cls._inst_lock:
            if cls._instance is None:
                cls._instance = cls(crawler=crawler)
            return cls._instance

    def __init__(self, crawler: Optional[Crawler] = None):
        global active_watcher
        self.crawler = crawler or Crawler()
        self.observer = Observer()
        self.handler = DebouncedIndexHandler(self.crawler)
        self.watches: Dict[str, Any] = {}
        self.watched_paths: Set[str] = set()
        self._lock = threading.Lock()
        active_watcher = self
        FileWatcher._instance = self

    def add_watch_directory(self, target_dir: Union[Path, str]) -> bool:
        """Dynamically adds a new directory to the active watchdog observer with parent-child deduplication."""
        p = Path(target_dir).expanduser().resolve()
        if not p.exists() or not p.is_dir():
            logger.warning(f"Cannot watch non-existent directory: {p}")
            return False

        p_str = str(p)
        with self._lock:
            # 1. Check if this directory is already covered by an existing watched parent root
            for watched in self.watched_paths:
                if p_str == watched or p_str.startswith(watched.rstrip("/") + "/"):
                    logger.info(f"Directory {p} is already covered by watched root {watched}")
                    return True

            # 2. Check if this new directory is a PARENT of any already-watched subdirectories.
            # If so, unschedule redundant child watches because recursive watch on parent covers them.
            children_to_remove = [
                w for w in list(self.watched_paths)
                if w.startswith(p_str.rstrip("/") + "/")
            ]
            for child in children_to_remove:
                if child in self.watches:
                    try:
                        self.observer.unschedule(self.watches[child])
                        del self.watches[child]
                    except Exception as e:
                        logger.debug(f"Could not unschedule child watch {child}: {e}")
                self.watched_paths.discard(child)
                logger.info(f"Subsumed child watch '{child}' under parent '{p_str}'")

            # 3. Schedule the new watch
            try:
                watch = self.observer.schedule(self.handler, p_str, recursive=True)
                self.watches[p_str] = watch
                self.watched_paths.add(p_str)
                if not self.observer.is_alive():
                    self.observer.start()
                logger.info(f"Successfully added dynamic watch on: {p_str}")
                return True
            except Exception as e:
                logger.error(f"Failed to schedule watch on {p_str}: {e}")
                return False

    def remove_watch_directory(self, target_dir: Union[Path, str]) -> bool:
        """Dynamically removes a directory from the active watchdog observer."""
        p = Path(target_dir).expanduser().resolve()
        p_str = str(p)
        with self._lock:
            if p_str in self.watches:
                try:
                    self.observer.unschedule(self.watches[p_str])
                    del self.watches[p_str]
                except Exception as e:
                    logger.error(f"Error unscheduling watch for {p_str}: {e}")
                self.watched_paths.discard(p_str)
                logger.info(f"Removed watch on: {p_str}")
                return True
            return False

    def get_watched_paths(self) -> List[str]:
        """Returns sorted list of currently watched directories."""
        with self._lock:
            return sorted(list(self.watched_paths))

    def watch(self, target_dirs: Optional[Union[Path, str, List[Union[Path, str]]]] = None):
        """Starts watching one or more target directories recursively and blocks until interrupted."""
        if target_dirs:
            if isinstance(target_dirs, (str, Path)):
                if isinstance(target_dirs, str) and "," in target_dirs:
                    dirs = [Path(p.strip()) for p in target_dirs.split(",") if p.strip()]
                else:
                    dirs = [Path(target_dirs)]
            else:
                dirs = [Path(d) for d in target_dirs]

            for d in dirs:
                self.add_watch_directory(d)

        if not self.observer.is_alive():
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
        if self.observer.is_alive():
            self.observer.stop()
            self.observer.join()

