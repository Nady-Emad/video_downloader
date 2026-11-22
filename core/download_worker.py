"""QThread wrappers. All GUI communication happens through signals (never by touching widgets)."""
from __future__ import annotations

import time
import traceback
from typing import Any, Dict, Optional, Tuple

from PyQt6.QtCore import QThread, pyqtSignal

from core.cookie_manager import CookieConfig
from core.download_history import DownloadHistory
from core.download_queue import QueueTask, TaskStatus
from core.fallback_manager import default_engines
from core.ffmpeg_manager import FfmpegManager
from core.platform_detector import YOUTUBE, detect_platform, is_playlist_url, playlist_target, youtube_video_id
from core.task_runner import TaskCallbacks, TaskRunner
from engines.base_engine import CancelToken, DownloadCancelled, MediaInfo, ProgressInfo
from utils.logger import get_logger, make_log_callback
from utils.network import classify_error, network_available
from utils.validators import validate_url

log = get_logger("worker")

_PROGRESS_INTERVAL = 0.1  # seconds between progress signals (keeps the GUI responsive)


class DownloadWorker(QThread):
    """Runs one queue task. Do not name any signal `finished`: QThread already has one."""

    log_message = pyqtSignal(str, str, str)         # task_id, level, message
    progress = pyqtSignal(str, object)              # task_id, ProgressInfo
    status_changed = pyqtSignal(str, str, str)      # task_id, status, detail
    playlist_progress = pyqtSignal(str, int, int, int)   # task_id, done, total, failed
    playlist_item = pyqtSignal(str, int, str)            # task_id, item index, status
    engine_changed = pyqtSignal(str, str)           # task_id, engine name
    title_changed = pyqtSignal(str, str)            # task_id, title
    thumbnail_changed = pyqtSignal(str, str)        # task_id, thumbnail URL
    history_changed = pyqtSignal()
    task_done = pyqtSignal(str, str, str)           # task_id, final status, message

    def __init__(self, task: QueueTask, settings: Dict[str, Any], cookies: CookieConfig,
                 ffmpeg: FfmpegManager, history: DownloadHistory, parent: Optional[Any] = None) -> None:
        super().__init__(parent)
        self.task = task
        self._settings = settings
        self._cookies = cookies
        self._ffmpeg = ffmpeg
        self._history = history
        self._cancel = CancelToken()
        self._last_progress = 0.0
        self.result: Optional[Tuple[str, str]] = None   # (final status, message) once run() has finished

    @property
    def task_id(self) -> str:
        return self.task.id

    def request_cancel(self, keep_partial: bool = False) -> None:
        """Stop for good. Partial files are discarded unless keep_partial is True.
        Thread-safe; the engine stops at its next progress/poll point."""
        self._cancel.cancel("keep" if keep_partial else "cancel")

    def request_pause(self) -> None:
        """Stop but KEEP partial files so the download can be resumed later."""
        self._cancel.cancel("pause")

    def _emit_progress(self, info: ProgressInfo) -> None:
        now = time.monotonic()
        if info.stage != "downloading" or info.percent >= 100.0 or now - self._last_progress >= _PROGRESS_INTERVAL:
            self._last_progress = now
            self.progress.emit(self.task.id, info)

    def run(self) -> None:
        task_id = self.task.id
        status, message = TaskStatus.FAILED, "The download ended unexpectedly."
        try:
            callbacks = TaskCallbacks()
            callbacks.log = make_log_callback(
                "task", lambda level, msg: self.log_message.emit(task_id, level, msg))
            callbacks.progress = self._emit_progress
            callbacks.status = lambda st, detail: self.status_changed.emit(task_id, st, detail)
            callbacks.playlist = lambda d, t, f: self.playlist_progress.emit(task_id, d, t, f)
            callbacks.item = lambda i, st: self.playlist_item.emit(task_id, i, st)
            callbacks.engine = lambda name: self.engine_changed.emit(task_id, name)
            callbacks.title = lambda title: self.title_changed.emit(task_id, title)
            callbacks.thumbnail = lambda url: self.thumbnail_changed.emit(task_id, url)
            callbacks.history_changed = self.history_changed.emit
            runner = TaskRunner(self.task, self._settings, self._cookies, self._ffmpeg, default_engines(),
                                self._history, callbacks, self._cancel)
            status, message = runner.run()
        except BaseException as exc:  # last line of defence: a thread must never die silently
            log.error("Worker crashed:\n%s", traceback.format_exc())
            status, message = TaskStatus.FAILED, classify_error(exc).message
        finally:
            self.result = (status, message)
            self.task_done.emit(task_id, status, message)


