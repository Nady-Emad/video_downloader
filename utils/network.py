"""Error classification and retry helpers shared by every engine."""
from __future__ import annotations

import errno
import socket
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

# Error kinds
INVALID_URL = "invalid_url"
UNSUPPORTED = "unsupported"
NETWORK = "network"
TIMEOUT = "timeout"
DNS = "dns"
PRIVATE = "private"
DELETED = "deleted"
RESTRICTED = "restricted"
AUTH = "auth"
DISK = "disk"
PERMISSION = "permission"
MISSING_DEP = "missing_dependency"
FFMPEG = "ffmpeg"
CORRUPTED = "corrupted"
CANCELLED = "cancelled"
FILE_LOCKED = "file_locked"
UNKNOWN = "unknown"

TRANSIENT_KINDS = {NETWORK, TIMEOUT, DNS, FILE_LOCKED}
# After these kinds no other engine can succeed, so the fallback chain stops.
STOP_CHAIN_KINDS = {INVALID_URL, PRIVATE, DELETED, DISK, PERMISSION, CANCELLED}


@dataclass(frozen=True)
class ErrorInfo:
    kind: str
    message: str  # readable text for the GUI

    @property
    def transient(self) -> bool:
        return self.kind in TRANSIENT_KINDS

    @property
    def stops_chain(self) -> bool:
        return self.kind in STOP_CHAIN_KINDS


# (substring, kind, readable message). First match wins; order matters.
_PATTERNS = [
    ("being used by another process", FILE_LOCKED,
     "A file is temporarily locked by another program (antivirus or indexer). Retrying."),
    ("winerror 32", FILE_LOCKED, "A file is temporarily locked by another program (antivirus or indexer). Retrying."),
    ("sign in to confirm", AUTH, "The site asks you to sign in. Configure browser cookies or a cookies file in Settings."),
    ("login required", AUTH, "Login is required. Configure browser cookies or a cookies file in Settings."),
    ("log in", AUTH, "Login is required. Configure browser cookies or a cookies file in Settings."),
    ("cookies", AUTH, "Authentication problem with the cookies. Refresh your cookies and try again."),
    ("members-only", AUTH, "This content is for members only (authentication required)."),
    ("private video", PRIVATE, "This video is private."),
    ("is private", PRIVATE, "This content is private."),
    ("video unavailable", DELETED, "This video is unavailable or has been removed."),
    ("has been removed", DELETED, "This video has been removed."),
    ("been deleted", DELETED, "This video has been deleted."),
    ("does not exist", DELETED, "This content does not exist."),
    ("not available in your country", RESTRICTED, "This content is geo-restricted."),
    ("geo", RESTRICTED, "This content may be geo-restricted."),
    ("age-restricted", RESTRICTED, "This content is age-restricted; sign in with cookies to access it."),
    ("confirm your age", RESTRICTED, "This content is age-restricted; sign in with cookies to access it."),
    ("drm", RESTRICTED, "This content is DRM-protected and cannot be downloaded."),
    ("unsupported url", UNSUPPORTED, "This website or URL is not supported by the engine."),
    ("no suitable extractor", UNSUPPORTED, "This website or URL is not supported by the engine."),
    ("no video formats found", UNSUPPORTED, "No downloadable video was found at this URL."),
    ("requested format is not available", UNSUPPORTED, "The requested quality or format is not available for this video."),
    ("invalid url", INVALID_URL, "The URL is invalid."),
    ("name or service not known", DNS, "DNS lookup failed. Check your internet connection."),
    ("getaddrinfo failed", DNS, "DNS lookup failed. Check your internet connection."),
    ("temporary failure in name resolution", DNS, "DNS lookup failed. Check your internet connection."),
    ("timed out", TIMEOUT, "The connection timed out."),
    ("timeout", TIMEOUT, "The connection timed out."),
    ("connection reset", NETWORK, "The connection was reset."),
    ("connection aborted", NETWORK, "The connection was interrupted."),
    ("connection refused", NETWORK, "The connection was refused."),
    ("network is unreachable", NETWORK, "The network is unreachable."),
    ("remote end closed", NETWORK, "The server closed the connection."),
    ("incomplete read", NETWORK, "The download was interrupted."),
    ("http error 429", NETWORK, "Too many requests (HTTP 429). Retrying later may help."),
    ("http error 5", NETWORK, "The server reported an error. Retrying may help."),
    ("http error 403", RESTRICTED, "Access denied by the server (HTTP 403)."),
    ("http error 404", DELETED, "The content was not found (HTTP 404)."),
    ("not enough disk space", DISK, "There is not enough disk space."),
    ("no space left", DISK, "There is not enough disk space."),
    ("permission denied", PERMISSION, "Permission denied when writing files."),
    ("ffmpeg", FFMPEG, "FFmpeg is required or failed. Check the FFmpeg path in Settings."),
    ("ffprobe", FFMPEG, "FFmpeg/ffprobe is required or failed. Check the FFmpeg path in Settings."),
]


