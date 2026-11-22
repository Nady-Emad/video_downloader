"""Runs engines in priority order with safe temp files, retries and atomic finalisation."""
from __future__ import annotations

import gc
import hashlib
import threading
from dataclasses import replace
from pathlib import Path
from typing import Callable, List, Optional, Sequence, Tuple

from core.cookie_manager import CookieConfig, prepare_private_copy
from core.ffmpeg_manager import FfmpegManager
from core.quality_manager import QualitySpec
from engines.base_engine import (
    BaseEngine, CancelToken, DownloadCancelled, DownloadRequest, DownloadResult, EngineError, LogCallback,
    ProgressCallback, ProgressInfo,
)
from utils import file_manager
from utils.network import (
    CANCELLED, UNKNOWN, UNSUPPORTED, ErrorInfo, classify_error, interruptible_sleep,
)

EngineCallback = Callable[[str], None]
NameFactory = Callable[[str], str]


class DownloadFailed(Exception):
    """Every compatible engine failed (or a permanent error stopped the chain)."""

    def __init__(self, info: ErrorInfo, failures: Sequence[Tuple[str, ErrorInfo]],
                 resumable: bool = False) -> None:
        super().__init__(info.message)
        self.info = info
        self.failures = list(failures)
        self.resumable = resumable      # partial data was kept and the download can be resumed


