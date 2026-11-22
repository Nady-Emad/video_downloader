"""Test doubles shared by several test modules."""
from __future__ import annotations

from pathlib import Path
from typing import Callable, List, Optional, Tuple

from core.cookie_manager import CookieConfig
from core.ffmpeg_manager import FfmpegManager
from core.quality_manager import QualitySpec
from engines.base_engine import (
    TEMP_STEM, BaseEngine, CancelToken, DownloadRequest, DownloadResult, EngineError, MediaInfo, ProgressInfo,
)


class FakeFfmpeg(FfmpegManager):
    """FfmpegManager that never touches the system; validation accepts any non-empty file."""

    def __init__(self, available: bool = True) -> None:
        self.custom_path = ""
        self.ffmpeg_path = "/fake/ffmpeg" if available else None
        self.ffprobe_path = None
        self.version = "fake 1.0" if available else ""

    def validate_output(self, path, expect_video, expect_audio=True):
        from core.ffmpeg_manager import ValidationResult
        path = Path(path)
        if not path.is_file() or path.stat().st_size == 0:
            return ValidationResult(False, "The output file is empty.")
        return ValidationResult(True, "ok")

    def validate_media(self, path):
        path = Path(path)
        if not path.is_file() or path.stat().st_size == 0:
            return False, "The output file is empty."
        return True, "ok"


class FakeEngine(BaseEngine):
    """Configurable engine. `behavior(request, attempt)` may write files or raise."""

    def __init__(self, name: str, behavior: Optional[Callable] = None, platforms: Optional[set] = None,
                 installed: bool = True, info: Optional[MediaInfo] = None, title: str = "Fake Title",
                 resume: bool = False) -> None:
        self.supports_resume = resume
        self.name = name
        self.calls: List[DownloadRequest] = []
        self._behavior = behavior
        self._platforms = platforms
        self._installed = installed
        self._info = info
        self._title = title
        self.work_dirs: List[Path] = []

    def is_installed(self) -> bool:
        return self._installed

    def supports(self, platform: str, quality: QualitySpec, cookies: CookieConfig) -> Tuple[bool, str]:
        if not self._installed:
            return False, f"{self.name} is not installed"
        if self._platforms is not None and platform not in self._platforms:
            return False, f"{self.name} does not support {platform}"
        return True, ""

    def fetch_info(self, url, cookies, noplaylist, log_cb):
        return self._info

    def download(self, request, progress_cb, log_cb, cancel):
        self.calls.append(request)
        self.work_dirs.append(request.work_dir)
        attempt = len(self.calls)
        produced = self._behavior(request, attempt, cancel) if self._behavior else None
        out = produced if isinstance(produced, Path) else request.work_dir / f"{TEMP_STEM}.mp4"
        if not out.exists():
            out.write_bytes(b"x" * 2048)
        progress_cb(ProgressInfo(percent=100.0, engine=self.name))
        return DownloadResult(file_path=out, engine=self.name, title=self._title, resolution="720p",
                              container="mp4", size=out.stat().st_size)


def fail_with(message: str, partial: bool = True):
    def _behavior(request, attempt, cancel):
        if partial:
            (request.work_dir / f"{TEMP_STEM}.mp4.part").write_bytes(b"partial")
        raise EngineError(message)
    return _behavior
