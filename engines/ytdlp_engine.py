"""Primary engine: yt-dlp."""
from __future__ import annotations

import shutil
from typing import Any, Dict, Optional, Tuple

from core import cookie_manager
from core.quality_manager import QualitySpec, available_heights, pick_height, ytdlp_format, ytdlp_sort
from engines.base_engine import (
    TEMP_STEM, BaseEngine, CancelToken, DownloadCancelled, DownloadRequest, DownloadResult, EngineError,
    FormatInfo, LogCallback, MediaInfo, MissingDependencyError, PlaylistEntry, ProgressCallback,
    ProgressInfo, find_output_file,
)
from utils.network import classify_error
from utils.system_info import js_runtime_path, module_available

_DELETED_TITLES = ("[private video]", "[deleted video]", "[unavailable video]")


class _YtdlpLogger:
    """Routes yt-dlp's own messages into the application log (warnings/errors only)."""

    def __init__(self, log_cb: LogCallback) -> None:
        self._log = log_cb

    def debug(self, msg: str) -> None:
        if msg.startswith("[debug]"):
            self._log("DEBUG", msg)

    def info(self, msg: str) -> None:
        pass

    def warning(self, msg: str) -> None:
        self._log("WARNING", f"yt-dlp: {msg}")

    def error(self, msg: str) -> None:
        self._log("DEBUG", f"yt-dlp: {msg}")  # the exception itself is reported by the caller


def _int(value: Any) -> Optional[int]:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _effective_height(fmt: Dict[str, Any]) -> Optional[int]:
    """Short side of the frame, matching how yt-dlp's `res` sort field and resolution labels work."""
    height, width = _int(fmt.get("height")), _int(fmt.get("width"))
    if height and width:
        return min(height, width)
    return height


def _entry_url(entry: Dict[str, Any]) -> str:
    for key in ("webpage_url", "url"):
        value = entry.get(key)
        if isinstance(value, str) and value.startswith(("http://", "https://")):
            return value
    ident = entry.get("id")
    if ident and str(entry.get("ie_key", "")).lower() == "youtube":
        return f"https://www.youtube.com/watch?v={ident}"
    return ""


def _best_thumbnail(raw: Dict[str, Any]) -> str:
    """Prefer the widest JPEG/PNG (Qt always decodes those); fall back to the `thumbnail` field."""
    best, best_width = "", -1
    for thumb in raw.get("thumbnails") or []:
        url = str(thumb.get("url") or "")
        if not url.startswith(("http://", "https://")):
            continue
        path = url.split("?")[0].lower()
        if not path.endswith((".jpg", ".jpeg", ".png")):
            continue
        width = _int(thumb.get("width")) or _int(thumb.get("preference")) or 0
        if width > best_width:
            best, best_width = url, width
    return best or str(raw.get("thumbnail") or "")


