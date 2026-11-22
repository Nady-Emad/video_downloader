"""Input validation for URLs, filename templates and settings."""
from __future__ import annotations

import re
import string
from pathlib import Path
from typing import Any, Dict, List
from urllib.parse import urlparse

_URL_FINDER = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
ALLOWED_TEMPLATE_FIELDS = {"title", "index", "id", "uploader", "playlist", "date"}

VIDEO_FORMATS = ("mp4", "mkv", "webm")
CONFLICT_POLICIES = ("rename", "skip", "overwrite")
LOG_LEVELS = ("debug", "info", "warning", "error")
COOKIE_MODES = ("none", "browser", "file")
BROWSERS = ("chrome", "firefox", "edge", "brave", "opera")


def validate_url(url: str) -> str:
    """Return the cleaned URL or raise ValueError with a readable message."""
    cleaned = (url or "").strip()
    if not cleaned:
        raise ValueError("The URL is empty.")
    if any(ch in cleaned for ch in ("\n", "\r", "\t", "\x00")) or " " in cleaned:
        raise ValueError("The URL contains whitespace or control characters.")
    if len(cleaned) > 4096:
        raise ValueError("The URL is too long.")
    parsed = urlparse(cleaned)
    if parsed.scheme.lower() not in ("http", "https"):
        raise ValueError("Only http:// and https:// URLs are supported.")
    host = parsed.hostname
    if not host or "." not in host:
        raise ValueError("The URL has no valid host name.")
    return cleaned


def extract_urls(text: str) -> List[str]:
    """Find every http(s) URL in free text, preserving order and removing duplicates."""
    seen: List[str] = []
    for match in _URL_FINDER.findall(text or ""):
        url = match.rstrip(".,);]")
        if url not in seen:
            seen.append(url)
    return seen


def validate_filename_template(template: str) -> None:
    """Raise ValueError if the template uses unknown fields or invalid syntax."""
    if not template or not template.strip():
        raise ValueError("The filename template is empty.")
    try:
        fields = [f for _, f, _, _ in string.Formatter().parse(template) if f is not None]
    except ValueError as exc:
        raise ValueError(f"Invalid filename template: {exc}") from exc
    unknown = [f for f in fields if f.split(".")[0].split("[")[0] not in ALLOWED_TEMPLATE_FIELDS]
    if unknown:
        raise ValueError(
            f"Unknown field(s) in filename template: {', '.join(unknown)}. "
            f"Allowed: {', '.join(sorted(ALLOWED_TEMPLATE_FIELDS))}."
        )
    if "title" not in fields and "id" not in fields:
        raise ValueError("The filename template must contain {title} or {id}.")


def validate_settings(values: Dict[str, Any]) -> List[str]:
    """Return a list of readable problems (empty when the settings are valid)."""
    errors: List[str] = []

    def _int_in(key: str, label: str, low: int, high: int) -> None:
        try:
            number = int(values[key])
        except (KeyError, TypeError, ValueError):
            errors.append(f"{label} must be a whole number.")
            return
        if not low <= number <= high:
            errors.append(f"{label} must be between {low} and {high}.")

    download_dir = str(values.get("download_dir", "")).strip()
    if not download_dir:
        errors.append("The default download directory is required.")
    else:
        path = Path(download_dir).expanduser()
        if path.exists() and not path.is_dir():
            errors.append("The default download directory is not a folder.")

    if values.get("default_container") not in VIDEO_FORMATS:
        errors.append("Default video format must be MP4, MKV or WEBM.")
    if values.get("conflict_policy") not in CONFLICT_POLICIES:
        errors.append("Invalid filename conflict policy.")
    if str(values.get("log_level", "")).lower() not in LOG_LEVELS:
        errors.append("Invalid log verbosity.")
    if values.get("cookie_mode") not in COOKIE_MODES:
        errors.append("Invalid cookie mode.")
    if values.get("cookie_mode") == "browser" and values.get("cookie_browser") not in BROWSERS:
        errors.append("Select a supported browser for cookies.")
    if values.get("cookie_mode") == "file":
        cookie_file = str(values.get("cookie_file", "")).strip()
        if not cookie_file or not Path(cookie_file).expanduser().is_file():
            errors.append("The cookies file does not exist.")

    ffmpeg_path = str(values.get("ffmpeg_path", "")).strip()
    if ffmpeg_path and not Path(ffmpeg_path).expanduser().exists():
        errors.append("The FFmpeg path does not exist.")

    if str(values.get("preferred_codec", "auto")) not in ("auto", "h264", "vp9", "av1"):
        errors.append("Preferred codec must be auto, h264, vp9 or av1.")
    if values.get("language", "en") not in ("en", "ar"):
        errors.append("Language must be English or Arabic.")
    if values.get("theme", "dark") not in ("dark", "light"):
        errors.append("Theme must be dark or light.")
    if values.get("quality_preset", "best") not in ("best", "balanced", "small", "compatible", "custom"):
        errors.append("Invalid quality preset.")
    _int_in("speed_limit_kbps", "Speed limit", 0, 1_000_000)
    if values.get("schedule_enabled"):
        import re as _re
        for key, label in (("schedule_start", "Schedule start"), ("schedule_end", "Schedule end")):
            if not _re.match(r"^([01]?\d|2[0-3]):[0-5]\d$", str(values.get(key, "")).strip()):
                errors.append(f"{label} must be a time like 22:30.")
    update_url = str(values.get("update_url", "")).strip()
    if update_url and not update_url.startswith("https://"):
        errors.append("The update address must start with https://")
    _int_in("download_connections", "Download connections", 1, 16)
    _int_in("max_retries", "Maximum retry count", 0, 10)
    _int_in("retry_delay", "Retry delay (seconds)", 0, 600)
    _int_in("max_concurrent", "Maximum concurrent downloads", 1, 5)

    for key, label in (("filename_template", "Filename template"),
                       ("playlist_filename_template", "Playlist filename template")):
        try:
            validate_filename_template(str(values.get(key, "")))
        except ValueError as exc:
            errors.append(f"{label}: {exc}")
    return errors
