"""Download queue model with SQLite persistence (Qt-free)."""
from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterator, List, Optional, Set

from core.quality_manager import QualitySpec
from utils.logger import get_logger

log = get_logger("queue")


class TaskStatus:
    PENDING = "Pending"
    EXTRACTING = "Extracting Information"
    DOWNLOADING = "Downloading"
    COMPLETED = "Completed"
    FAILED = "Failed"
    CANCELLED = "Cancelled"
    PAUSED = "Paused"
    INTERRUPTED = "Interrupted"      # connection lost; partial data kept, resumes automatically or manually

    ACTIVE = {EXTRACTING, DOWNLOADING}
    OPEN = {PENDING, EXTRACTING, DOWNLOADING, PAUSED, INTERRUPTED}     # block duplicates while in these states
    FINISHED = {COMPLETED, CANCELLED}
    ALL = (PENDING, EXTRACTING, DOWNLOADING, COMPLETED, FAILED, CANCELLED, PAUSED, INTERRUPTED)


class QueueError(Exception):
    pass


class DuplicateTaskError(QueueError):
    pass


@dataclass
class QueueTask:
    url: str
    quality: QualitySpec
    dest_dir: str
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    status: str = TaskStatus.PENDING
    created_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    error: str = ""
    priority: int = 0
    title: str = ""
    progress: float = 0.0
    engine: str = ""
    playlist_done: int = 0
    playlist_total: int = 0
    seq: int = 0
    thumbnail: str = ""             # URL (display only, not persisted)
    playlist_items: str = ""        # "1,3,5": only these playlist items; empty means the whole playlist
    waiting_network: bool = False   # paused because the connection dropped; auto-resumes when it is back

    @property
    def format_label(self) -> str:
        return "MP3" if self.quality.is_audio and self.quality.audio_format == "mp3" else self.quality.container.upper()

    def duplicate_key(self) -> str:
        return f"{self.url}|{self.quality.cache_key()}|{self.dest_dir}|{self.playlist_items}"


SCHEMA = """
CREATE TABLE IF NOT EXISTS queue_tasks (
    id TEXT PRIMARY KEY, seq INTEGER NOT NULL, url TEXT NOT NULL, spec_json TEXT NOT NULL,
    dest_dir TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL, error TEXT NOT NULL DEFAULT '',
    priority INTEGER NOT NULL DEFAULT 0, title TEXT NOT NULL DEFAULT '', playlist_items TEXT NOT NULL DEFAULT ''
);
"""


