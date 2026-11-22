"""Shared data types, exceptions and the abstract engine interface."""
from __future__ import annotations

import os
import queue
import re
import subprocess
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Callable, List, Optional, Sequence, Tuple

if TYPE_CHECKING:  # avoid circular imports at runtime
    from core.cookie_manager import CookieConfig
    from core.ffmpeg_manager import FfmpegManager
    from core.quality_manager import QualitySpec

LogCallback = Callable[[str, str], None]          # (level, message)
ProgressCallback = Callable[["ProgressInfo"], None]

TEMP_STEM = "media"  # every engine writes "media.<ext>" inside its private attempt directory


# ----------------------------------------------------------------------------- exceptions
class EngineError(Exception):
    """Base class for engine failures. `kind` is one of utils.network error kinds (or '')."""

    def __init__(self, message: str, kind: str = "") -> None:
        super().__init__(message)
        self.kind = kind


class EngineUnsupported(EngineError):
    """The engine cannot handle this URL/quality combination."""


class MissingDependencyError(EngineError):
    """The Python package behind an engine is not installed."""

    def __init__(self, message: str) -> None:
        super().__init__(message, "missing_dependency")


class DownloadCancelled(Exception):
    """Raised when the user cancels."""


# ----------------------------------------------------------------------------- data types
class CancelToken:
    """Stop request. `reason` is "cancel" (discard partial files) or "pause" (keep them to resume)."""

    def __init__(self) -> None:
        self._event = threading.Event()
        self.reason = "cancel"

    _PRIORITY = {"pause": 0, "keep": 1, "cancel": 2}

    def cancel(self, reason: str = "cancel") -> None:
        """reason: "pause" (keep data, resumable), "keep" (cancel but keep partial files), "cancel" (discard).
        A stronger request overrides a weaker one that was made earlier."""
        if not self._event.is_set() or self._PRIORITY.get(reason, 2) >= self._PRIORITY.get(self.reason, 2):
            self.reason = reason
        self._event.set()

    @property
    def keep_partial(self) -> bool:
        """True when partial files must survive this stop (pause, or cancel with 'keep partial files')."""
        return self._event.is_set() and self.reason in ("pause", "keep")

    @property
    def is_pause(self) -> bool:
        return self._event.is_set() and self.reason == "pause"

    @property
    def event(self) -> threading.Event:
        return self._event

    @property
    def is_cancelled(self) -> bool:
        return self._event.is_set()

    def raise_if_cancelled(self) -> None:
        if self._event.is_set():
            raise DownloadCancelled()


@dataclass
class ProgressInfo:
    percent: float = -1.0          # -1 means unknown
    speed: float = 0.0             # bytes / second
    eta: Optional[int] = None      # seconds
    downloaded: int = 0
    total: int = 0
    stage: str = "downloading"     # downloading | merging | converting | finished
    engine: str = ""
    title: str = ""
    filename: str = ""


@dataclass
class FormatInfo:
    format_id: str
    ext: str = ""
    height: Optional[int] = None
    fps: Optional[float] = None
    vcodec: str = "none"
    acodec: str = "none"
    filesize: Optional[int] = None
    abr: Optional[float] = None

    @property
    def has_video(self) -> bool:
        return self.vcodec not in ("none", "", None)

    @property
    def has_audio(self) -> bool:
        return self.acodec not in ("none", "", None)


@dataclass
class PlaylistEntry:
    index: int
    title: str
    url: str
    available: bool = True
    id: str = ""
    thumbnail: str = ""
    duration: Optional[int] = None       # seconds, when the playlist listing provides it
    size: Optional[int] = None           # bytes, only when the source states it


@dataclass
class MediaInfo:
    url: str
    title: str = ""
    id: str = ""
    uploader: str = ""
    duration: Optional[int] = None
    upload_date: str = ""
    thumbnail: str = ""
    is_playlist: bool = False
    entries: List[PlaylistEntry] = field(default_factory=list)
    formats: List[FormatInfo] = field(default_factory=list)


