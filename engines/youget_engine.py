"""Additional fallback: you-get (only for platforms and media types it supports)."""
from __future__ import annotations

import re
from collections import deque
from typing import Any, Deque, Tuple

from core import cookie_manager
from core.platform_detector import FACEBOOK, OTHER, INSTAGRAM, SOUNDCLOUD, TIKTOK, TWITTER, VIMEO, YOUTUBE
from core.quality_manager import QualitySpec
from engines.base_engine import (
    TEMP_STEM, BaseEngine, CancelToken, DownloadRequest, DownloadResult, EngineError, EngineUnsupported,
    LogCallback, MissingDependencyError, ProgressCallback, ProgressInfo, find_output_file, run_subprocess,
)
from utils.network import classify_error
from utils.system_info import module_available, module_command

SUPPORTED_PLATFORMS = {YOUTUBE, VIMEO, TWITTER, INSTAGRAM, TIKTOK, SOUNDCLOUD, FACEBOOK}
_PERCENT = re.compile(r"(\d{1,3}(?:\.\d+)?)%")


class YouGetEngine(BaseEngine):
    name = "you-get"

    def is_installed(self) -> bool:
        return module_available("you_get")

    def supports(self, platform: str, quality: QualitySpec, cookies: Any) -> Tuple[bool, str]:
        if platform not in SUPPORTED_PLATFORMS and platform != OTHER:
            return False, f"you-get does not support {platform}"
        if quality.is_audio and platform != SOUNDCLOUD:
            return False, "you-get cannot extract audio-only from this platform"
        if quality.max_height and not quality.is_audio:
            return False, "you-get cannot enforce a maximum resolution"
        if not self.is_installed():
            return False, "you-get is not installed"
        return True, ""

    def download(self, request: DownloadRequest, progress_cb: ProgressCallback,
                 log_cb: LogCallback, cancel: CancelToken) -> DownloadResult:
        if not self.is_installed():
            raise MissingDependencyError("you-get is not installed. Run: pip install you-get")
        if request.cookies.mode == "browser":
            log_cb("WARNING", "you-get cannot read browser cookies; downloading without cookies.")
        tail: Deque[str] = deque(maxlen=10)

        def on_line(line: str) -> None:
            tail.append(line)
            match = _PERCENT.search(line)
            percent = min(float(match.group(1)), 100.0) if match else -1.0
            progress_cb(ProgressInfo(percent=percent, stage="downloading", engine=self.name))

        args = module_command("you_get") + [
            "-o", str(request.work_dir), "-O", TEMP_STEM, "--no-caption",
            *cookie_manager.youget_args(request.cookies), request.url,
        ]
        code = run_subprocess(args, request.work_dir, on_line, cancel)
        output = find_output_file(request.work_dir)
        if code != 0 or output is None:
            detail = " | ".join(tail)
            if output is None and code == 0:
                raise EngineUnsupported("you-get finished without producing a media file.", "unsupported")
            raise EngineError(f"you-get failed (exit {code}): {detail}", classify_error(detail).kind)
        if output.suffix.lower().lstrip(".") != request.quality.container and not request.quality.is_audio:
            log_cb("WARNING", f"you-get saved the media as {output.suffix}; no container conversion was applied.")
        return DownloadResult(
            file_path=output, engine=self.name, title="", resolution="best",
            container=output.suffix.lstrip("."), size=output.stat().st_size,
        )
