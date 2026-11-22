"""Secondary engine: pytubefix (YouTube only)."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, List, Tuple

from core.platform_detector import YOUTUBE
from core.quality_manager import QualitySpec, pick_height
from engines.base_engine import (
    TEMP_STEM, BaseEngine, CancelToken, DownloadCancelled, DownloadRequest, DownloadResult, EngineError, EngineUnsupported,
    LogCallback, MissingDependencyError, ProgressCallback, ProgressInfo,
)
from utils.network import classify_error
from utils.system_info import module_available


def _stream_height(stream: Any) -> int:
    match = re.match(r"(\d+)p", str(getattr(stream, "resolution", "") or ""))
    return int(match.group(1)) if match else 0


class PytubefixEngine(BaseEngine):
    name = "pytubefix"

    def is_installed(self) -> bool:
        return module_available("pytubefix")

    def supports(self, platform: str, quality: QualitySpec, cookies: Any) -> Tuple[bool, str]:
        if platform != YOUTUBE:
            return False, "pytubefix only supports YouTube"
        if not self.is_installed():
            return False, "pytubefix is not installed"
        return True, ""

    # ------------------------------------------------------------------ download
    def download(self, request: DownloadRequest, progress_cb: ProgressCallback,
                 log_cb: LogCallback, cancel: CancelToken) -> DownloadResult:
        try:
            from pytubefix import YouTube  # type: ignore
        except ImportError as exc:
            raise MissingDependencyError("pytubefix is not installed. Run: pip install pytubefix") from exc

        quality, ffmpeg = request.quality, request.ffmpeg
        if request.cookies.enabled:
            log_cb("WARNING", "pytubefix does not use cookie settings; downloading without cookies.")
        state: dict = {"title": "", "resolution": ""}

        def on_progress(stream: Any, chunk: bytes, remaining: int) -> None:
            cancel.raise_if_cancelled()  # raising here aborts the stream download
            total = int(getattr(stream, "filesize", 0) or 0)
            done = max(total - remaining, 0)
            progress_cb(ProgressInfo(
                percent=(done / total * 100.0) if total else -1.0, downloaded=done, total=total,
                stage="downloading", engine=self.name, title=state["title"],
            ))

        try:
            yt = YouTube(request.url, on_progress_callback=on_progress)
            state["title"] = str(yt.title or "")
            streams = yt.streams
            if quality.is_audio:
                output = self._download_audio(streams, request, log_cb, cancel)
            else:
                output = self._download_video(streams, request, log_cb, cancel, state)
        except (EngineError, DownloadCancelled):
            raise
        except Exception as exc:
            if cancel.is_cancelled:
                raise DownloadCancelled() from exc
            raise EngineError(str(exc), classify_error(exc).kind) from exc

        return DownloadResult(
            file_path=output, engine=self.name, title=state["title"],
            resolution="audio" if quality.is_audio else state.get("resolution", ""),
            container=output.suffix.lstrip("."), size=output.stat().st_size,
        )

    # ------------------------------------------------------------------ audio
    def _download_audio(self, streams: Any, request: DownloadRequest, log_cb: LogCallback,
                        cancel: CancelToken) -> Path:
        quality, ffmpeg = request.quality, request.ffmpeg
        audio = streams.filter(only_audio=True).order_by("abr").desc().first()
        if audio is None:
            raise EngineUnsupported("No audio stream is available.", "unsupported")
        ext = "m4a" if audio.subtype in ("mp4", "m4a") else audio.subtype
        raw = Path(audio.download(output_path=str(request.work_dir), filename=f"{TEMP_STEM}.raw.{ext}"))
        if quality.audio_format == "mp3" and ffmpeg.available:
            target = request.work_dir / f"{TEMP_STEM}.mp3"
            ffmpeg.convert_to_mp3(raw, target, quality.audio_bitrate, cancel)
            raw.unlink(missing_ok=True)
            return target
        if quality.audio_format == "mp3":
            log_cb("WARNING", "FFmpeg not found: MP3 conversion skipped; saving the original audio format.")
        final = request.work_dir / f"{TEMP_STEM}.{ext}"
        raw.rename(final)
        return final

    # ------------------------------------------------------------------ video
    def _download_video(self, streams: Any, request: DownloadRequest, log_cb: LogCallback,
                        cancel: CancelToken, state: dict) -> Path:
        quality, ffmpeg = request.quality, request.ffmpeg
        work = request.work_dir

        progressive = list(streams.filter(progressive=True, file_extension="mp4").order_by("resolution").desc())
        prog_heights = [h for h in (_stream_height(s) for s in progressive) if h]
        adaptive_video: List[Any] = []
        if ffmpeg.available:
            ext = "webm" if quality.container == "webm" else "mp4"
            adaptive_video = list(streams.filter(adaptive=True, only_video=True, file_extension=ext)
                                  .order_by("resolution").desc())
        adaptive_heights = [h for h in (_stream_height(s) for s in adaptive_video) if h]

        all_heights = sorted(set(prog_heights) | set(adaptive_heights), reverse=True)
        target = pick_height(all_heights, quality.max_height)
        if target is None and not all_heights and not ffmpeg.available:
            raise EngineUnsupported(
                "No single-file (progressive) stream is available and FFmpeg is not installed. "
                "Install FFmpeg to download this video.", "unsupported")
        if target is None:
            raise EngineUnsupported(
                f"No resolution at or below {quality.max_height}p is available.", "unsupported")
        if quality.max_height and target < quality.max_height:
            log_cb("INFO", f"{quality.max_height}p is not available; using {target}p.")
        state["resolution"] = f"{target}p"

        use_adaptive = bool(adaptive_video) and target in adaptive_heights and target not in prog_heights
        if not ffmpeg.available:
            log_cb("WARNING", "FFmpeg not found: only progressive streams (limited quality) can be downloaded.")

        if use_adaptive:
            video = next(s for s in adaptive_video if _stream_height(s) == target)
            ext = "webm" if quality.container == "webm" else "mp4"
            audio = (streams.filter(adaptive=True, only_audio=True, file_extension=ext).order_by("abr").desc().first()
                     or streams.filter(only_audio=True).order_by("abr").desc().first())
            if audio is None:
                raise EngineUnsupported("No audio stream is available to merge.", "unsupported")
            v_path = Path(video.download(output_path=str(work), filename=f"{TEMP_STEM}.video.{video.subtype}"))
            a_ext = "m4a" if audio.subtype in ("mp4", "m4a") else audio.subtype
            a_path = Path(audio.download(output_path=str(work), filename=f"{TEMP_STEM}.audio.{a_ext}"))
            target_path = work / f"{TEMP_STEM}.{quality.container}"
            ffmpeg.merge(v_path, a_path, target_path, cancel)
            v_path.unlink(missing_ok=True)
            a_path.unlink(missing_ok=True)
            return target_path

        stream = next((s for s in progressive if _stream_height(s) == target), None)
        if stream is None:
            raise EngineUnsupported("No suitable stream was found.", "unsupported")
        path = Path(stream.download(output_path=str(work), filename=f"{TEMP_STEM}.mp4"))
        if quality.container == "mkv" and ffmpeg.available:
            mkv = work / f"{TEMP_STEM}.mkv"
            ffmpeg.remux(path, mkv, cancel)
            path.unlink(missing_ok=True)
            return mkv
        if quality.container != "mp4":
            log_cb("WARNING", f"{quality.container.upper()} requested but this stream can only be saved as MP4 "
                              "(no conversion available); saving as MP4.")
        return path
