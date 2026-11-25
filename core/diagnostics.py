"""Error center data and diagnostic report (Qt-free). Everything that leaves the app goes through redact()."""
from __future__ import annotations

import platform
import sys
import threading
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Deque, Dict, List, Mapping, Optional

import app_info
from utils import network
from utils.logger import redact

CATEGORIES = {
    network.MISSING_DEP: "Missing dependency", network.FFMPEG: "FFmpeg",
    network.AUTH: "Authentication", network.NETWORK: "Network", network.TIMEOUT: "Network",
    network.DNS: "Network", network.DISK: "Storage", network.PERMISSION: "Storage / permissions",
    network.FILE_LOCKED: "Storage / permissions", network.EXTRACTOR: "Site changed (update engines)", network.CORRUPTED: "Media validation",
    network.PRIVATE: "Source (private)", network.DELETED: "Source (unavailable)",
    network.RESTRICTED: "Source (restricted)", network.INVALID_URL: "Invalid link",
}
HINTS = {
    "Site changed (update engines)": "Open Diagnostics > Dependencies and press Update download engines, then retry.",
    "Missing dependency": "Install the missing package (pip install -r requirements.txt) and restart.",
    "FFmpeg": "Install FFmpeg (winget install Gyan.FFmpeg) or set its path in Settings.",
    "Authentication": "Configure browser cookies or a cookies file in Settings, then retry.",
    "Network": "Check the connection; the task resumes automatically when the network is back.",
    "Storage": "Free disk space or choose another download folder.",
    "Storage / permissions": "Choose a folder you can write to; close programs that lock the file; check antivirus.",
    "Media validation": "The downloaded file was not a valid video. Retry; Compatibility mode may help.",
}
SENSITIVE_SETTINGS = {"cookie_file", "update_url", "window_geometry"}


@dataclass
class ErrorEntry:
    title: str
    url: str
    message: str
    category: str
    kind: str = ""
    details: str = ""
    time: str = field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

    @property
    def hint(self) -> str:
        return HINTS.get(self.category, "")

    def as_text(self, with_details: bool = True) -> str:
        lines = [f"[{self.time}] {self.category}: {self.message}", f"Item: {self.title or '-'}", f"Link: {self.url}"]
        if self.hint:
            lines.append(f"Suggestion: {self.hint}")
        if with_details and self.details:
            lines.append("Technical details:\n" + self.details)
        return redact("\n".join(lines))


def categorize(message: str) -> network.ErrorInfo:
    return network.classify_error(RuntimeError(message))


class ErrorStore:
    """Thread-safe, bounded list of recent errors shown in the Error Center."""

    def __init__(self, limit: int = 200) -> None:
        self._items: Deque[ErrorEntry] = deque(maxlen=limit)
        self._lock = threading.Lock()

    def add(self, title: str, url: str, message: str, details: str = "") -> ErrorEntry:
        info = categorize(message)
        entry = ErrorEntry(title=title, url=redact(url), message=redact(message),
                           category=CATEGORIES.get(info.kind, "Other"), kind=info.kind, details=redact(details))
        with self._lock:
            self._items.appendleft(entry)
        return entry

    def items(self) -> List[ErrorEntry]:
        with self._lock:
            return list(self._items)

    def clear(self) -> None:
        with self._lock:
            self._items.clear()


def tail(path: Path, lines: int = 200, max_bytes: int = 200_000) -> str:
    try:
        with open(path, "rb") as handle:
            handle.seek(0, 2)
            size = handle.tell()
            handle.seek(max(0, size - max_bytes))
            data = handle.read().decode("utf-8", "replace")
    except OSError:
        return ""
    return "\n".join(data.splitlines()[-lines:])


def build_report(settings: Mapping[str, Any], dependencies: Mapping[str, Any], errors: List[ErrorEntry],
                 log_file: Optional[Path] = None, extra: Optional[Dict[str, str]] = None) -> str:
    """Plain-text report safe to share: settings with secrets removed, redacted errors and log tail."""
    out = [f"{app_info.APP_NAME} {app_info.VERSION} - diagnostic report",
           f"Created: {datetime.now().isoformat(timespec='seconds')}",
           f"System: {platform.platform()}  Python {sys.version.split()[0]}", "", "== Dependencies =="]
    for name, value in dependencies.items():
        out.append(f"{name}: {value if not isinstance(value, bool) else ('found' if value else 'MISSING')}")
    for key, value in (extra or {}).items():
        out.append(f"{key}: {value}")
    out += ["", "== Settings =="]
    for key in sorted(settings):
        value = "[hidden]" if key in SENSITIVE_SETTINGS and settings[key] else settings[key]
        out.append(f"{key} = {value}")
    out += ["", f"== Recent errors ({len(errors)}) =="]
    out += [e.as_text() + "\n" for e in errors[:50]] or ["(none)"]
    if log_file is not None:
        out += ["", "== Log (last lines) ==", tail(log_file)]
    return redact("\n".join(out))
