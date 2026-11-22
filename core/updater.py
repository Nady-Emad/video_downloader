"""Update check against a GitHub-releases style JSON endpoint ({"tag_name": "v1.2.0", "html_url": "..."}).
Runs in a worker thread. Nothing is downloaded or installed automatically: the user is only told a release exists."""
from __future__ import annotations

import json
import urllib.request
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlparse

import app_info


@dataclass
class UpdateInfo:
    version: str
    url: str
    notes: str = ""


def check_for_update(endpoint: str, current: str = app_info.VERSION, timeout: float = 8.0,
                     allow_http_localhost: bool = False) -> Optional[UpdateInfo]:
    """Return UpdateInfo when a newer release exists, None when up to date. Raises ValueError/OSError on problems."""
    parsed = urlparse(endpoint.strip())
    local = parsed.hostname in ("127.0.0.1", "localhost")
    if parsed.scheme != "https" and not (allow_http_localhost and local and parsed.scheme == "http"):
        raise ValueError("The update address must start with https://")
    request = urllib.request.Request(endpoint, headers={"User-Agent": f"{app_info.APP_ID}/{current}",
                                                         "Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - scheme validated above
        data = json.loads(response.read(500_000).decode("utf-8", "replace"))
    if not isinstance(data, dict):
        raise ValueError("Unexpected update response.")
    tag = str(data.get("tag_name") or data.get("version") or "")
    if not app_info.is_newer(tag, current):
        return None
    page = str(data.get("html_url") or data.get("url") or "")
    if page and not page.startswith("https://"):
        page = ""                                       # never offer a non-https link
    return UpdateInfo(version=tag.lstrip("vV"), url=page, notes=str(data.get("body") or "")[:2000])