def classify_error(error: object) -> ErrorInfo:
    """Map an exception or message to a stable kind and a readable message."""
    # Imported lazily to avoid a circular import with base_engine.
    from engines.base_engine import DownloadCancelled, EngineError, MissingDependencyError

    if isinstance(error, DownloadCancelled):
        return ErrorInfo(CANCELLED, "Cancelled by user.")
    if isinstance(error, MissingDependencyError):
        return ErrorInfo(MISSING_DEP, str(error))
    if isinstance(error, EngineError) and error.kind:
        return ErrorInfo(error.kind, str(error))
    if isinstance(error, PermissionError):
        return ErrorInfo(PERMISSION, "Permission denied when writing files.")
    if isinstance(error, OSError) and getattr(error, "errno", None) == errno.ENOSPC:
        return ErrorInfo(DISK, "There is not enough disk space.")
    if isinstance(error, socket.gaierror):
        return ErrorInfo(DNS, "DNS lookup failed. Check your internet connection.")
    if isinstance(error, (socket.timeout, TimeoutError)):
        return ErrorInfo(TIMEOUT, "The connection timed out.")
    if isinstance(error, ConnectionError):
        return ErrorInfo(NETWORK, "The connection was interrupted.")

    text = str(error)
    lowered = text.lower()
    for needle, kind, message in _PATTERNS:
        if needle in lowered:
            return ErrorInfo(kind, message)
    short = text.strip().splitlines()[0] if text.strip() else error.__class__.__name__
    return ErrorInfo(UNKNOWN, short[:300])


def interruptible_sleep(seconds: float, cancel_event: Optional[threading.Event]) -> bool:
    """Sleep up to `seconds`; return False immediately if the cancel event gets set."""
    if cancel_event is None:
        threading.Event().wait(seconds)
        return True
    return not cancel_event.wait(seconds)


# ----------------------------------------------------------------------------- thumbnails / connectivity
_IMAGE_EXT = {"image/jpeg": ".jpg", "image/jpg": ".jpg", "image/png": ".png", "image/webp": ".webp",
              "image/gif": ".gif"}
MAX_THUMBNAIL_BYTES = 4 * 1024 * 1024
_MAX_CACHED_THUMBNAILS = 400


def fetch_image(url: str, cache_dir: "Path", timeout: float = 15.0) -> "Optional[Path]":
    """Download an image to the cache (once) and return its path, or None on any problem.

    Only http(s) URLs are fetched, the response must be an image no larger than 4 MB, and the file is
    written atomically. Safe to call from a worker thread; never raises.
    """
    import hashlib
    import os
    import urllib.request
    from pathlib import Path as _Path
    from urllib.parse import urlparse

    try:
        if urlparse(url).scheme not in ("http", "https"):
            return None
        cache = _Path(cache_dir)
        cache.mkdir(parents=True, exist_ok=True)
        name = hashlib.sha1(url.encode("utf-8")).hexdigest()
        for existing in cache.glob(name + ".*"):
            if existing.is_file() and existing.stat().st_size > 0:
                return existing
        request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 VideoDownloader"})
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - scheme checked above
            content_type = response.headers.get_content_type()
            if not content_type.startswith("image/"):
                return None
            data = response.read(MAX_THUMBNAIL_BYTES + 1)
        if not data or len(data) > MAX_THUMBNAIL_BYTES:
            return None
        target = cache / (name + _IMAGE_EXT.get(content_type, ".img"))
        tmp = cache / (name + ".tmp")
        tmp.write_bytes(data)
        os.replace(tmp, target)
        _prune_cache(cache)
        return target
    except Exception:  # network, SSL, HTTP and disk problems all mean "no thumbnail"
        return None


def _prune_cache(cache: "Path") -> None:
    try:
        files = sorted((p for p in cache.iterdir() if p.is_file()), key=lambda p: p.stat().st_mtime)
        for old in files[:-_MAX_CACHED_THUMBNAILS]:
            old.unlink(missing_ok=True)
    except OSError:
        pass


def network_available(timeout: float = 4.0) -> bool:
    """True if a TCP connection to a public resolver succeeds (used to detect that internet is back)."""
    for host, port in (("1.1.1.1", 443), ("8.8.8.8", 53)):
        try:
            socket.create_connection((host, port), timeout=timeout).close()
            return True
        except OSError:
            continue
    return False
