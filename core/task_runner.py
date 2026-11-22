"""Executes one queue task (single video or playlist). Qt-free so it can be tested without a GUI.

Callbacks are invoked from the worker thread; the Qt wrapper turns them into signals.
"""
from __future__ import annotations

import json
import traceback
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Sequence, Tuple

from core import playlist_manager, thumbnail_tools
from core.cookie_manager import CookieConfig
from core.download_history import STATUS_COMPLETED, STATUS_FAILED, DownloadHistory, HistoryRecord
from core.download_queue import QueueTask, TaskStatus
from core.fallback_manager import DownloadFailed, FallbackManager
from core.ffmpeg_manager import FfmpegManager
from core.platform_detector import YOUTUBE, detect_platform, is_playlist_url, playlist_target
from engines.base_engine import (
    BaseEngine, CancelToken, DownloadCancelled, DownloadResult, MediaInfo, ProgressInfo,
)
from utils import file_manager
from utils.logger import get_logger
from utils.network import classify_error
from utils.validators import validate_url

log = get_logger("runner")

# Message used when a download paused itself because the connection dropped (manager matches on it).
WAITING_NETWORK_MESSAGE = "Interrupted: connection lost - progress saved. Resumes automatically when the network is back."


class TaskCallbacks:
    """Bundle of optional callbacks (all default to no-ops)."""

    def __init__(self) -> None:
        self.log: Callable[[str, str], None] = lambda level, msg: None
        self.progress: Callable[[ProgressInfo], None] = lambda info: None
        self.status: Callable[[str, str], None] = lambda status, detail: None
        self.playlist: Callable[[int, int, int], None] = lambda done, total, failed: None
        self.item: Callable[[int, str], None] = lambda index, status: None     # playlist item index, status
        self.engine: Callable[[str], None] = lambda name: None
        self.title: Callable[[str], None] = lambda title: None
        self.thumbnail: Callable[[str], None] = lambda url: None
        self.history_changed: Callable[[], None] = lambda: None