class QuickPreviewWorker(QThread):
    """Instant preview (title + thumbnail) for YouTube links, before the slow full extraction finishes."""

    preview_ready = pyqtSignal(str, str, str)        # url, title, thumbnail URL

    def __init__(self, url: str, parent: Optional[Any] = None) -> None:
        super().__init__(parent)
        self.url = url

    def run(self) -> None:
        import json
        import urllib.parse
        import urllib.request
        video_id = youtube_video_id(self.url)
        if not video_id:
            return
        thumb = f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"
        title = ""
        try:
            endpoint = "https://www.youtube.com/oembed?format=json&url=" + urllib.parse.quote(
                f"https://www.youtube.com/watch?v={video_id}", safe="")
            request = urllib.request.Request(endpoint, headers={"User-Agent": "Mozilla/5.0 VideoDownloader"})
            with urllib.request.urlopen(request, timeout=6) as response:  # noqa: S310 - fixed https endpoint
                data = json.loads(response.read(200_000).decode("utf-8", "replace"))
            title = str(data.get("title") or "")
            thumb = str(data.get("thumbnail_url") or thumb)
        except Exception:      # offline, blocked or private video: the thumbnail URL alone is still useful
            pass
        self.preview_ready.emit(self.url, title, thumb)


_INFO_CACHE: "dict[str, tuple[float, Any]]" = {}
_INFO_TTL = 600.0            # seconds a metadata result is reused (pasting the same link again is instant)
_INFO_MAX = 32


def _cached_info(url: str) -> Any:
    import time
    hit = _INFO_CACHE.get(url)
    if hit and time.monotonic() - hit[0] < _INFO_TTL:
        return hit[1]
    _INFO_CACHE.pop(url, None)
    return None


def _store_info(url: str, info: Any) -> None:
    import time
    if len(_INFO_CACHE) >= _INFO_MAX:
        _INFO_CACHE.pop(min(_INFO_CACHE, key=lambda k: _INFO_CACHE[k][0]), None)
    _INFO_CACHE[url] = (time.monotonic(), info)


class InfoWorker(QThread):
    """Extracts metadata (formats / playlist entries) without downloading."""

    info_ready = pyqtSignal(str, object)     # url, MediaInfo
    info_failed = pyqtSignal(str, str)       # url, readable message
    log_message = pyqtSignal(str, str)       # level, message

    def __init__(self, url: str, cookies: CookieConfig, parent: Optional[Any] = None) -> None:
        super().__init__(parent)
        self.url = url
        self._cookies = cookies

    def run(self) -> None:
        try:
            url = validate_url(self.url)
            engine = next((e for e in default_engines() if e.name == "yt-dlp"), None)
            if engine is None or not engine.is_installed():
                self.info_failed.emit(self.url, "yt-dlp is not installed. Run: pip install yt-dlp")
                return
            cached = _cached_info(url)
            if cached is not None:
                self.info_ready.emit(self.url, cached)
                return
            platform = detect_platform(url)
            noplaylist = platform == YOUTUBE and not is_playlist_url(url)
            callback = make_log_callback("info", lambda level, msg: self.log_message.emit(level, msg))
            info: Optional[MediaInfo] = engine.fetch_info(playlist_target(url), self._cookies, noplaylist, callback)
            if info is None:
                self.info_failed.emit(self.url, "No information could be extracted.")
            else:
                _store_info(url, info)
                self.info_ready.emit(self.url, info)
        except ValueError as exc:
            self.info_failed.emit(self.url, str(exc))
        except DownloadCancelled:
            self.info_failed.emit(self.url, "Cancelled.")
        except Exception as exc:
            log.error("Info worker error: %s", traceback.format_exc())
            self.info_failed.emit(self.url, classify_error(exc).message)


class NetworkProbe(QThread):
    """Checks (off the GUI thread) whether the internet connection is back."""

    result = pyqtSignal(bool)

    def run(self) -> None:
        try:
            ok = network_available()
        except Exception:
            ok = False
        self.result.emit(ok)
