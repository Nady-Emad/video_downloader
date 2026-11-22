"""Persistent application settings stored as JSON (Qt-free so it can be unit tested)."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from utils.logger import get_logger
from utils.system_info import default_download_dir, settings_path
from utils.validators import validate_settings

log = get_logger("settings")

DEFAULTS: Dict[str, Any] = {
    "download_dir": str(default_download_dir()),
    "default_video_quality": "best",
    "default_container": "mp4",
    "default_audio_bitrate": 192,       # 0 means "best available audio"
    "ffmpeg_path": "",
    "cookie_mode": "none",              # none | browser | file
    "cookie_browser": "chrome",
    "cookie_file": "",
    "max_retries": 5,
    "retry_delay": 3,           # initial delay; doubles after every failed attempt
    "max_concurrent": 1,
    "auto_process_queue": True,
    "auto_resume_network": True,
    "download_connections": 8,          # parallel connections/fragments per download (1-16)
    "use_aria2c": False,                # use aria2c for multi-connection transfers when installed
    "preferred_codec": "auto",          # auto | h264 | vp9 | av1
    "compat_mode": True,                # MP4 + H.264 + AAC (re-encodes only when needed)
    "preserve_partials": True,          # keep partial files when a download is interrupted
    "save_thumbnails": False,           # save <title>.jpg beside the video
    "embed_audio_thumbnail": True,      # cover art inside MP3 files
    "auto_recover_queue": False,        # start recovered tasks automatically after a restart        # resume paused-by-network-loss downloads automatically
    "conflict_policy": "rename",        # rename | skip | overwrite
    "log_level": "info",
    "filename_template": "{title}",
    "playlist_filename_template": "{index} - {title}",
    "window_geometry": "",
    "quality_preset": "best",           # best | balanced | small | compatible | custom
    "speed_limit_kbps": 0,              # 0 = unlimited (per download)
    "schedule_enabled": False,          # only start queued downloads inside the time window
    "schedule_start": "00:00",
    "schedule_end": "06:00",
    "language": "en",                   # en | ar
    "theme": "dark",                    # dark | light
    "clipboard_monitor": False,         # offer to analyse links copied to the clipboard
    "tray_enabled": True,
    "close_to_tray": False,             # closing the window keeps downloads running in the tray
    "minimize_to_tray": False,
    "notifications": True,
    "check_updates": False,
    "update_url": "",                   # GitHub-releases style JSON endpoint (https)
    "first_run_done": False,
}


class Settings:
    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = Path(path) if path else settings_path()
        self._values: Dict[str, Any] = dict(DEFAULTS)
        self.load()

    # -- access ---------------------------------------------------------
    def get(self, key: str) -> Any:
        return self._values.get(key, DEFAULTS.get(key))

    def snapshot(self) -> Dict[str, Any]:
        """Copy of all current values (for reports and validation)."""
        return dict(self._values)

    def set(self, key: str, value: Any) -> None:
        if key not in DEFAULTS:
            raise KeyError(f"Unknown setting: {key}")
        self._values[key] = value

    def as_dict(self) -> Dict[str, Any]:
        return dict(self._values)

    # -- persistence ----------------------------------------------------
    def load(self) -> None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
        except (OSError, ValueError) as exc:
            log.warning("Could not read settings (%s); using defaults.", exc)
            return
        if not isinstance(raw, dict):
            return
        for key, value in raw.items():
            if key not in DEFAULTS:
                continue
            default = DEFAULTS[key]
            if isinstance(default, bool):
                valid = isinstance(value, bool)
            elif isinstance(default, int):
                valid = isinstance(value, int) and not isinstance(value, bool)
            else:
                valid = isinstance(value, type(default))
            if valid:
                self._values[key] = value

    def validate(self, values: Optional[Dict[str, Any]] = None) -> List[str]:
        return validate_settings(values if values is not None else self._values)

    def update(self, values: Dict[str, Any]) -> List[str]:
        """Validate then apply `values`; returns the list of errors (nothing is applied on error)."""
        merged = dict(self._values)
        merged.update({k: v for k, v in values.items() if k in DEFAULTS})
        errors = self.validate(merged)
        if errors:
            return errors
        self._values = merged
        self.save()
        return []

    def save(self) -> None:
        """Atomic write so a crash cannot leave a half-written settings file."""
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp_name = tempfile.mkstemp(dir=str(self.path.parent), suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(self._values, handle, indent=2, ensure_ascii=False)
            os.replace(tmp_name, self.path)
        except OSError as exc:
            log.error("Could not save settings: %s", exc)
