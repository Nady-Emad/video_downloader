"""Filename sanitising, unique naming, temp directories, safe cleanup and atomic finalisation."""
from __future__ import annotations

import errno
import os
import re
import shutil
import string
import time
import unicodedata
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from utils.logger import get_logger

log = get_logger("files")

TEMP_ROOT_NAME = ".vdl_tmp"
LOCK_NAME = ".vdl_lock"
RESUME_MARKER = ".vdl_resumable"
RESUME_KEEP_DAYS = 7          # partial downloads kept for resuming are removed after this long
_ATTEMPT_NAME = re.compile(r"[0-9a-f]{32}(-\d+)?")
_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f\x7f]')
_PARTIAL = (".part", ".ytdl", ".temp", ".tmp", ".partial", ".download")
MEDIA_SUFFIXES = {".mp4", ".mkv", ".webm", ".mp3", ".m4a", ".opus", ".ogg", ".aac", ".wav", ".flac",
                  ".mov", ".avi", ".flv", ".m4v", ".ts", ".3gp", ".weba", ".jpg", ".jpeg", ".png", ".gif", ".webp"}


class FileError(Exception):
    pass


# ----------------------------------------------------------------------------- names
def sanitize_filename(name: str, max_length: int = 150, fallback: str = "untitled") -> str:
    """Make `name` a safe single path component on Windows, Linux and macOS."""
    text = unicodedata.normalize("NFC", str(name or ""))
    text = _ILLEGAL.sub("_", text)
    text = re.sub(r"\s+", " ", text).strip()
    text = text.replace("..", ".")          # no traversal sequences
    text = text.strip(" .")                  # Windows forbids trailing dots/spaces
    if len(text) > max_length:
        text = text[:max_length].rstrip(" .")
    # Keep UTF-8 length below common 255-byte filename limits (leave room for extension).
    while len(text.encode("utf-8")) > 200 and text:
        text = text[:-1]
    text = text.rstrip(" .")
    if not text:
        text = fallback
    if text.split(".")[0].upper() in _RESERVED:
        text = "_" + text
    return text


class _SafeDict(dict):
    def __missing__(self, key: str) -> str:
        return ""


def render_filename(template: str, title: str, index: Optional[int] = None, total: Optional[int] = None,
                    media_id: str = "", uploader: str = "", playlist: str = "", date: str = "") -> str:
    """Render a filename template (without extension). Index is zero padded to the playlist size."""
    width = max(2, len(str(total))) if total else 2
    values: Dict[str, Any] = _SafeDict(
        title=sanitize_filename(title, fallback="video"),
        index=f"{index:0{width}d}" if index is not None else "",
        id=sanitize_filename(media_id, fallback=""),
        uploader=sanitize_filename(uploader, fallback=""),
        playlist=sanitize_filename(playlist, fallback=""),
        date=date,
    )
    try:
        rendered = string.Formatter().vformat(template, (), values)
    except (ValueError, KeyError, IndexError, AttributeError):
        rendered = f"{values['index']} - {values['title']}" if index is not None else values["title"]
    return sanitize_filename(rendered, fallback=values["title"] or "video")


def unique_path(path: Path) -> Path:
    """`name.ext` -> `name (1).ext`, `name (2).ext`... until the path is free."""
    path = Path(path)
    if not path.exists():
        return path
    for counter in range(1, 10_000):
        candidate = path.with_name(f"{path.stem} ({counter}){path.suffix}")
        if not candidate.exists():
            return candidate
    raise FileError(f"Could not find a free filename for {path.name}")


def find_existing(directory: Path, stem: str) -> Optional[Path]:
    """A completed media file `stem.<ext>` in directory (ignores partial files)."""
    directory = Path(directory)
    if not directory.is_dir():
        return None
    for suffix in sorted(MEDIA_SUFFIXES):
        candidate = directory / f"{stem}{suffix}"
        if candidate.is_file() and candidate.stat().st_size > 0:
            return candidate
    return None