class YtdlpEngine(BaseEngine):
    name = "yt-dlp"
    supports_resume = True      # continues .part / fragment files left in the work folder

    def is_installed(self) -> bool:
        return module_available("yt_dlp")

    def supports(self, platform: str, quality: QualitySpec, cookies: Any) -> Tuple[bool, str]:
        if not self.is_installed():
            return False, "yt-dlp is not installed"
        return True, ""

    # ------------------------------------------------------------------ metadata
    def _base_opts(self, cookies: Any, log_cb: LogCallback) -> Dict[str, Any]:
        opts: Dict[str, Any] = {
            "quiet": True, "no_warnings": False, "noprogress": True, "logger": _YtdlpLogger(log_cb),
            "socket_timeout": 30, "retries": 0, "fragment_retries": 3, "extractor_retries": 0,
            "windowsfilenames": True, "ignoreconfig": True,
        }
        opts.update(cookie_manager.ytdlp_options(cookies))
        # yt-dlp enables only Deno by default; hand it whichever supported runtime is installed (Deno/Node/Bun).
        runtime = js_runtime_path()
        if runtime:
            opts["js_runtimes"] = {runtime[0]: {"path": runtime[1]}}
        return opts

    @staticmethod
    def _speed_options(opts: Dict[str, Any], request: Any, log_cb: LogCallback) -> None:
        """Faster transfers: parallel fragments, 10 MB range requests (avoids per-connection throttling) and,
        optionally, aria2c with several connections per file."""
        connections = max(1, min(16, int(getattr(request, "connections", 8))))
        opts["concurrent_fragment_downloads"] = connections
        limit = int(getattr(request, "rate_limit_kbps", 0) or 0)
        if limit > 0:
            opts["ratelimit"] = limit * 1024
        opts["file_access_retries"] = 15          # Windows: antivirus/indexers may hold a freshly written file
        opts["http_chunk_size"] = 10 * 1024 * 1024
        if getattr(request, "use_aria2c", False):
            if shutil.which("aria2c"):
                opts["external_downloader"] = {"default": "aria2c"}
                opts["external_downloader_args"] = {"aria2c": [
                    f"-x{connections}", f"-s{connections}", "-k1M", "--file-allocation=none", "--summary-interval=1"]
                    + ([f"--max-overall-download-limit={limit}K"] if limit > 0 else [])}
                log_cb("INFO", f"Using aria2c with {connections} connections.")
            else:
                log_cb("WARNING", "aria2c is not installed; using the built-in downloader instead.")

    def fetch_info(self, url: str, cookies: Any, noplaylist: bool, log_cb: LogCallback) -> Optional[MediaInfo]:
        try:
            import yt_dlp  # type: ignore
        except ImportError as exc:
            raise MissingDependencyError("yt-dlp is not installed. Run: pip install yt-dlp") from exc
        opts = self._base_opts(cookies, log_cb)
        opts.update({"extract_flat": "in_playlist", "noplaylist": noplaylist, "skip_download": True})
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                raw = ydl.extract_info(url, download=False)
        except Exception as exc:  # yt-dlp raises DownloadError/ExtractorError and others
            raise EngineError(str(exc), classify_error(exc).kind) from exc
        if not raw:
            return None
        return self._to_media_info(url, raw)

    @staticmethod
    def _to_media_info(url: str, raw: Dict[str, Any]) -> MediaInfo:
        info = MediaInfo(
            url=url, title=str(raw.get("title") or ""), id=str(raw.get("id") or ""),
            uploader=str(raw.get("uploader") or raw.get("channel") or ""),
            duration=_int(raw.get("duration")), upload_date=str(raw.get("upload_date") or ""),
            thumbnail=_best_thumbnail(raw),
        )
        if raw.get("_type") in ("playlist", "multi_video") or raw.get("entries") is not None:
            info.is_playlist = True
            index = 0
            for entry in raw.get("entries") or []:
                index += 1
                if not entry:
                    info.entries.append(PlaylistEntry(index, f"Unavailable item {index}", "", False))
                    continue
                title = str(entry.get("title") or f"Video {index}")
                entry_url = _entry_url(entry)
                available = bool(entry_url) and title.lower() not in _DELETED_TITLES
                info.entries.append(PlaylistEntry(index, title, entry_url, available, str(entry.get("id") or ""),
                                                  _best_thumbnail(entry), _int(entry.get("duration")),
                                                  _int(entry.get("filesize") or entry.get("filesize_approx"))))
            return info
        for fmt in raw.get("formats") or []:
            if not isinstance(fmt, dict) or fmt.get("format_note") == "storyboard" or fmt.get("ext") == "mhtml":
                continue
            info.formats.append(FormatInfo(
                format_id=str(fmt.get("format_id", "")), ext=str(fmt.get("ext") or ""),
                height=_effective_height(fmt), fps=fmt.get("fps"),
                vcodec=str(fmt.get("vcodec") or "none"), acodec=str(fmt.get("acodec") or "none"),
                filesize=_int(fmt.get("filesize") or fmt.get("filesize_approx")), abr=fmt.get("abr"),
            ))
        return info

    # ------------------------------------------------------------------ download
    def download(self, request: DownloadRequest, progress_cb: ProgressCallback,
                 log_cb: LogCallback, cancel: CancelToken) -> DownloadResult:
        try:
            import yt_dlp  # type: ignore
        except ImportError as exc:
            raise MissingDependencyError("yt-dlp is not installed. Run: pip install yt-dlp") from exc

        quality, ffmpeg = request.quality, request.ffmpeg
        ffmpeg_ok = ffmpeg.available
        state = {"stage": "downloading", "title": ""}

        def hook(data: Dict[str, Any]) -> None:
            cancel.raise_if_cancelled()
            status = data.get("status")
            if status == "downloading":
                total = _int(data.get("total_bytes") or data.get("total_bytes_estimate")) or 0
                done = _int(data.get("downloaded_bytes")) or 0
                percent = (done / total * 100.0) if total else -1.0
                progress_cb(ProgressInfo(
                    percent=min(percent, 100.0), speed=float(data.get("speed") or 0.0),
                    eta=_int(data.get("eta")), downloaded=done, total=total, stage="downloading",
                    engine=self.name, title=state["title"], filename=str(data.get("filename", "")),
                ))
            elif status == "finished":
                progress_cb(ProgressInfo(percent=100.0, stage="finished", engine=self.name, title=state["title"]))

        def pp_hook(data: Dict[str, Any]) -> None:
            cancel.raise_if_cancelled()
            if data.get("status") == "started":
                stage = "converting" if data.get("postprocessor") == "ExtractAudio" else "merging"
                progress_cb(ProgressInfo(percent=-1.0, stage=stage, engine=self.name, title=state["title"]))

        opts = self._base_opts(request.cookies, log_cb)
        opts.update({
            "format": ytdlp_format(quality, ffmpeg_ok),
            "format_sort": ytdlp_sort(quality),
            "outtmpl": str(request.work_dir / f"{TEMP_STEM}.%(ext)s"),
            "noplaylist": request.noplaylist,
            "progress_hooks": [hook], "postprocessor_hooks": [pp_hook],
            "continuedl": True, "writethumbnail": False,
        })
        self._speed_options(opts, request, log_cb)
        if ffmpeg_ok:
            opts["ffmpeg_location"] = ffmpeg.location
            if not quality.is_audio:
                opts["merge_output_format"] = quality.container
            elif quality.audio_format == "mp3":
                opts["postprocessors"] = [{
                    "key": "FFmpegExtractAudio", "preferredcodec": "mp3",
                    "preferredquality": str(quality.audio_bitrate or 0),
                }]
        elif quality.is_audio and quality.audio_format == "mp3":
            log_cb("WARNING", "FFmpeg not found: MP3 conversion skipped; the original audio format will be saved.")
        if not ffmpeg_ok and not quality.is_audio:
            log_cb("WARNING", "FFmpeg not found: downloading single-file streams only (quality may be limited).")

        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                # process=False returns the raw extraction (formats not yet selected), which is
                # yt-dlp's documented pattern for inspecting info before calling process_ie_result.
                raw = ydl.extract_info(request.url, download=False, process=False)
                if raw:
                    state["title"] = str(raw.get("title") or "")
                    self._check_resolution(raw, quality, log_cb)
                    cancel.raise_if_cancelled()
                    ydl.process_ie_result(raw, download=True)
                else:
                    ydl.download([request.url])
        except DownloadCancelled:
            raise
        except Exception as exc:
            if cancel.is_cancelled:
                raise DownloadCancelled() from exc
            if isinstance(exc, EngineError):
                raise
            if "requested format is not available" in str(exc).lower() and not ffmpeg_ok and not quality.is_audio:
                raise EngineError(
                    "No single-file stream exists for this video (YouTube serves separate video and audio "
                    "streams). Install FFmpeg to download it - see Settings > FFmpeg.", "unsupported") from exc
            raise EngineError(str(exc), classify_error(exc).kind) from exc

        output = find_output_file(request.work_dir)
        if output is not None and (request.work_dir / (output.name + ".part")).exists():
            output = None
        if output is None:
            raise EngineError("yt-dlp finished but produced no output file.", "corrupted")
        height = None
        if raw:
            heights = [h for h in (_effective_height(f) for f in raw.get("formats") or []) if h]
            height = _int(raw.get("height")) or (pick_height(heights, quality.max_height) if heights else None)
        return DownloadResult(
            file_path=output, engine=self.name, title=state["title"],
            resolution=f"{height}p" if height and not quality.is_audio else ("audio" if quality.is_audio else ""),
            container=output.suffix.lstrip("."), size=output.stat().st_size,
        )

    @staticmethod
    def _check_resolution(raw: Dict[str, Any], quality: QualitySpec, log_cb: LogCallback) -> None:
        """Refuse to exceed the requested maximum resolution (closest lower one is used if present)."""
        if quality.is_audio or not quality.max_height or raw.get("_type") == "playlist":
            return
        formats = [FormatInfo(format_id="x", height=_effective_height(f), vcodec=str(f.get("vcodec") or "none"))
                   for f in raw.get("formats") or [] if isinstance(f, dict)]
        heights = available_heights(formats)
        if not heights:
            return
        chosen = pick_height(heights, quality.max_height)
        if chosen is None:
            raise EngineError(
                f"No resolution at or below {quality.max_height}p is available (lowest is {min(heights)}p).",
                "unsupported",
            )
        if chosen < quality.max_height:
            log_cb("INFO", f"{quality.max_height}p is not available; using the closest lower resolution ({chosen}p).")