class DownloadQueue:
    """Ordered task list. All methods are thread-safe. When `db_path` is None nothing is persisted."""

    def __init__(self, db_path: Optional[Path] = None) -> None:
        self._lock = threading.RLock()
        self._tasks: List[QueueTask] = []
        self._next_seq = 1
        self.db_path = Path(db_path) if db_path else None
        if self.db_path:
            self._init_db()
            self._recover()

    # ------------------------------------------------------------------ persistence
    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        assert self.db_path is not None
        conn = sqlite3.connect(str(self.db_path), timeout=15)
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def _init_db(self) -> None:
        try:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)  # type: ignore[union-attr]
            with self._conn() as conn:
                conn.executescript(SCHEMA)
                columns = {row[1] for row in conn.execute("PRAGMA table_info(queue_tasks)")}
                if "playlist_items" not in columns:      # database created by an older version
                    conn.execute("ALTER TABLE queue_tasks ADD COLUMN playlist_items TEXT NOT NULL DEFAULT ''")
        except sqlite3.Error as exc:
            log.error("Queue database unavailable, running without persistence: %s", exc)
            self.db_path = None

    def _recover(self) -> None:
        """Reload unfinished tasks. Tasks interrupted mid-download return to Pending."""
        try:
            with self._conn() as conn:
                rows = conn.execute(
                    "SELECT id, seq, url, spec_json, dest_dir, status, created_at, error, priority, title, playlist_items "
                    "FROM queue_tasks ORDER BY seq").fetchall()
        except sqlite3.Error as exc:
            log.error("Could not recover the queue: %s", exc)
            return
        for row in rows:
            try:
                spec = QualitySpec.from_dict(json.loads(row[3]))
            except (ValueError, TypeError):
                continue
            status = row[5] if row[5] in TaskStatus.ALL else TaskStatus.PENDING
            if status in TaskStatus.ACTIVE:
                status = TaskStatus.PENDING
            self._tasks.append(QueueTask(
                id=row[0], seq=row[1], url=row[2], quality=spec, dest_dir=row[4], status=status,
                created_at=row[6], error=row[7], priority=row[8], title=row[9], playlist_items=row[10] or "",
            ))
            self._next_seq = max(self._next_seq, row[1] + 1)

    def _persist(self, task: QueueTask) -> None:
        if not self.db_path:
            return
        try:
            with self._conn() as conn:
                if task.status in TaskStatus.FINISHED:
                    conn.execute("DELETE FROM queue_tasks WHERE id = ?", (task.id,))
                else:
                    conn.execute(
                        "INSERT OR REPLACE INTO queue_tasks (id, seq, url, spec_json, dest_dir, status, "
                        "created_at, error, priority, title, playlist_items) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                        (task.id, task.seq, task.url, json.dumps(task.quality.to_dict()), task.dest_dir,
                         task.status, task.created_at, task.error, task.priority, task.title, task.playlist_items))
        except sqlite3.Error as exc:
            log.error("Could not persist queue task: %s", exc)

    def _forget(self, task_id: str) -> None:
        if not self.db_path:
            return
        try:
            with self._conn() as conn:
                conn.execute("DELETE FROM queue_tasks WHERE id = ?", (task_id,))
        except sqlite3.Error as exc:
            log.error("Could not delete queue task: %s", exc)

    # ------------------------------------------------------------------ queries
    def tasks(self) -> List[QueueTask]:
        with self._lock:
            return list(self._tasks)

    def get(self, task_id: str) -> Optional[QueueTask]:
        with self._lock:
            return next((t for t in self._tasks if t.id == task_id), None)

    def count(self, status: str) -> int:
        with self._lock:
            return sum(1 for t in self._tasks if t.status == status)

    def next_pending(self, exclude: Optional[Set[str]] = None) -> Optional[QueueTask]:
        """Highest priority first, then oldest first."""
        with self._lock:
            pending = [t for t in self._tasks if t.status == TaskStatus.PENDING and t.id not in (exclude or set())]
            if not pending:
                return None
            return sorted(pending, key=lambda t: (-t.priority, t.seq))[0]

    # ------------------------------------------------------------------ mutations
    def add(self, url: str, quality: QualitySpec, dest_dir: str, priority: int = 0,
            playlist_items: str = "") -> QueueTask:
        with self._lock:
            task = QueueTask(url=url, quality=quality, dest_dir=str(dest_dir), priority=priority,
                             playlist_items=playlist_items)
            key = task.duplicate_key()
            if any(t.status in TaskStatus.OPEN and t.duplicate_key() == key for t in self._tasks):
                raise DuplicateTaskError("This URL is already in the queue with the same settings.")
            task.seq = self._next_seq
            self._next_seq += 1
            self._tasks.append(task)
            self._persist(task)
            return task

    def set_status(self, task_id: str, status: str, error: str = "") -> Optional[QueueTask]:
        with self._lock:
            task = self.get(task_id)
            if task is None:
                return None
            task.status = status
            task.error = error
            if status == TaskStatus.COMPLETED:
                task.progress = 100.0
            self._persist(task)
            return task

    def update_info(self, task_id: str, **fields: object) -> None:
        """Update non-persistent display fields (title, progress, engine, playlist counters)."""
        with self._lock:
            task = self.get(task_id)
            if task is None:
                return
            persist = "title" in fields
            for key, value in fields.items():
                if hasattr(task, key):
                    setattr(task, key, value)
            if persist:
                self._persist(task)

    def set_priority(self, task_id: str, priority: int) -> bool:
        with self._lock:
            task = self.get(task_id)
            if task is None:
                return False
            task.priority = priority
            self._persist(task)
            return True

    def remove(self, task_id: str) -> bool:
        """Remove a task that is not currently running."""
        with self._lock:
            task = self.get(task_id)
            if task is None or task.status in TaskStatus.ACTIVE:
                return False
            self._tasks.remove(task)
            self._forget(task_id)
            return True

    def retry(self, task_id: str) -> bool:
        with self._lock:
            task = self.get(task_id)
            if task is None or task.status not in (TaskStatus.FAILED, TaskStatus.CANCELLED):
                return False
            task.status, task.error, task.progress = TaskStatus.PENDING, "", 0.0
            self._persist(task)
            return True

    def pause_task(self, task_id: str) -> bool:
        with self._lock:
            task = self.get(task_id)
            if task is None or task.status != TaskStatus.PENDING:
                return False
            task.status = TaskStatus.PAUSED
            self._persist(task)
            return True

    def resume_task(self, task_id: str) -> bool:
        with self._lock:
            task = self.get(task_id)
            if task is None or task.status not in (TaskStatus.PAUSED, TaskStatus.INTERRUPTED):
                return False
            task.status, task.error = TaskStatus.PENDING, ""
            task.waiting_network = False
            self._persist(task)
            return True

    def clear_completed(self) -> int:
        with self._lock:
            done = [t for t in self._tasks if t.status in (TaskStatus.COMPLETED, TaskStatus.CANCELLED)]
            for task in done:
                self._tasks.remove(task)
                self._forget(task.id)
            return len(done)