@dataclass
class DownloadRequest:
    url: str
    work_dir: Path                      # private attempt directory
    quality: "QualitySpec"
    platform: str
    cookies: "CookieConfig"
    ffmpeg: "FfmpegManager"
    noplaylist: bool = True
    connections: int = 8                # parallel fragments/connections where the engine supports it
    use_aria2c: bool = False            # hand the transfer to aria2c (multi-connection) when installed
    rate_limit_kbps: int = 0            # 0 = unlimited


@dataclass
class DownloadResult:
    file_path: Path
    engine: str
    title: str = ""
    resolution: str = ""
    container: str = ""
    size: int = 0
    skipped_existing: bool = False


# ----------------------------------------------------------------------------- engine interface
class BaseEngine(ABC):
    name: str = "base"
    supports_resume: bool = False   # True when a partially downloaded work_dir can be reused

    @abstractmethod
    def is_installed(self) -> bool:
        """True when the underlying library can be imported."""

    @abstractmethod
    def supports(self, platform: str, quality: "QualitySpec", cookies: "CookieConfig") -> Tuple[bool, str]:
        """Return (compatible, reason). Reason explains an incompatibility."""

    @abstractmethod
    def download(
        self,
        request: DownloadRequest,
        progress_cb: ProgressCallback,
        log_cb: LogCallback,
        cancel: CancelToken,
    ) -> DownloadResult:
        """Download into request.work_dir as TEMP_STEM.<ext>. Must return only after every
        thread/process it started has finished."""

    def fetch_info(self, url: str, cookies: "CookieConfig", noplaylist: bool,
                   log_cb: LogCallback) -> Optional[MediaInfo]:
        """Optional metadata extraction. Engines that cannot do it return None."""
        return None


# ----------------------------------------------------------------------------- helpers
_PARTIAL_SUFFIXES = (".part", ".ytdl", ".temp", ".tmp", ".partial", ".download")


def find_output_file(work_dir: Path, stem: str = TEMP_STEM) -> Optional[Path]:
    """Largest completed file named `stem.*` in work_dir, ignoring partial/aux files."""
    candidates: List[Path] = []
    for path in Path(work_dir).iterdir():
        if not path.is_file() or not path.name.startswith(stem + "."):
            continue
        lowered = path.name.lower()
        if lowered.endswith(_PARTIAL_SUFFIXES) or ".part-" in lowered:
            continue
        if re.match(rf"^{re.escape(stem.lower())}\.f[\w-]+\.", lowered):
            continue  # yt-dlp intermediate stream (e.g. media.f137.mp4) left by a failed merge
        if path.suffix.lower() in (".json", ".txt", ".vtt", ".srt", ".jpg", ".png", ".webp", ".description"):
            continue
        candidates.append(path)
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_size)


def run_subprocess(
    args: Sequence[str],
    cwd: Path,
    line_cb: Callable[[str], None],
    cancel: CancelToken,
    timeout: Optional[float] = None,
) -> int:
    """Run `args` (never through a shell), streaming merged output lines to `line_cb`.

    Cancellation terminates the process and waits for it, so no child outlives this call.
    Returns the exit code; raises DownloadCancelled on cancel and EngineError(kind=timeout)
    when the timeout elapses.
    """
    cancel.raise_if_cancelled()
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    proc = subprocess.Popen(  # noqa: S603 - argument list, no shell
        list(args), cwd=str(cwd), env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace", bufsize=1,
    )
    lines: "queue.Queue[Optional[str]]" = queue.Queue()

    def _reader() -> None:
        assert proc.stdout is not None
        for raw in proc.stdout:
            lines.put(raw.rstrip("\r\n"))
        lines.put(None)

    thread = threading.Thread(target=_reader, daemon=True)
    thread.start()
    waited = 0.0
    finished_reading = False
    try:
        while not finished_reading:
            cancel.raise_if_cancelled()
            try:
                item = lines.get(timeout=0.2)
            except queue.Empty:
                item = ""
                waited += 0.2
            else:
                if item is None:
                    finished_reading = True
                    continue
                if item:
                    line_cb(item)
            if cancel.is_cancelled:
                raise DownloadCancelled()
            if timeout and waited > timeout and proc.poll() is None:
                raise EngineError("The process timed out.", "timeout")
        code = proc.wait()
        cancel.raise_if_cancelled()
        return code
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        thread.join(timeout=5)