# ----------------------------------------------------------------------------- folders / space
def ensure_dir(path: Path) -> Path:
    path = Path(path).expanduser()
    try:
        path.mkdir(parents=True, exist_ok=True)
    except PermissionError as exc:
        raise FileError(f"Permission denied creating folder: {path}") from exc
    except OSError as exc:
        raise FileError(f"Cannot create folder {path}: {exc}") from exc
    if not os.access(path, os.W_OK):
        raise FileError(f"The folder is not writable: {path}")
    return path


def free_space(path: Path) -> Optional[int]:
    probe = Path(path)
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    try:
        return shutil.disk_usage(probe).free
    except OSError:
        return None


def check_disk_space(path: Path, required: int = 0, minimum: int = 50 * 1024 * 1024) -> None:
    """Raise FileError when free space is below max(required * 1.1, minimum)."""
    free = free_space(path)
    if free is None:
        return
    needed = max(int(required * 1.1), minimum)
    if free < needed:
        raise FileError(
            f"Not enough disk space: {free // (1024 * 1024)} MB free, about {needed // (1024 * 1024)} MB needed."
        )


# ----------------------------------------------------------------------------- temp dirs
def make_attempt_dir(dest_dir: Path) -> Path:
    """Create a unique private temp directory inside dest_dir (same filesystem => atomic rename)."""
    root = ensure_dir(Path(dest_dir) / TEMP_ROOT_NAME)
    attempt = root / uuid.uuid4().hex
    attempt.mkdir()
    (attempt / LOCK_NAME).write_text(str(os.getpid()), encoding="ascii")
    return attempt


STATE_NAME = ".vdl_state.json"


def resume_dir(dest_dir: Path, key: str, signature: str = "") -> Path:
    """Stable work folder for a resumable download (reused across attempts, pauses and restarts).

    `signature` identifies what is being downloaded (URL + quality). Partial data written for a different
    signature is discarded, and empty (0-byte) leftovers are removed, so only usable partials are resumed."""
    root = ensure_dir(Path(dest_dir) / TEMP_ROOT_NAME)
    path = root / key
    path.mkdir(exist_ok=True)
    state = path / STATE_NAME
    if signature:
        try:
            old = state.read_text(encoding="utf-8").strip() if state.exists() else None
        except OSError:
            old = None
        if old is not None and old != signature:
            for child in path.iterdir():                       # stale partials of a different request
                if child.is_file() and child.name.startswith("media"):
                    child.unlink(missing_ok=True)
        state.write_text(signature, encoding="utf-8")
    for child in path.iterdir():
        try:
            if child.is_file() and child.name.startswith("media") and child.stat().st_size == 0:
                child.unlink()
        except OSError:
            pass
    (path / LOCK_NAME).write_text(str(os.getpid()), encoding="ascii")
    (path / RESUME_MARKER).write_text("1", encoding="ascii")
    return path


def cleanup_task_partials(dest_dir: Path, task_id: str) -> int:
    """Delete resumable partial folders belonging to one task (in dest_dir and its sub-folders,
    which covers playlist folders). Used when a task is cancelled or removed."""
    removed = 0
    base = Path(dest_dir)
    if not base.is_dir():
        return 0
    try:
        candidates = [base] + [p for p in base.iterdir() if p.is_dir() and p.name != TEMP_ROOT_NAME]
    except OSError:
        return 0
    for folder in candidates:
        root = folder / TEMP_ROOT_NAME
        if not root.is_dir():
            continue
        for child in root.iterdir():
            if child.name.startswith(task_id) and _is_attempt_dir(child):
                shutil.rmtree(child, ignore_errors=True)
                removed += 1
        try:
            root.rmdir()
        except OSError:
            pass
    return removed


def _is_attempt_dir(path: Path) -> bool:
    return (
        path.parent.name == TEMP_ROOT_NAME
        and _ATTEMPT_NAME.fullmatch(path.name) is not None
        and (path / LOCK_NAME).exists()
    )


