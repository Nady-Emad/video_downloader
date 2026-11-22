"""Cookie handling. Cookies stay on the local machine and their content is never logged."""
from __future__ import annotations

import shutil
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Dict, List, Tuple

from utils.logger import get_logger

log = get_logger("cookies")

BROWSER_CHOICES: List[Tuple[str, str]] = [
    ("Chrome", "chrome"), ("Firefox", "firefox"), ("Edge", "edge"), ("Brave", "brave"), ("Opera", "opera"),
]
COPY_NAME = "cookies.private.txt"


@dataclass(frozen=True)
class CookieConfig:
    mode: str = "none"      # none | browser | file
    browser: str = ""
    file: str = ""

    @property
    def enabled(self) -> bool:
        if self.mode == "browser":
            return bool(self.browser)
        if self.mode == "file":
            return bool(self.file)
        return False

    def describe(self) -> str:
        """Human readable and safe to log (no paths or content)."""
        if self.mode == "browser" and self.browser:
            return f"cookies from {self.browser}"
        if self.mode == "file" and self.file:
            return "cookies file"
        return "no cookies"


@dataclass
class CookieCheck:
    ok: bool
    message: str
    total: int = 0
    expired: int = 0


def from_settings(settings: Any) -> CookieConfig:
    return CookieConfig(
        mode=str(settings.get("cookie_mode") or "none"),
        browser=str(settings.get("cookie_browser") or ""),
        file=str(settings.get("cookie_file") or ""),
    )


def validate_cookie_file(path: str) -> CookieCheck:
    """Check that `path` looks like a Netscape cookies.txt and report expiry counts only."""
    file = Path(path).expanduser()
    if not file.is_file():
        return CookieCheck(False, "The cookies file does not exist.")
    try:
        lines = file.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:
        return CookieCheck(False, f"The cookies file cannot be read: {exc.strerror or exc}")
    now = time.time()
    total = expired = 0
    for line in lines:
        stripped = line.strip()
        if not stripped or (stripped.startswith("#") and not stripped.startswith("#HttpOnly_")):
            continue
        parts = stripped.split("\t")
        if len(parts) != 7:
            continue
        total += 1
        try:
            expiry = int(parts[4])
        except ValueError:
            continue
        if expiry and expiry < now:
            expired += 1
    if total == 0:
        return CookieCheck(False, "This is not a valid Netscape-format cookies.txt file (no cookie entries found).")
    if expired == total:
        return CookieCheck(True, "All cookies in this file have expired. Export fresh cookies from your browser.",
                           total, expired)
    msg = f"{total} cookies found"
    if expired:
        msg += f" ({expired} expired)"
    return CookieCheck(True, msg + ".", total, expired)


def prepare_private_copy(config: CookieConfig, work_dir: Path) -> CookieConfig:
    """Copy the cookies file into the attempt directory so libraries that rewrite it never
    touch the user's original. The copy is deleted with the attempt directory."""
    if config.mode != "file" or not config.file:
        return config
    source = Path(config.file).expanduser()
    target = Path(work_dir) / COPY_NAME
    try:
        shutil.copyfile(source, target)
        target.chmod(0o600)
    except OSError as exc:
        log.warning("Could not prepare cookies file copy: %s", exc.strerror or exc)
        return CookieConfig()  # continue without cookies rather than crash
    return replace(config, file=str(target))


def ytdlp_options(config: CookieConfig) -> Dict[str, Any]:
    if config.mode == "file" and config.file:
        return {"cookiefile": config.file}
    if config.mode == "browser" and config.browser:
        return {"cookiesfrombrowser": (config.browser,)}
    return {}


def gallery_dl_args(config: CookieConfig) -> List[str]:
    if config.mode == "file" and config.file:
        return ["--cookies", config.file]
    if config.mode == "browser" and config.browser:
        return ["--cookies-from-browser", config.browser]
    return []


def youget_args(config: CookieConfig) -> List[str]:
    """you-get only understands cookie files."""
    if config.mode == "file" and config.file:
        return ["--cookies", config.file]
    return []
