"""Media library: scans the download folder (Qt-free, so it is unit tested) and offers safe file operations.

Only files inside the library root are ever renamed or deleted, and only after the caller confirmed in the UI."""
from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from utils.file_manager import TEMP_ROOT_NAME, sanitize_filename, unique_path

VIDEO_EXT = {".mp4", ".mkv", ".webm", ".mov", ".avi", ".m4v", ".flv", ".ts"}
AUDIO_EXT = {".mp3", ".m4a", ".opus", ".ogg", ".flac", ".wav", ".aac"}
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp"}
SORT_KEYS = ("date", "name", "size", "type")
KINDS = ("all", "video", "audio")


@dataclass
class LibraryItem:
    path: Path
    name: str
    folder: str
    size: int
    modified: float
    kind: str                     # video | audio
    thumbnail: str = ""           # image file beside the media (saved thumbnail), if any

    @property
    def modified_text(self) -> str:
        return datetime.fromtimestamp(self.modified).strftime("%Y-%m-%d %H:%M")


def _kind(path: Path) -> str:
    ext = path.suffix.lower()
    return "video" if ext in VIDEO_EXT else "audio" if ext in AUDIO_EXT else ""


def scan(root: Path, limit: int = 20000) -> List[LibraryItem]:
    """Media files under root (recursive). Temporary folders and partial files are skipped."""
    items: List[LibraryItem] = []
    root = Path(root)
    if not root.is_dir():
        return items
    for current, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d != TEMP_ROOT_NAME and not d.startswith(".")]
        names = set(files)
        for name in files:
            path = Path(current) / name
            kind = _kind(path)
            if not kind or name.endswith((".part", ".ytdl")):
                continue
            try:
                st = path.stat()
            except OSError:
                continue
            thumb = ""
            for ext in (".jpg", ".jpeg", ".png", ".webp"):
                if path.with_suffix(ext).name in names:
                    thumb = str(path.with_suffix(ext))
                    break
            items.append(LibraryItem(path, path.stem, str(Path(current)), st.st_size, st.st_mtime, kind, thumb))
            if len(items) >= limit:
                return items
    return items


def filter_sort(items: List[LibraryItem], text: str = "", kind: str = "all", sort: str = "date",
                descending: bool = True) -> List[LibraryItem]:
    needle = text.strip().lower()
    out = [i for i in items if (kind == "all" or i.kind == kind) and (not needle or needle in i.name.lower())]
    keyfn = {"date": lambda i: i.modified, "name": lambda i: i.name.lower(), "size": lambda i: i.size,
             "type": lambda i: (i.kind, i.name.lower())}.get(sort, lambda i: i.modified)
    out.sort(key=keyfn, reverse=descending)
    return out


def storage_stats(items: List[LibraryItem], root: Optional[Path] = None) -> Dict[str, int]:
    stats = {"files": len(items), "bytes": sum(i.size for i in items),
             "video_bytes": sum(i.size for i in items if i.kind == "video"),
             "audio_bytes": sum(i.size for i in items if i.kind == "audio"),
             "disk_total": 0, "disk_free": 0}
    if root is not None and Path(root).exists():
        usage = shutil.disk_usage(root)
        stats["disk_total"], stats["disk_free"] = usage.total, usage.free
    return stats


def _inside(root: Path, path: Path) -> bool:
    try:
        path.resolve().relative_to(Path(root).resolve())
        return True
    except ValueError:
        return False


def rename(root: Path, path: Path, new_stem: str) -> Path:
    """Rename a media file (and its saved thumbnail) inside the library. Never overwrites: a number is appended."""
    path = Path(path)
    if not path.is_file() or not _inside(root, path):
        raise ValueError("That file is not inside the library folder.")
    stem = sanitize_filename(new_stem) if new_stem.strip() else ""
    if not stem:
        raise ValueError("The new name is empty.")
    target = path.with_name(stem + path.suffix)
    if target == path:
        return path
    target = unique_path(target)
    path.rename(target)
    for ext in (".jpg", ".jpeg", ".png", ".webp"):
        thumb = path.with_suffix(ext)
        if thumb.is_file():
            thumb.rename(unique_path(target.with_suffix(ext)))
    return target


def delete(root: Path, path: Path) -> None:
    """Delete one media file. Refuses anything outside the library or not media. Image files beside it are kept
    (they might not be ours)."""
    path = Path(path)
    if not _inside(root, path) or not _kind(path) or not path.is_file():
        raise ValueError("Refusing to delete a file that is not a media file inside the library.")
    path.unlink()


def make_preview(ffmpeg: str, media: Path, cache_dir: Path, width: int = 320, timeout: float = 25.0) -> Optional[Path]:
    """JPEG still frame of a video for the library grid, cached by path + modification time. Returns None for audio
    files or when FFmpeg is unavailable/fails. Runs ffmpeg without a shell."""
    import hashlib
    import subprocess

    media = Path(media)
    if not ffmpeg or _kind(media) != "video" or not media.is_file():
        return None
    key = hashlib.sha1(f"{media.resolve()}|{media.stat().st_mtime_ns}|{width}".encode("utf-8")).hexdigest()
    out = Path(cache_dir) / f"{key}.jpg"
    if out.is_file() and out.stat().st_size > 0:
        return out
    Path(cache_dir).mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp.jpg")
    for seek in ("3", "0"):                       # a frame 3 s in is usually nicer than the very first one
        cmd = [ffmpeg, "-y", "-loglevel", "error", "-ss", seek, "-i", str(media), "-frames:v", "1",
               "-vf", f"scale={width}:-2", str(tmp)]
        try:
            subprocess.run(cmd, capture_output=True, timeout=timeout, check=True)
        except (OSError, subprocess.SubprocessError):
            continue
        if tmp.is_file() and tmp.stat().st_size > 0:
            tmp.replace(out)
            return out
    tmp.unlink(missing_ok=True)
    return None
