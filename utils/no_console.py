"""Stop child processes from opening console (CMD/terminal) windows on Windows.

The app is a GUI program, but every helper it starts (ffmpeg, ffprobe, gallery-dl, you-get, pip, explorer)
and every process started *inside* libraries (yt-dlp's ffmpeg/ffprobe calls, pytubefix, ...) would otherwise
flash a console window. `install()` wraps `subprocess.Popen.__init__` once, at startup, so all of them get
CREATE_NO_WINDOW and a hidden STARTUPINFO. Pipes, exit codes, timeouts and error handling are untouched.
"""
from __future__ import annotations

import subprocess
import sys
from typing import Any, Dict

CREATE_NO_WINDOW = 0x08000000
CREATE_NEW_CONSOLE = 0x00000010
DETACHED_PROCESS = 0x00000008
_STARTF_USESHOWWINDOW = 0x00000001
_SW_HIDE = 0

_installed = False


def patched_kwargs(kwargs: Dict[str, Any], hidden_startupinfo: Any = None) -> Dict[str, Any]:
    """Return kwargs with the no-window flags added. Pure function (testable on any OS)."""
    result = dict(kwargs)
    flags = int(result.get("creationflags") or 0)
    if not flags & (CREATE_NEW_CONSOLE | DETACHED_PROCESS):      # the caller explicitly asked for its own console
        result["creationflags"] = flags | CREATE_NO_WINDOW
    if result.get("startupinfo") is None and hidden_startupinfo is not None:
        result["startupinfo"] = hidden_startupinfo
    return result


def _hidden_startupinfo() -> Any:
    info = subprocess.STARTUPINFO()  # type: ignore[attr-defined]
    info.dwFlags |= _STARTF_USESHOWWINDOW
    info.wShowWindow = _SW_HIDE
    return info


def install() -> bool:
    """Patch subprocess.Popen on Windows. Safe to call repeatedly; does nothing elsewhere. Returns True if active."""
    global _installed
    if sys.platform != "win32":
        return False
    if _installed:
        return True
    original = subprocess.Popen.__init__

    def init(self: Any, *args: Any, **kwargs: Any) -> None:
        original(self, *args, **patched_kwargs(kwargs, _hidden_startupinfo()))

    subprocess.Popen.__init__ = init  # type: ignore[method-assign]
    _installed = True
    return True
