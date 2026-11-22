"""System helpers: application directories, dependency status, safe opening of files."""
from __future__ import annotations

import importlib.util
import os
import platform
import subprocess
import sys
from pathlib import Path
from typing import Dict, List

APP_NAME = "VideoDownloader"

# Extensions that may be opened from the History tab. Anything else (e.g. .exe, .bat, .sh)
# is refused so the application never launches downloaded executables.
SAFE_OPEN_EXTENSIONS = {
    ".mp4", ".mkv", ".webm", ".mov", ".avi", ".flv", ".m4v", ".ts", ".3gp",
    ".mp3", ".m4a", ".aac", ".ogg", ".opus", ".wav", ".flac", ".weba",
    ".jpg", ".jpeg", ".png", ".gif", ".webp",
}

# Marker argument used so a frozen (PyInstaller) build can run gallery-dl / you-get as a module.
RUN_MODULE_FLAG = "--vdl-run-module"


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def resource_root() -> Path:
    """Folder that contains bundled read-only resources (assets)."""
    if is_frozen():
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent.parent


def user_data_dir() -> Path:
    system = platform.system()
    if system == "Windows":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    elif system == "Darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return base / APP_NAME


def data_dir() -> Path:
    """Writable data folder. Project folder when running from source, user folder when frozen."""
    base = user_data_dir() if is_frozen() else Path(__file__).resolve().parent.parent
    base.mkdir(parents=True, exist_ok=True)
    return base


def database_path() -> Path:
    path = data_dir() / "database"
    path.mkdir(parents=True, exist_ok=True)
    return path / "download_history.db"


def log_path() -> Path:
    path = data_dir() / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path / "application.log"


def cache_dir(name: str) -> Path:
    path = data_dir() / "cache" / name
    path.mkdir(parents=True, exist_ok=True)
    return path


def settings_path() -> Path:
    path = data_dir() / "config"
    path.mkdir(parents=True, exist_ok=True)
    return path / "settings.json"


def default_download_dir() -> Path:
    return Path.home() / "Downloads" / "VideoDownloader"


def module_available(module: str) -> bool:
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


def dependency_status() -> Dict[str, bool]:
    return {
        "PyQt6": module_available("PyQt6"),
        "yt-dlp": module_available("yt_dlp"),
        "pytubefix": module_available("pytubefix"),
        "gallery-dl": module_available("gallery_dl"),
        "you-get": module_available("you_get"),
    }


def js_runtime_found() -> str:
    """Name of a JavaScript runtime yt-dlp can use for YouTube (deno, node, bun), or ''."""
    import shutil
    for name in ("deno", "node", "bun"):
        if shutil.which(name):
            return name
    return ""


def resource_path(*parts: str) -> Path:
    """Path to a bundled resource (assets/...), valid from source and inside a PyInstaller build."""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    return base.joinpath(*parts)


def js_runtime_path():
    """(name, absolute path) of an installed JavaScript runtime for yt-dlp, or None. Deno is preferred."""
    import shutil
    for name in ("deno", "node", "bun"):
        found = shutil.which(name)
        if found:
            return name, found
    return None


def module_command(module: str) -> List[str]:
    """Argument list that runs `module` as a script, valid both from source and when frozen."""
    if is_frozen():
        return [sys.executable, RUN_MODULE_FLAG, module]
    return [sys.executable, "-m", module]


def is_safe_to_open(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() in SAFE_OPEN_EXTENSIONS


def open_path(path: Path) -> bool:
    """Open a media file with the default application. Refuses non-media extensions."""
    path = Path(path)
    if not is_safe_to_open(path):
        return False
    try:
        system = platform.system()
        if system == "Windows":
            os.startfile(str(path))  # type: ignore[attr-defined]  # noqa: S606
        elif system == "Darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
        return True
    except OSError:
        return False


def reveal_in_folder(path: Path) -> bool:
    """Open the folder containing `path` (selecting the file where the OS supports it)."""
    path = Path(path)
    try:
        system = platform.system()
        if system == "Windows":
            if path.exists():
                subprocess.Popen(["explorer", "/select,", str(path)])
            else:
                os.startfile(str(path.parent))  # type: ignore[attr-defined]  # noqa: S606
        elif system == "Darwin":
            if path.exists():
                subprocess.Popen(["open", "-R", str(path)])
            else:
                subprocess.Popen(["open", str(path.parent)])
        else:
            subprocess.Popen(["xdg-open", str(path.parent if path.suffix else path)])
        return True
    except OSError:
        return False
