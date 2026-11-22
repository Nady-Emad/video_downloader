"""Download scheduling: an optional daily time window during which queued downloads may start."""
from __future__ import annotations

import datetime as dt
import re
from typing import Any, Mapping, Optional

_TIME = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")


def parse_time(text: str) -> Optional[dt.time]:
    match = _TIME.match(str(text).strip())
    return dt.time(int(match.group(1)), int(match.group(2))) if match else None


def in_window(start: dt.time, end: dt.time, now: dt.time) -> bool:
    """True when `now` is inside [start, end). Windows that cross midnight (22:00-06:00) are supported;
    start == end means 'always'."""
    if start == end:
        return True
    if start < end:
        return start <= now < end
    return now >= start or now < end


def downloads_allowed(settings: Mapping[str, Any], now: Optional[dt.datetime] = None) -> bool:
    if not settings.get("schedule_enabled"):
        return True
    start, end = parse_time(settings.get("schedule_start", "")), parse_time(settings.get("schedule_end", ""))
    if start is None or end is None:
        return True
    return in_window(start, end, (now or dt.datetime.now()).time())
