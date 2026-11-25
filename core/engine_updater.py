"""Update the download engines (yt-dlp, gallery-dl, you-get, pytubefix) from inside the app. Qt-free."""
from __future__ import annotations

import subprocess
import sys
from typing import Callable, List, Tuple

from utils.system_info import is_frozen

PACKAGES = ["yt-dlp[default,curl-cffi]", "gallery-dl", "you-get", "pytubefix"]
FROZEN_MESSAGE = ("This is a packaged (exe) build: the engines are inside it and cannot be updated in place. "
                  "Run make_exe.bat again (it installs the newest versions) or use the Python version of the app.")


def pip_command() -> List[str]:
    return [sys.executable, "-m", "pip", "install", "--upgrade", "--disable-pip-version-check", *PACKAGES]


def update_engines(log: Callable[[str], None] = lambda _m: None, timeout: int = 600) -> Tuple[bool, str]:
    """Run pip to upgrade the engines. Returns (success, readable output). Restart the app afterwards."""
    if is_frozen():
        return False, FROZEN_MESSAGE
    try:
        proc = subprocess.run(pip_command(), capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"Could not run pip: {exc}"
    output = ((proc.stdout or "") + (proc.stderr or "")).strip()
    for line in output.splitlines()[-20:]:
        log(line)
    return proc.returncode == 0, output[-2000:]
