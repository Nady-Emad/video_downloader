"""Application identity and version (single source of truth; also read by the installer script)."""
from __future__ import annotations

import re
from typing import Tuple

APP_NAME = "Video Downloader"
APP_ID = "VideoDownloader"
VERSION = "1.1.0"


def parse_version(text: str) -> Tuple[int, ...]:
    """'v1.2.3-beta' -> (1, 2, 3). Unparseable text gives ()."""
    return tuple(int(n) for n in re.findall(r"\d+", text.split("-")[0])[:4])


def is_newer(candidate: str, current: str = VERSION) -> bool:
    a, b = parse_version(candidate), parse_version(current)
    return bool(a) and a > b
