"""Tertiary engine: gallery-dl (non-YouTube platforms), run as a subprocess without a shell."""
from __future__ import annotations

from collections import deque
from pathlib import Path
from typing import Any, Deque, Optional, Tuple

from core import cookie_manager
from core.platform_detector import FACEBOOK, OTHER, INSTAGRAM, SOUNDCLOUD, TIKTOK, TWITTER, YOUTUBE
from core.quality_manager import QualitySpec
from engines.base_engine import (
    TEMP_STEM, BaseEngine, CancelToken, DownloadRequest, DownloadResult, EngineError, EngineUnsupported,
    LogCallback, MissingDependencyError, ProgressCallback, ProgressInfo, run_subprocess,
)
from utils.file_manager import MEDIA_SUFFIXES
from utils.network import classify_error
from utils.system_info import module_available, module_command

# Platforms gallery-dl has extractors for among those this application detects.
SUPPORTED_PLATFORMS = {TWITTER, INSTAGRAM, TIKTOK, FACEBOOK, SOUNDCLOUD}
_AUDIO_SUFFIXES = {".mp3", ".m4a", ".opus", ".ogg", ".aac", ".wav", ".flac"}
_VIDEO_SUFFIXES = {".mp4", ".mkv", ".webm", ".mov", ".m4v", ".avi", ".flv", ".ts", ".3gp"}


def pick_media_file(directory: Path, prefer_audio: bool) -> Optional[Path]:
    """Choose the downloaded file to keep: video/audio first, then images; largest wins."""
    files = [p for p in Path(directory).iterdir()
             if p.is_file() and p.suffix.lower() in MEDIA_SUFFIXES and not p.name.startswith(TEMP_STEM + ".private")]
    if not files:
        return None
    primary = _AUDIO_SUFFIXES if prefer_audio else _VIDEO_SUFFIXES
    preferred = [p for p in files if p.suffix.lower() in primary] or \
                [p for p in files if p.suffix.lower() in _VIDEO_SUFFIXES | _AUDIO_SUFFIXES] or files
    return max(preferred, key=lambda p: p.stat().st_size)


class GalleryDlEngine(BaseEngine):
    name = "gallery-dl"

    def is_installed(self) -> bool:
        return module_available("gallery_dl")

    def supports(self, platform: str, quality: QualitySpec, cookies: Any) -> Tuple[bool, str]:
        if platform == YOUTUBE:
            return False, "gallery-dl is not used for YouTube"
        if platform not in SUPPORTED_PLATFORMS and platform != OTHER:
            return False, f"gallery-dl has no extractor for {platform}"
        if quality.is_audio and platform != SOUNDCLOUD:
            return False, "gallery-dl cannot extract audio from this platform"
        if not self.is_installed():
            return False, "gallery-dl is not installed"
        return True, ""

    def download(self, request: DownloadRequest, progress_cb: ProgressCallback,
                 log_cb: LogCallback, cancel: CancelToken) -> DownloadResult:
        if not self.is_installed():
            raise MissingDependencyError("gallery-dl is not installed. Run: pip install gallery-dl")
        quality = request.quality
        if quality.max_height or quality.container != "mp4" or quality.audio_format == "mp3" and quality.is_audio:
            log_cb("WARNING", "gallery-dl cannot apply resolution/format/bitrate settings; "
                              "the original media will be saved.")
        tail: Deque[str] = deque(maxlen=10)

        def on_line(line: str) -> None:
            tail.append(line)
            progress_cb(ProgressInfo(percent=-1.0, stage="downloading", engine=self.name, filename=line[:200]))

        args = module_command("gallery_dl") + [
            "-D", str(request.work_dir), "-f", "{filename}.{extension}", "--no-part",
            *cookie_manager.gallery_dl_args(request.cookies), request.url,
        ]
        code = run_subprocess(args, request.work_dir, on_line, cancel)
        output = pick_media_file(request.work_dir, quality.is_audio)
        if code != 0 and output is None:
            detail = " | ".join(tail)
            raise EngineError(f"gallery-dl failed (exit {code}): {detail}", classify_error(detail).kind)
        if output is None:
            raise EngineUnsupported("gallery-dl found no downloadable media at this URL.", "unsupported")

        final = request.work_dir / f"{TEMP_STEM}{output.suffix.lower()}"
        output.rename(final)
        return DownloadResult(
            file_path=final, engine=self.name, title=output.stem, resolution="original",
            container=final.suffix.lstrip("."), size=final.stat().st_size,
        )
