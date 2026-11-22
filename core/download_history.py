"""Persistent download history stored in SQLite.

Every public method opens its own short-lived connection inside a transaction, so the class is
safe to use from worker threads and the GUI thread. Database errors are logged and reported
through return values (None / False / []) instead of crashing the application.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterator, List, Optional

from utils.logger import get_logger

log = get_logger("history")

STATUS_COMPLETED = "Completed"
STATUS_FAILED = "Failed"

SCHEMA = """
CREATE TABLE IF NOT EXISTS history (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    title         TEXT NOT NULL,
    url           TEXT NOT NULL,
    platform      TEXT NOT NULL DEFAULT '',
    file_path     TEXT NOT NULL DEFAULT '',
    file_size     INTEGER NOT NULL DEFAULT 0,
    resolution    TEXT NOT NULL DEFAULT '',
    format        TEXT NOT NULL DEFAULT '',
    engine        TEXT NOT NULL DEFAULT '',
    downloaded_at TEXT NOT NULL,
    status        TEXT NOT NULL,
    error         TEXT NOT NULL DEFAULT '',
    spec_json     TEXT NOT NULL DEFAULT '',
    dest_dir      TEXT NOT NULL DEFAULT '',
    thumbnail     TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_history_date ON history(downloaded_at);
CREATE INDEX IF NOT EXISTS idx_history_url ON history(url);
"""


@dataclass
class HistoryRecord:
    title: str
    url: str
    platform: str = ""
    file_path: str = ""
    file_size: int = 0
    resolution: str = ""
    format: str = ""
    engine: str = ""
    status: str = STATUS_COMPLETED
    error: str = ""
    spec_json: str = ""
    dest_dir: str = ""
    thumbnail: str = ""
    downloaded_at: str = ""
    id: Optional[int] = None


def _escape_like(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class DownloadHistory:
    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.ok = self._init_schema()

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(str(self.db_path), timeout=15)
        conn.row_factory = sqlite3.Row
        try:
            with conn:   # commit on success, rollback on exception
                yield conn
        finally:
            conn.close()

    def _init_schema(self) -> bool:
        try:
            with self._transaction() as conn:
                conn.execute("PRAGMA journal_mode=WAL")      # readers never block the writer thread
                conn.executescript(SCHEMA)
                columns = {row[1] for row in conn.execute("PRAGMA table_info(history)")}
                if "thumbnail" not in columns:      # database created by an older version
                    conn.execute("ALTER TABLE history ADD COLUMN thumbnail TEXT NOT NULL DEFAULT ''")
            return True
        except sqlite3.Error as exc:
            log.error("Could not initialise the history database: %s", exc)
            return False

    # ------------------------------------------------------------------ writes
    def add(self, record: HistoryRecord) -> Optional[int]:
        stamp = record.downloaded_at or datetime.now().isoformat(timespec="seconds")
        try:
            with self._transaction() as conn:
                cursor = conn.execute(
                    "INSERT INTO history (title, url, platform, file_path, file_size, resolution, format, "
                    "engine, downloaded_at, status, error, spec_json, dest_dir, thumbnail) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (record.title, record.url, record.platform, record.file_path, int(record.file_size),
                     record.resolution, record.format, record.engine, stamp, record.status, record.error,
                     record.spec_json, record.dest_dir, record.thumbnail),
                )
                return int(cursor.lastrowid)
        except sqlite3.Error as exc:
            log.error("Could not save history record: %s", exc)
            return None

    def remove(self, record_id: int) -> bool:
        """Delete one record. The downloaded file is never touched."""
        try:
            with self._transaction() as conn:
                conn.execute("DELETE FROM history WHERE id = ?", (record_id,))
            return True
        except sqlite3.Error as exc:
            log.error("Could not remove history record: %s", exc)
            return False

    def clear(self) -> bool:
        try:
            with self._transaction() as conn:
                conn.execute("DELETE FROM history")
            return True
        except sqlite3.Error as exc:
            log.error("Could not clear history: %s", exc)
            return False

    # ------------------------------------------------------------------ reads
    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> HistoryRecord:
        return HistoryRecord(
            id=row["id"], title=row["title"], url=row["url"], platform=row["platform"],
            file_path=row["file_path"], file_size=row["file_size"], resolution=row["resolution"],
            format=row["format"], engine=row["engine"], downloaded_at=row["downloaded_at"],
            status=row["status"], error=row["error"], spec_json=row["spec_json"], dest_dir=row["dest_dir"],
            thumbnail=row["thumbnail"],
        )

    def search(self, text: str = "", platform: str = "", status: str = "",
               newest_first: bool = True, limit: Optional[int] = None) -> List[HistoryRecord]:
        clauses, params = [], []
        if text.strip():
            clauses.append("title LIKE ? ESCAPE '\\'")
            params.append(f"%{_escape_like(text.strip())}%")
        if platform:
            clauses.append("platform = ?")
            params.append(platform)
        if status:
            clauses.append("status = ?")
            params.append(status)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        order = "DESC" if newest_first else "ASC"
        sql = f"SELECT * FROM history {where} ORDER BY downloaded_at {order}, id {order}"
        if limit:
            sql += f" LIMIT {int(limit)}"
        try:
            with self._transaction() as conn:
                return [self._row_to_record(r) for r in conn.execute(sql, params)]
        except sqlite3.Error as exc:
            log.error("History query failed: %s", exc)
            return []

    def get(self, record_id: int) -> Optional[HistoryRecord]:
        try:
            with self._transaction() as conn:
                row = conn.execute("SELECT * FROM history WHERE id = ?", (record_id,)).fetchone()
                return self._row_to_record(row) if row else None
        except sqlite3.Error as exc:
            log.error("History lookup failed: %s", exc)
            return None

    def platforms(self) -> List[str]:
        try:
            with self._transaction() as conn:
                return [r[0] for r in conn.execute(
                    "SELECT DISTINCT platform FROM history WHERE platform != '' ORDER BY platform")]
        except sqlite3.Error:
            return []

    def find_completed(self, url: str) -> Optional[HistoryRecord]:
        """Most recent completed download of `url` whose file still exists."""
        try:
            with self._transaction() as conn:
                rows = conn.execute("SELECT * FROM history WHERE url = ? AND status = ? AND file_path != '' "
                                    "ORDER BY id DESC", (url, STATUS_COMPLETED)).fetchall()
        except sqlite3.Error:
            return None
        for row in rows:
            record = self._row_to_record(row)
            if Path(record.file_path).is_file():
                return record
        return None

    def stats(self) -> dict:
        """Totals for the dashboard: completed count, total bytes, failed count."""
        try:
            with self._transaction() as conn:
                done = conn.execute("SELECT COUNT(*), COALESCE(SUM(file_size), 0) FROM history WHERE status = ?",
                                    (STATUS_COMPLETED,)).fetchone()
                failed = conn.execute("SELECT COUNT(*) FROM history WHERE status != ?",
                                      (STATUS_COMPLETED,)).fetchone()
            return {"completed": int(done[0]), "bytes": int(done[1]), "failed": int(failed[0])}
        except sqlite3.Error:
            return {"completed": 0, "bytes": 0, "failed": 0}