class TaskRunner:
    def __init__(self, task: QueueTask, settings: Dict[str, Any], cookies: CookieConfig,
                 ffmpeg: FfmpegManager, engines: Sequence[BaseEngine], history: DownloadHistory,
                 callbacks: TaskCallbacks, cancel: CancelToken) -> None:
        self.task = task
        self.settings = settings
        self.cookies = cookies
        self.ffmpeg = ffmpeg
        self.engines = list(engines)
        self.history = history
        self.cb = callbacks
        self.cancel = cancel
        self.fallback = FallbackManager(
            self.engines, ffmpeg, max_retries=int(settings.get("max_retries", 5)),
            connections=int(settings.get("download_connections", 8)), use_aria2c=bool(settings.get("use_aria2c", False)),
            rate_limit_kbps=int(settings.get("speed_limit_kbps", 0)),
            retry_delay=float(settings.get("retry_delay", 3)), conflict_policy=str(settings.get("conflict_policy", "rename")),
        )

    def _keep_partials(self) -> bool:
        return bool(self.settings.get("preserve_partials", True))

    def _after_download(self, file_path: Path, thumbnail: str) -> None:
        thumbnail_tools.process(self.settings, self.ffmpeg, Path(file_path), thumbnail, self.cb.log)

    # ------------------------------------------------------------------ entry point
    def run(self) -> Tuple[str, str]:
        """Run the task. Returns (final TaskStatus, message)."""
        try:
            return self._run()
        except DownloadCancelled:
            if self.cancel.reason == "pause":
                self.cb.log("INFO", "Download paused. Progress is saved; press Resume to continue.")
                return TaskStatus.PAUSED, ""
            self.cb.log("WARNING", "Download cancelled by the user.")
            return TaskStatus.CANCELLED, ""
        except DownloadFailed as failed:
            return TaskStatus.FAILED, failed.info.message
        except Exception as exc:  # unexpected thread exception: never crash the application
            log.error("Unexpected error in task %s:\n%s", self.task.id, traceback.format_exc())
            info = classify_error(exc)
            self.cb.log("ERROR", f"Unexpected error: {info.message}")
            return TaskStatus.FAILED, info.message

    # ------------------------------------------------------------------ helpers
    def _dest(self) -> Path:
        return Path(self.task.dest_dir).expanduser()

    def _record(self, title: str, url: str, platform: str, status: str, folder: Path,
                result: Optional[DownloadResult] = None, error: str = "", thumbnail: str = "") -> None:
        spec = self.task.quality
        self.history.add(HistoryRecord(
            title=title or url, url=url, platform=platform,
            file_path=str(result.file_path) if result else "",
            file_size=result.size if result else 0,
            resolution=(result.resolution if result and result.resolution else spec.label),
            format=(result.container if result and result.container else spec.container),
            engine=result.engine if result else "", status=status, error=error,
            spec_json=json.dumps(spec.to_dict()), dest_dir=str(folder), thumbnail=thumbnail,
        ))
        self.cb.history_changed()

    def _find_info_engine(self) -> Optional[BaseEngine]:
        return next((e for e in self.engines if e.name == "yt-dlp" and e.is_installed()), None)

    def _fetch_info(self, platform: str) -> Optional[MediaInfo]:
        engine = self._find_info_engine()
        if engine is None:
            self.cb.log("WARNING", "yt-dlp is not available; metadata extraction skipped.")
            return None
        noplaylist = platform == YOUTUBE and not is_playlist_url(self.task.url)
        try:
            info = engine.fetch_info(playlist_target(self.task.url), self.cookies, noplaylist, self.cb.log)
        except DownloadCancelled:
            raise
        except Exception as exc:
            error = classify_error(exc)
            if error.stops_chain:
                self.cb.log("ERROR", error.message)
                raise DownloadFailed(error, [(engine.name, error)]) from exc
            self.cb.log("WARNING", f"Metadata extraction failed ({error.message}); trying the download engines directly.")
            return None
        self.cancel.raise_if_cancelled()
        return info

    def _name_factory(self, info: Optional[MediaInfo]) -> Callable[[str], str]:
        template = str(self.settings.get("filename_template", "{title}"))

        def name_for(title: str) -> str:
            return file_manager.render_filename(
                template, title=title or (info.title if info else "") or "video",
                media_id=info.id if info else "", uploader=info.uploader if info else "",
                date=info.upload_date if info else "",
            )
        return name_for

    # ------------------------------------------------------------------ flow
    def _run(self) -> Tuple[str, str]:
        task = self.task
        self.cb.status(TaskStatus.EXTRACTING, "")
        try:
            url = validate_url(task.url)
        except ValueError as exc:
            self.cb.log("ERROR", str(exc))
            self._record(task.url, task.url, "", STATUS_FAILED, self._dest(), error=str(exc))
            return TaskStatus.FAILED, str(exc)

        platform = detect_platform(url)
        self.cb.log("INFO", f"Platform detected: {platform}.")
        self.cb.log("INFO", f"Authentication: {self.cookies.describe()}.")
        info = self._fetch_info(platform)
        if info and info.title:
            self.cb.title(info.title)
        if info and info.thumbnail:
            self.cb.thumbnail(info.thumbnail)
        self.cancel.raise_if_cancelled()
        self.cb.status(TaskStatus.DOWNLOADING, "")
        if info is None and platform == YOUTUBE and is_playlist_url(url):
            raise DownloadFailed(classify_error(RuntimeError("The playlist could not be read. Check the link and "
                                                             "your connection, then retry.")), [])
        if info and info.is_playlist:
            return self._run_playlist(url, platform, info)
        return self._run_single(url, platform, info)

    def _run_single(self, url: str, platform: str, info: Optional[MediaInfo]) -> Tuple[str, str]:
        previous = self.history.find_completed(url)
        if previous:
            self.cb.log("WARNING", f"This URL was downloaded before: {previous.file_path}")
        title = info.title if info else ""
        noplaylist = platform == YOUTUBE and not is_playlist_url(url)
        try:
            result = self.fallback.run(
                url, platform, self.task.quality, self.cookies, self._dest(), self._name_factory(info),
                self.cb.progress, self.cb.log, self.cancel, self.cb.engine, title_hint=title, noplaylist=noplaylist,
                resume_key=self.task.id if self._keep_partials() else "",
            )
        except DownloadFailed as failed:
            if failed.resumable:       # network dropped: keep the task, resume later (not a final failure)
                return TaskStatus.INTERRUPTED, WAITING_NETWORK_MESSAGE
            self._record(title, url, platform, STATUS_FAILED, self._dest(), error=failed.info.message,
                         thumbnail=info.thumbnail if info else "")
            raise
        self._after_download(result.file_path, info.thumbnail if info else "")
        self._record(result.title or title, url, platform, STATUS_COMPLETED, self._dest(), result,
                     thumbnail=info.thumbnail if info else "")
        return TaskStatus.COMPLETED, ""

    def _run_playlist(self, url: str, platform: str, info: MediaInfo) -> Tuple[str, str]:
        folder = file_manager.ensure_dir(playlist_manager.playlist_folder(info, self._dest()))
        wanted = {int(p) for p in self.task.playlist_items.split(",") if p.strip().isdigit()}
        entries = [e for e in info.entries if not wanted or e.index in wanted]
        total = len(entries)
        numbering_total = len(info.entries)          # file numbers keep the item's position in the playlist
        progress = playlist_manager.PlaylistProgress(total=total)
        template = str(self.settings.get("playlist_filename_template", "{index} - {title}"))
        if "{index" not in template:
            template = "{index} - " + template
        picked = f"{total} of {numbering_total} items selected" if wanted else f"{total} items"
        self.cb.log("INFO", f"Playlist detected: {info.title or 'Untitled'} ({picked}). Folder: {folder.name}")
        self.cb.playlist(0, total, 0)

        for entry in entries:
            self.cancel.raise_if_cancelled()
            if not entry.available or not entry.url:
                progress.skipped += 1
                self.cb.item(entry.index, "Unavailable")
                self.cb.log("WARNING", f"Item {entry.index}/{total} is unavailable and was skipped: {entry.title}")
                self.cb.playlist(progress.done, total, progress.failed + progress.skipped)
                continue
            stem = file_manager.render_filename(
                template, title=entry.title, index=entry.index, total=numbering_total, media_id=entry.id,
                playlist=info.title,
            )
            if playlist_manager.already_downloaded(folder, stem):
                progress.done += 1
                self.cb.item(entry.index, "Skipped (already downloaded)")
                self.cb.log("INFO", f"Item {entry.index}/{total} already downloaded, skipping: {stem}")
                self.cb.playlist(progress.done, total, progress.failed + progress.skipped)
                continue
            self.cb.title(entry.title)
            self.cb.thumbnail(entry.thumbnail or info.thumbnail)
            self.cb.item(entry.index, "Downloading")
            self.cb.log("INFO", f"Playlist item {entry.index}/{total}: {entry.title}")
            entry_platform = detect_platform(entry.url)
            try:
                result = self.fallback.run(
                    entry.url, entry_platform, self.task.quality, self.cookies, folder,
                    (lambda _title, _stem=stem: _stem), self.cb.progress, self.cb.log, self.cancel,
                    self.cb.engine, title_hint=entry.title, noplaylist=True,
                    resume_key=f"{self.task.id}-{entry.index}" if self._keep_partials() else "",
                )
            except DownloadFailed as failed:
                if failed.resumable:   # connection lost: stop the playlist here, finished items are kept
                    self.cb.item(entry.index, "Interrupted")
                    self.cb.log("WARNING", f"Playlist paused at item {entry.index}/{total}: {progress.summary()}.")
                    return TaskStatus.INTERRUPTED, WAITING_NETWORK_MESSAGE
                progress.failed += 1
                self.cb.item(entry.index, "Failed")
                self.cb.log("ERROR", f"Item {entry.index}/{total} failed: {failed.info.message}. Continuing with the next item.")
                self._record(entry.title, entry.url, entry_platform, STATUS_FAILED, folder, error=failed.info.message,
                             thumbnail=entry.thumbnail)
            else:
                progress.done += 1
                self.cb.item(entry.index, "Done")
                self._after_download(result.file_path, entry.thumbnail or info.thumbnail)
                self._record(result.title or entry.title, entry.url, entry_platform, STATUS_COMPLETED, folder, result,
                             thumbnail=entry.thumbnail)
            self.cb.playlist(progress.done, total, progress.failed + progress.skipped)

        summary = progress.summary()
        self.cb.log("INFO", f"Playlist finished: {summary}.")
        if total and progress.done == 0:
            return TaskStatus.FAILED, f"No item could be downloaded ({summary})."
        if progress.failed or progress.skipped:
            self.cb.log("WARNING", "Some items were not downloaded. Failed items are listed in History and can be retried.")
            return TaskStatus.COMPLETED, f"Completed with problems: {summary}."
        self.cb.log("SUCCESS", f"Playlist completed: {summary}.")
        return TaskStatus.COMPLETED, ""