class _TargetRegistry:
    """Process-wide registry that prevents two workers producing the same output file."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._active: set = set()

    def claim(self, key: str) -> bool:
        with self._lock:
            if key in self._active:
                return False
            self._active.add(key)
            return True

    def release(self, key: str) -> None:
        with self._lock:
            self._active.discard(key)


_REGISTRY = _TargetRegistry()


class FallbackManager:
    """Engines must be passed in priority order: yt-dlp, pytubefix, gallery-dl, you-get."""

    def __init__(self, engines: Sequence[BaseEngine], ffmpeg: FfmpegManager,
                 max_retries: int = 5, retry_delay: float = 3.0, connections: int = 8,
                 use_aria2c: bool = False, rate_limit_kbps: int = 0, conflict_policy: str = "rename") -> None:
        self.engines = list(engines)
        self.ffmpeg = ffmpeg
        self.connections = max(1, min(16, int(connections)))
        self.use_aria2c = bool(use_aria2c)
        self.rate_limit_kbps = max(0, int(rate_limit_kbps))
        self.max_retries = max(0, int(max_retries))
        self.retry_delay = max(0.0, float(retry_delay))
        self.conflict_policy = conflict_policy

    # ------------------------------------------------------------------ planning
    def plan(self, platform: str, quality: QualitySpec,
             cookies: CookieConfig) -> Tuple[List[BaseEngine], List[Tuple[str, str]]]:
        """Return (usable engines in priority order, [(engine name, reason skipped)])."""
        usable: List[BaseEngine] = []
        skipped: List[Tuple[str, str]] = []
        for engine in self.engines:
            ok, reason = engine.supports(platform, quality, cookies)
            if ok:
                usable.append(engine)
            else:
                skipped.append((engine.name, reason or "not compatible"))
        return usable, skipped

    # ------------------------------------------------------------------ main entry
    def run(
        self,
        url: str,
        platform: str,
        quality: QualitySpec,
        cookies: CookieConfig,
        dest_dir: Path,
        name_for: NameFactory,
        progress_cb: ProgressCallback,
        log_cb: LogCallback,
        cancel: CancelToken,
        engine_cb: Optional[EngineCallback] = None,
        title_hint: str = "",
        noplaylist: bool = True,
        resume_key: str = "",
    ) -> DownloadResult:
        log_cb("INFO", "Initializing download.")
        log_cb("INFO", f"Platform detected: {platform}.")
        dest = file_manager.ensure_dir(Path(dest_dir))
        file_manager.check_disk_space(dest)

        usable, skipped = self.plan(platform, quality, cookies)
        for name, reason in skipped:
            log_cb("INFO", f"Skipping {name}: {reason}.")
        if not usable:
            info = ErrorInfo("missing_dependency" if all("not installed" in r for _, r in skipped) else UNSUPPORTED,
                             "No installed download engine can handle this URL with the selected options. "
                             "Install yt-dlp with: pip install yt-dlp")
            log_cb("ERROR", info.message)
            raise DownloadFailed(info, [])

        # Existing output / duplicate protection.
        if title_hint:
            stem_hint = name_for(title_hint)
            existing = file_manager.find_existing(dest, stem_hint)
            if existing and self.conflict_policy == "skip":
                log_cb("INFO", f"File already exists, skipping: {existing.name}")
                return DownloadResult(file_path=existing, engine="existing file", title=title_hint,
                                      size=existing.stat().st_size, skipped_existing=True)
            claim_key = str((dest / stem_hint).resolve()).lower()
        else:
            claim_key = f"{dest.resolve()}|{url}".lower()
        if not _REGISTRY.claim(claim_key):
            info = ErrorInfo("duplicate", "The same download is already running.")
            log_cb("WARNING", info.message)
            raise DownloadFailed(info, [])

        failures: List[Tuple[str, ErrorInfo]] = []
        resumable_any = False
        try:
            for position, engine in enumerate(usable):
                cancel.raise_if_cancelled()
                if position == 0 and engine.name == "yt-dlp":
                    log_cb("INFO", "Attempting primary engine: yt-dlp.")
                elif position == 0:
                    log_cb("INFO", f"Attempting engine: {engine.name}.")
                else:
                    log_cb("INFO", f"Switching to {engine.name}.")
                if engine_cb:
                    engine_cb(engine.name)
                try:
                    result = self._run_engine(engine, url, platform, quality, cookies, dest, name_for,
                                              progress_cb, log_cb, cancel, title_hint, noplaylist, resume_key)
                    if resume_key:      # another engine may have finished: drop leftovers of the resumable one
                        file_manager.cleanup_attempt_dir(dest / file_manager.TEMP_ROOT_NAME / resume_key)
                    return result
                except DownloadCancelled:
                    raise
                except DownloadFailed as failed:
                    failures.append((engine.name, failed.info))
                    if failed.resumable:
                        resumable_any = True
                        log_cb("WARNING", "Connection problem: stopping here so the saved progress can be resumed.")
                        break
                    if failed.info.stops_chain:
                        log_cb("ERROR", f"{failed.info.message} No other engine can fix this; stopping.")
                        break
            info = self._best_failure(failures)
            log_cb("ERROR", f"Download failed: {info.message}")
            raise DownloadFailed(info, failures, resumable=resumable_any)
        finally:
            _REGISTRY.release(claim_key)

    # ------------------------------------------------------------------ one engine
    def _run_engine(self, engine: BaseEngine, url: str, platform: str, quality: QualitySpec,
                    cookies: CookieConfig, dest: Path, name_for: NameFactory, progress_cb: ProgressCallback,
                    log_cb: LogCallback, cancel: CancelToken, title_hint: str, noplaylist: bool,
                    resume_key: str = "") -> DownloadResult:
        """Run one engine with retries. Engines that support resuming keep their partial files in a
        stable folder (kept on pause or network loss, deleted on success, cancel or permanent errors)."""
        last_info = ErrorInfo(UNKNOWN, "Unknown error.")
        resumable = bool(resume_key) and engine.supports_resume
        keep = False
        for attempt in range(1, self.max_retries + 2):
            if resumable:
                gc.collect()      # a previous paused/failed attempt may still hold the .part file open (Windows)
                signature = hashlib.sha1(f"{url}|{quality.cache_key()}"
                                         .encode("utf-8")).hexdigest()
                work = file_manager.resume_dir(dest, resume_key, signature)
                partial = [p for p in work.iterdir() if p.name.startswith("media")]
                if partial:
                    size = sum(p.stat().st_size for p in partial if p.is_file())
                    log_cb("INFO", f"Found a verified partial download ({size / 1048576:.1f} MB); "
                                   "resuming where it stopped.")
            else:
                work = file_manager.make_attempt_dir(dest)
            keep = False
            try:
                log_cb("DEBUG", f"{engine.name}: attempt {attempt} in a private temp folder.")
                request = DownloadRequest(
                    url=url, work_dir=work, quality=quality, platform=platform,
                    cookies=prepare_private_copy(cookies, work), ffmpeg=self.ffmpeg, noplaylist=noplaylist,
                    connections=self.connections, use_aria2c=self.use_aria2c,
                    rate_limit_kbps=self.rate_limit_kbps,
                )
                log_cb("INFO", "Download started.")
                try:
                    result = engine.download(request, progress_cb, log_cb, cancel)
                finally:
                    gc.collect()      # close file handles an interrupted engine left open (Windows WinError 32)
                cancel.raise_if_cancelled()
                return self._finalize(result, work, dest, name_for, title_hint, log_cb, quality, progress_cb, cancel)
            except DownloadCancelled:
                keep = resumable and cancel.keep_partial
                if cancel.reason == "pause" and not resumable:
                    log_cb("WARNING", f"{engine.name} cannot resume a partial download; "
                                      "the file will start from the beginning when resumed.")
                log_cb("WARNING", "Download paused; progress saved." if keep
                       else "Download cancelled; removing temporary files.")
                raise
            except Exception as exc:  # engines may raise anything; classify it
                last_info = classify_error(exc)
                if last_info.kind == CANCELLED or cancel.is_cancelled:
                    keep = resumable and cancel.keep_partial
                    raise DownloadCancelled() from exc
                log_cb("ERROR", f"{engine.name} failed: {last_info.message}")
                log_cb("DEBUG", f"{engine.name} technical details: {exc!r}")
                keep = resumable and last_info.transient     # network trouble: keep what was downloaded
                if last_info.transient and attempt <= self.max_retries:
                    delay = self.backoff_delay(attempt)
                    log_cb("INFO", f"Retrying {engine.name} in {delay:g}s "
                                   f"(attempt {attempt + 1} of {self.max_retries + 1}).")
                    if not interruptible_sleep(delay, cancel.event):
                        keep = resumable and cancel.keep_partial
                        raise DownloadCancelled() from exc
                    continue
                break
            finally:
                # The engine call has returned (or raised), so nothing is writing here any more.
                if not keep:
                    file_manager.cleanup_attempt_dir(work)
        if keep:
            log_cb("WARNING", "Progress was saved and can be resumed.")
        raise DownloadFailed(last_info, [(engine.name, last_info)], resumable=keep)

    MAX_BACKOFF = 120.0

    def backoff_delay(self, attempt: int) -> float:
        """Exponential backoff: retry_delay, 2x, 4x, ... capped at MAX_BACKOFF seconds."""
        return min(self.retry_delay * (2 ** max(0, attempt - 1)), self.MAX_BACKOFF)

    def _finalize(self, result: DownloadResult, work: Path, dest: Path, name_for: NameFactory,
                  title_hint: str, log_cb: LogCallback, quality: Optional[QualitySpec] = None,
                  progress_cb: Optional[ProgressCallback] = None,
                  cancel: Optional[CancelToken] = None) -> DownloadResult:
        source = Path(result.file_path)
        if work not in source.parents:
            raise EngineError("The engine wrote outside its temporary folder.", "corrupted")
        expect_video = quality is not None and not quality.is_audio

        # 1. Validate with ffprobe (applies to every engine): video downloads must contain video.
        check = self.ffmpeg.validate_output(source, expect_video=expect_video)
        if not check.ok:
            raise EngineError(check.message, "corrupted")
        log_cb("INFO" if check.streams else "DEBUG", check.message)
        for warning in check.warnings:
            log_cb("WARNING", warning)

        # 2. Compatibility mode: MP4 + H.264 + AAC, re-encoding only the streams that need it.
        if expect_video and quality is not None and quality.compat and source.suffix.lower() == ".mp4" \
                and check.streams is not None:
            video_tc, audio_tc = self.ffmpeg.needs_compat_transcode(check.streams)
            if video_tc or audio_tc:
                if not self.ffmpeg.available:
                    log_cb("WARNING", "This file uses a codec some players cannot show (FFmpeg is needed to convert it).")
                else:
                    what = " and ".join(n for n, f in (("video to H.264", video_tc), ("audio to AAC", audio_tc)) if f)
                    log_cb("INFO", f"Compatibility mode: converting {what} (the other stream is copied).")
                    if progress_cb:
                        progress_cb(ProgressInfo(percent=-1.0, stage="converting", engine=result.engine))
                    converted = work / "media.compat.mp4"
                    self.ffmpeg.make_compatible(source, converted, check.streams, cancel)
                    recheck = self.ffmpeg.validate_output(converted, expect_video=True)
                    if not recheck.ok:
                        raise EngineError(f"Conversion produced an invalid file: {recheck.message}", "ffmpeg")
                    source.unlink(missing_ok=True)
                    source = converted
                    log_cb("INFO", recheck.message)

        stem = name_for(result.title or title_hint or source.stem)
        final, skipped = file_manager.finalize_file(source, dest, stem, self.conflict_policy)
        if skipped:
            log_cb("INFO", f"File already exists, kept the existing one: {final.name}")
        else:
            log_cb("SUCCESS", f"Download completed: {final.name}")
        return replace(result, file_path=final, size=final.stat().st_size, skipped_existing=skipped,
                       container=final.suffix.lstrip("."))

    # ------------------------------------------------------------------ reporting
    @staticmethod
    def _best_failure(failures: Sequence[Tuple[str, ErrorInfo]]) -> ErrorInfo:
        """Most informative failure: first one that is not just 'unsupported/unknown'."""
        for _, info in failures:
            if info.kind not in (UNSUPPORTED, UNKNOWN):
                return info
        return failures[0][1] if failures else ErrorInfo(UNKNOWN, "Download failed.")


def default_engines() -> List[BaseEngine]:
    """The required engine priority: yt-dlp, pytubefix, gallery-dl, you-get."""
    from engines.gallery_dl_engine import GalleryDlEngine
    from engines.pytubefix_engine import PytubefixEngine
    from engines.youget_engine import YouGetEngine
    from engines.ytdlp_engine import YtdlpEngine

    return [YtdlpEngine(), PytubefixEngine(), GalleryDlEngine(), YouGetEngine()]
