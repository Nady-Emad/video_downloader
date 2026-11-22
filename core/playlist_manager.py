"""Playlist helpers: folder naming, per-entry filenames and completion tracking."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List

from engines.base_engine import MediaInfo, PlaylistEntry
from utils import file_manager


@dataclass
class PlaylistProgress:
    total: int = 0
    done: int = 0       # downloaded or already present
    failed: int = 0
    skipped: int = 0    # unavailable entries

    @property
    def processed(self) -> int:
        return self.done + self.failed + self.skipped

    @property
    def percent(self) -> float:
        return (self.processed / self.total * 100.0) if self.total else 0.0

    def summary(self) -> str:
        return f"{self.done}/{self.total} downloaded, {self.failed} failed, {self.skipped} unavailable"


def playlist_folder(info: MediaInfo, base_dir: Path) -> Path:
    """`<base>/<sanitised playlist title>` (created on demand by the caller)."""
    title = info.title or info.id or "Playlist"
    return Path(base_dir) / file_manager.sanitize_filename(title, fallback="Playlist")


def entry_stem(template: str, entry: PlaylistEntry, total: int, playlist_title: str) -> str:
    """Filename (no extension) for a playlist entry, prefixed with its zero-padded index."""
    return file_manager.render_filename(
        template, title=entry.title, index=entry.index, total=total,
        media_id=entry.id, playlist=playlist_title,
    )


def already_downloaded(folder: Path, stem: str) -> bool:
    return file_manager.find_existing(folder, stem) is not None


def downloadable_entries(info: MediaInfo) -> List[PlaylistEntry]:
    return [e for e in info.entries if e.available]