def cleanup_attempt_dir(path: Path) -> bool:
    """Delete one attempt directory created by make_attempt_dir. Refuses any other path."""
    path = Path(path)
    if not path.exists():
        return True
    # Name and location identify our own folders; the lock file may already be gone after a partial delete
    # (Windows keeps files that are still open), so it is not required here.
    if not (path.parent.name == TEMP_ROOT_NAME and _ATTEMPT_NAME.fullmatch(path.name)):
        log.warning("Refusing to clean a folder that is not an attempt directory: %s", path)
        return False
    shutil.rmtree(path, ignore_errors=True)
    try:
        path.parent.rmdir()   # remove .vdl_tmp when empty; fails harmlessly otherwise
    except OSError:
        pass
    return not path.exists()


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)  # type: ignore[attr-defined]
        if handle:
            ctypes.windll.kernel32.CloseHandle(handle)  # type: ignore[attr-defined]
            return True
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def cleanup_stale_attempts(dest_dir: Path, max_age_hours: float = 1.0) -> int:
    """Remove attempt directories left by crashed runs. Only folders created by this
    application (valid name + lock file) whose owning process is gone are removed."""
    root = Path(dest_dir) / TEMP_ROOT_NAME
    removed = 0
    if not root.is_dir():
        return 0
    for child in root.iterdir():
        if not child.is_dir() or not (_ATTEMPT_NAME.fullmatch(child.name)):
            continue
        lock = child / LOCK_NAME
        try:
            pid = int(lock.read_text(encoding="ascii").strip() or "0")
            age = time.time() - lock.stat().st_mtime
        except (OSError, ValueError):
            pid, age = 0, float("inf")
        if (child / RESUME_MARKER).exists():
            # Partial download kept on purpose so it can be resumed: only expire it after a week.
            if age < RESUME_KEEP_DAYS * 86400:
                continue
        if pid == os.getpid() or (_pid_alive(pid) and age < max_age_hours * 3600 * 24):
            continue
        if age >= max_age_hours * 3600 or not _pid_alive(pid):
            shutil.rmtree(child, ignore_errors=True)
            removed += 1
    try:
        root.rmdir()
    except OSError:
        pass
    return removed


# ----------------------------------------------------------------------------- finalisation
def _move_no_clobber(src: Path, target: Path) -> None:
    """Move src to target atomically without ever replacing an existing target."""
    try:
        os.link(src, target)          # fails with FileExistsError if target exists (atomic check)
    except FileExistsError:
        raise
    except (OSError, NotImplementedError, AttributeError):
        # Filesystem without hard links (FAT/exFAT): os.rename refuses to overwrite on Windows;
        # on POSIX check first (tiny race window, documented in README).
        if target.exists():
            raise FileExistsError(errno.EEXIST, "File exists", str(target))
        os.rename(src, target)
        return
    os.unlink(src)


def finalize_file(src: Path, dest_dir: Path, stem: str, policy: str = "rename") -> Tuple[Path, bool]:
    """Move a finished temp file into dest_dir as `<stem><suffix>`.

    policy: rename (add " (n)"), skip (keep existing, discard new), overwrite (explicit replace).
    Returns (final_path, skipped_existing).
    """
    src = Path(src)
    dest_dir = ensure_dir(dest_dir)
    target = dest_dir / f"{sanitize_filename(stem)}{src.suffix.lower()}"

    if policy == "overwrite":
        os.replace(src, target)
        log.info("Overwrote existing file by user policy: %s", target.name)
        return target, False

    if policy == "skip" and target.exists() and target.stat().st_size > 0:
        return target, True

    for _ in range(1000):
        candidate = unique_path(target) if policy == "rename" else target
        try:
            _move_no_clobber(src, candidate)
            return candidate, False
        except FileExistsError:
            if policy != "rename":
                return candidate, True
            continue  # somebody created it in between; pick the next number
    raise FileError("Could not finalize the file: too many name conflicts.")


def list_partial_files(directory: Path) -> List[Path]:
    return [p for p in Path(directory).iterdir() if p.is_file() and p.name.lower().endswith(_PARTIAL)]
