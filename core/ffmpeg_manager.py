"""FFmpeg detection and the small set of operations the application needs."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from collections import deque
from pathlib import Path
from dataclasses import dataclass, field
from typing import Deque, List, Optional, Tuple

from engines.base_engine import CancelToken, DownloadCancelled, EngineError, run_subprocess
from utils.logger import get_logger

log = get_logger("ffmpeg")

INSTALL_INSTRUCTIONS = """\
FFmpeg installation

Windows
  1. winget install --id Gyan.FFmpeg      (or: choco install ffmpeg)
  2. Or download a build from https://www.gyan.dev/ffmpeg/builds/ , unzip it, and either add its
     "bin" folder to PATH or select ffmpeg.exe in Settings -> FFmpeg path.
  3. Restart the application.

Linux
  Debian/Ubuntu: sudo apt update && sudo apt install ffmpeg
  Fedora:        sudo dnf install ffmpeg
  Arch:          sudo pacman -S ffmpeg

macOS
  brew install ffmpeg
"""


_AUDIO_ONLY_EXT = {".m4a", ".mp3", ".aac", ".opus", ".ogg", ".oga", ".wav", ".flac", ".weba"}
_COMPAT_PIX_FMTS = {"yuv420p", "yuvj420p"}


@dataclass
class MediaStreams:
    """What ffprobe found in a media file (cover art is not counted as video)."""
    has_video: bool = False
    has_audio: bool = False
    video_codec: str = ""
    audio_codec: str = ""
    pix_fmt: str = ""
    width: int = 0
    height: int = 0
    duration: float = 0.0
    format_name: str = ""

    def describe(self) -> str:
        parts = []
        if self.has_video:
            parts.append(f"video {self.video_codec} {self.width}x{self.height}")
        if self.has_audio:
            parts.append(f"audio {self.audio_codec}")
        mins, secs = divmod(int(self.duration), 60)
        parts.append(f"{mins}:{secs:02d}")
        return ", ".join(parts)


@dataclass
class ValidationResult:
    ok: bool
    message: str
    streams: Optional[MediaStreams] = None
    warnings: List[str] = field(default_factory=list)


class FfmpegManager:
    def __init__(self, custom_path: str = "") -> None:
        self.custom_path = (custom_path or "").strip()
        self.ffmpeg_path: Optional[str] = None
        self.ffprobe_path: Optional[str] = None
        self.version: str = ""
        self.refresh()

    # ----- detection -------------------------------------------------------------------------
    def refresh(self) -> None:
        self.ffmpeg_path = self._locate("ffmpeg")
        self.ffprobe_path = self._locate("ffprobe")
        self.version = self._read_version() if self.ffmpeg_path else ""

    def _locate(self, tool: str) -> Optional[str]:
        exe = tool + (".exe" if os.name == "nt" else "")
        if self.custom_path:
            custom = Path(self.custom_path).expanduser()
            if custom.is_dir():
                candidate = custom / exe
                if candidate.is_file():
                    return str(candidate)
            elif custom.is_file():
                if tool == "ffmpeg":
                    return str(custom)
                sibling = custom.with_name(exe)
                if sibling.is_file():
                    return str(sibling)
        found = shutil.which(tool)
        if found:
            return found
        if getattr(sys, "frozen", False):          # packaged build: ffmpeg placed next to the executable
            bundled = Path(sys.executable).parent / exe
            if bundled.is_file():
                return str(bundled)
        return None

    @staticmethod
    def _cache_file() -> Path:
        from utils import system_info
        return system_info.data_dir() / "cache" / "ffmpeg_version.json"

    def _read_version(self) -> str:
        """`ffmpeg -version` output. Cached by path + size + modification time so normal startups do not have to
        start ffmpeg at all (process creation is slow on Windows, especially with antivirus scanning)."""
        try:
            stat = Path(self.ffmpeg_path).stat()
            stamp = f"{self.ffmpeg_path}|{stat.st_size}|{stat.st_mtime_ns}"
        except OSError:
            return ""
        try:
            cached = json.loads(self._cache_file().read_text(encoding="utf-8"))
            if cached.get("stamp") == stamp and cached.get("version"):
                return str(cached["version"])
        except (OSError, ValueError):
            pass
        version = self._probe_version()
        if version:
            try:
                target = self._cache_file()
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(json.dumps({"stamp": stamp, "version": version}), encoding="utf-8")
            except OSError:
                pass
        return version

    def _probe_version(self) -> str:
        try:
            out = subprocess.run(  # noqa: S603 - argument list
                [self.ffmpeg_path, "-version"], capture_output=True, text=True, timeout=15,
                encoding="utf-8", errors="replace",
            )
            return out.stdout.splitlines()[0] if out.returncode == 0 and out.stdout else ""
        except (OSError, subprocess.SubprocessError):
            return ""

    @property
    def available(self) -> bool:
        return bool(self.ffmpeg_path and self.version)

    @property
    def location(self) -> Optional[str]:
        """Value for yt-dlp's `ffmpeg_location` option."""
        return self.ffmpeg_path if self.available else None

    def status_text(self) -> str:
        if self.available:
            return f"FFmpeg found: {self.version}"
        return "FFmpeg not found. Merging and MP3 conversion are disabled."

    def limitations(self) -> List[str]:
        if self.available:
            return []
        return [
            "Separate video and audio streams cannot be merged, so high resolutions (above the best "
            "single-file stream, often 360p–720p) are unavailable.",
            "MP3 conversion and audio bitrate selection are disabled; audio is saved in its original format.",
            "MKV/WEBM remuxing is unavailable.",
        ]

    # ----- operations ------------------------------------------------------------------------
    def _run(self, args: List[str], out_dir: Path, cancel: Optional[CancelToken], timeout: float = 7200) -> None:
        if not self.available:
            raise EngineError("FFmpeg is not available.", "ffmpeg")
        tail: Deque[str] = deque(maxlen=8)
        full = [self.ffmpeg_path, "-nostdin", "-y", "-hide_banner", "-loglevel", "error", *args]
        try:
            code = run_subprocess(full, out_dir, tail.append, cancel or CancelToken(), timeout=timeout)
        except DownloadCancelled:
            raise
        except OSError as exc:
            raise EngineError(f"FFmpeg could not be started: {exc}", "ffmpeg") from exc
        if code != 0:
            detail = " | ".join(tail) or f"exit code {code}"
            raise EngineError(f"FFmpeg failed: {detail}", "ffmpeg")

    def merge(self, video: Path, audio: Path, output: Path, cancel: Optional[CancelToken] = None) -> Path:
        """Mux separate streams without re-encoding. Container is chosen by output's extension."""
        self._run(["-i", str(video), "-i", str(audio), "-map", "0:v:0", "-map", "1:a:0",
                   "-c", "copy", str(output)], output.parent, cancel)
        self._require_output(output)
        return output

    def remux(self, source: Path, output: Path, cancel: Optional[CancelToken] = None) -> Path:
        self._run(["-i", str(source), "-map", "0", "-c", "copy", str(output)], output.parent, cancel)
        self._require_output(output)
        return output

    def convert_to_mp3(self, source: Path, output: Path, bitrate_kbps: int = 0,
                       cancel: Optional[CancelToken] = None) -> Path:
        """Convert audio to MP3. bitrate 0 => best VBR quality (-q:a 0)."""
        quality = ["-b:a", f"{int(bitrate_kbps)}k"] if bitrate_kbps else ["-q:a", "0"]
        self._run(["-i", str(source), "-vn", "-map", "0:a:0", "-codec:a", "libmp3lame", *quality,
                   str(output)], output.parent, cancel)
        self._require_output(output)
        return output

    @staticmethod
    def _require_output(path: Path) -> None:
        if not path.is_file() or path.stat().st_size == 0:
            raise EngineError("FFmpeg produced no output file.", "ffmpeg")

    # ----- validation ------------------------------------------------------------------------
    def probe(self, path: Path) -> Optional[dict]:
        if not self.ffprobe_path:
            return None
        try:
            out = subprocess.run(  # noqa: S603 - argument list
                [self.ffprobe_path, "-v", "error", "-show_entries", "format=duration,size:stream=codec_type",
                 "-of", "json", str(path)],
                capture_output=True, text=True, timeout=60, encoding="utf-8", errors="replace",
            )
            if out.returncode != 0:
                return {"error": out.stderr.strip()[:200] or "ffprobe failed"}
            return json.loads(out.stdout or "{}")
        except (OSError, subprocess.SubprocessError, ValueError):
            return None

    def inspect(self, path: Path) -> Optional[MediaStreams]:
        """Stream details via ffprobe, or None when ffprobe is unavailable / cannot read the file."""
        if not self.ffprobe_path:
            return None
        try:
            out = subprocess.run(  # noqa: S603 - argument list
                [self.ffprobe_path, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
                capture_output=True, text=True, timeout=60, encoding="utf-8", errors="replace",
            )
            if out.returncode != 0:
                return None
            data = json.loads(out.stdout or "{}")
        except (OSError, subprocess.SubprocessError, ValueError):
            return None
        info = MediaStreams(format_name=str(data.get("format", {}).get("format_name", "")))
        try:
            info.duration = float(data.get("format", {}).get("duration") or 0)
        except (TypeError, ValueError):
            info.duration = 0.0
        for stream in data.get("streams", []):
            kind = stream.get("codec_type")
            if kind == "video" and not stream.get("disposition", {}).get("attached_pic"):
                if not info.has_video:
                    info.has_video = True
                    info.video_codec = str(stream.get("codec_name", ""))
                    info.pix_fmt = str(stream.get("pix_fmt", ""))
                    info.width = int(stream.get("width") or 0)
                    info.height = int(stream.get("height") or 0)
            elif kind == "audio" and not info.has_audio:
                info.has_audio = True
                info.audio_codec = str(stream.get("codec_name", ""))
        return info

    def validate_output(self, path: Path, expect_video: bool, expect_audio: bool = True) -> ValidationResult:
        """Validate a finished download. A video download that has no video stream (audio only)
        is NEVER accepted, whichever engine produced it."""
        path = Path(path)
        if not path.is_file():
            return ValidationResult(False, "The output file does not exist.")
        if path.stat().st_size <= 0:
            return ValidationResult(False, "The output file is empty.")
        streams = self.inspect(path)
        if streams is None:
            if self.ffprobe_path:      # ffprobe exists but could not read the file
                return ValidationResult(False, "The file could not be read as media (corrupted download).")
            if expect_video and path.suffix.lower() in _AUDIO_ONLY_EXT:
                return ValidationResult(False, "The file is audio only (no video stream).")
            return ValidationResult(True, "Basic check passed (ffprobe unavailable, streams not verified).",
                                    warnings=["ffprobe not found: video/audio streams could not be verified."])
        warnings: List[str] = []
        if expect_video:
            if not streams.has_video:
                return ValidationResult(False, "The downloaded file contains no video stream (audio only).", streams)
            if not streams.video_codec:
                return ValidationResult(False, "The video stream has no valid codec.", streams)
            if streams.width <= 0 or streams.height <= 0:
                return ValidationResult(False, "The video stream has invalid dimensions.", streams)
            if expect_audio and not streams.has_audio:
                warnings.append("The file has no audio stream (the source may be silent).")
        else:
            if not streams.has_audio:
                return ValidationResult(False, "The downloaded file contains no audio stream.", streams)
        if streams.duration <= 0:
            return ValidationResult(False, "The file has an invalid duration.", streams)
        return ValidationResult(True, "Verified: " + streams.describe(), streams, warnings)

    @staticmethod
    def needs_compat_transcode(streams: MediaStreams) -> Tuple[bool, bool]:
        """(video needs re-encoding, audio needs re-encoding) for MP4 + H.264 + AAC compatibility."""
        video = streams.has_video and (streams.video_codec != "h264" or streams.pix_fmt not in _COMPAT_PIX_FMTS)
        audio = streams.has_audio and streams.audio_codec != "aac"
        return video, audio

    def make_compatible(self, source: Path, output: Path, streams: MediaStreams,
                        cancel: Optional[CancelToken] = None) -> Path:
        """Produce MP4/H.264/AAC from `source`, copying every stream that is already compatible."""
        video_tc, audio_tc = self.needs_compat_transcode(streams)
        args = ["-i", str(source), "-map", "0:v:0", "-map", "0:a:0?"]
        if video_tc:
            args += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p"]
        else:
            args += ["-c:v", "copy"]
        args += ["-c:a", "aac", "-b:a", "192k"] if audio_tc else ["-c:a", "copy"]
        args += ["-movflags", "+faststart", str(output)]
        self._run(args, output.parent, cancel)
        self._require_output(output)
        return output

    def to_jpeg(self, source: Path, output: Path, cancel: Optional[CancelToken] = None) -> Path:
        self._run(["-i", str(source), "-frames:v", "1", "-q:v", "2", str(output)], output.parent, cancel)
        self._require_output(output)
        return output

    def embed_cover(self, audio: Path, image: Path, output: Path, cancel: Optional[CancelToken] = None) -> Path:
        """Embed `image` (JPEG/PNG) as front-cover art into an MP3."""
        self._run(["-i", str(audio), "-i", str(image), "-map", "0:a", "-map", "1:v", "-c", "copy",
                   "-id3v2_version", "3", "-metadata:s:v", "title=Album cover",
                   "-metadata:s:v", "comment=Cover (front)", str(output)], output.parent, cancel)
        self._require_output(output)
        return output

    def validate_media(self, path: Path) -> Tuple[bool, str]:
        """Check existence, nonzero size and (when ffprobe exists) that the file is readable media."""
        path = Path(path)
        if not path.is_file():
            return False, "The output file does not exist."
        if path.stat().st_size <= 0:
            return False, "The output file is empty."
        data = self.probe(path)
        if data is None:
            return True, "Basic size check passed (ffprobe unavailable)."
        if "error" in data:
            return False, f"The file looks corrupted: {data['error']}"
        streams = data.get("streams") or []
        if not streams:
            return False, "The file contains no audio or video streams."
        return True, "Media file verified."
