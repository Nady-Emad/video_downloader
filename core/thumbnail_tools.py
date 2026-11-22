"""Post-download thumbnail handling: save a JPEG beside the video and embed cover art into MP3 files.
Runs in the worker thread. Failures here never fail the download; they are logged as warnings."""
from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from core.ffmpeg_manager import FfmpegManager
from utils import file_manager
from utils.network import fetch_image
from utils.system_info import cache_dir

Log = Callable[[str, str], None]
_IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp"}


def _as_jpeg(image: Path, ffmpeg: FfmpegManager, work: Path) -> Optional[Path]:
    """JPEG is the safest format for players and ID3 tags; convert PNG/WEBP when FFmpeg can."""
    header = image.read_bytes()[:3]
    if header == b"\xff\xd8\xff":
        return image
    if not ffmpeg.available:
        return None
    return ffmpeg.to_jpeg(image, work / "cover.jpg")


def process(settings: Dict[str, Any], ffmpeg: FfmpegManager, media_file: Path, thumbnail_url: str, log: Log,
            fetch: Callable[..., Optional[Path]] = fetch_image) -> None:
    """Apply the 'save thumbnails' and 'embed audio thumbnail' settings to a finished download."""
    want_save = bool(settings.get("save_thumbnails"))
    is_mp3 = media_file.suffix.lower() == ".mp3"
    want_embed = bool(settings.get("embed_audio_thumbnail")) and is_mp3
    if not thumbnail_url or not (want_save or want_embed) or not media_file.is_file():
        return
    try:
        image = fetch(thumbnail_url, cache_dir("thumbnails"))
    except Exception as exc:    # network trouble must not fail a finished download
        log("WARNING", f"Thumbnail could not be downloaded: {exc}")
        return
    if not image or not Path(image).is_file():
        log("WARNING", "Thumbnail could not be downloaded; the video itself is fine.")
        return
    image = Path(image)
    work = Path(tempfile.mkdtemp(prefix="vdl_thumb_", dir=str(media_file.parent)))
    try:
        jpeg = _as_jpeg(image, ffmpeg, work)
        if jpeg is None:
            log("WARNING", "Thumbnail format needs FFmpeg to convert; skipped.")
            return
        if want_save:
            target = file_manager.unique_path(media_file.with_suffix(".jpg"))   # never overwrite silently
            shutil.copyfile(jpeg, target)
            log("INFO", f"Thumbnail saved: {target.name}")
        if want_embed:
            if not ffmpeg.available:
                log("WARNING", "FFmpeg is needed to embed cover art in MP3 files; skipped.")
            else:
                out = work / "embedded.mp3"
                ffmpeg.embed_cover(media_file, jpeg, out)
                if out.is_file() and out.stat().st_size > 0:
                    os.replace(out, media_file)                                     # atomic replace
                    log("INFO", "Cover art embedded in the MP3.")
    except Exception as exc:
        log("WARNING", f"Thumbnail step failed (download kept): {exc}")
    finally:
        shutil.rmtree(work, ignore_errors=True)
