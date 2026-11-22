"""Detect the source platform from a URL."""
from __future__ import annotations

from typing import Dict, Tuple
from urllib.parse import parse_qs, urlparse

YOUTUBE = "YouTube"
FACEBOOK = "Facebook"
TIKTOK = "TikTok"
INSTAGRAM = "Instagram"
TWITTER = "X (Twitter)"
SOUNDCLOUD = "SoundCloud"
VIMEO = "Vimeo"
OTHER = "Other"

_DOMAINS: Dict[str, Tuple[str, ...]] = {
    YOUTUBE: ("youtube.com", "youtu.be", "youtube-nocookie.com"),
    FACEBOOK: ("facebook.com", "fb.watch", "fb.com"),
    TIKTOK: ("tiktok.com",),
    INSTAGRAM: ("instagram.com", "instagr.am"),
    TWITTER: ("twitter.com", "x.com", "t.co"),
    SOUNDCLOUD: ("soundcloud.com", "snd.sc"),
    VIMEO: ("vimeo.com",),
}

ALL_PLATFORMS = (YOUTUBE, FACEBOOK, TIKTOK, INSTAGRAM, TWITTER, SOUNDCLOUD, VIMEO, OTHER)


def _host(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower()
    except ValueError:
        return ""


def detect_platform(url: str) -> str:
    host = _host(url)
    if not host:
        return OTHER
    for platform, domains in _DOMAINS.items():
        for domain in domains:
            if host == domain or host.endswith("." + domain):
                return platform
    return OTHER


def is_youtube(url: str) -> bool:
    return detect_platform(url) == YOUTUBE


def is_playlist_url(url: str) -> bool:
    """Explicit playlist URL (YouTube /playlist or a list= link without a video id).

    Other platforms are decided from extracted metadata (a playlist-type result).
    """
    if detect_platform(url) != YOUTUBE:
        return False
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    list_id = (query.get("list") or [""])[0]
    if not list_id or list_id.startswith(_AUTO_MIX_PREFIXES):
        return False
    return True      # /playlist?list=..., or a video link that carries list=... (the whole playlist is wanted)


_AUTO_MIX_PREFIXES = ("RD", "UL")     # YouTube auto-generated "mixes": endless, so only the video is meant


def playlist_target(url: str) -> str:
    """Canonical URL used to read a YouTube playlist (works for watch?v=..&list=.. links too)."""
    if detect_platform(url) == YOUTUBE and is_playlist_url(url):
        list_id = (parse_qs(urlparse(url).query).get("list") or [""])[0]
        return f"https://www.youtube.com/playlist?list={list_id}"
    return url


def youtube_video_id(url: str) -> str:
    """Video id of a YouTube watch/short/embed/youtu.be link, or ''."""
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if host.endswith("youtu.be"):
        return parsed.path.strip("/").split("/")[0]
    if "youtube" in host:
        vid = (parse_qs(parsed.query).get("v") or [""])[0]
        if vid:
            return vid
        parts = [p for p in parsed.path.split("/") if p]
        if len(parts) >= 2 and parts[0] in ("shorts", "embed", "live", "v"):
            return parts[1]
    return ""


def has_playlist_param_with_video(url: str) -> bool:
    """YouTube watch URL that also carries list= (informational; such links are treated as playlists)."""
    if detect_platform(url) != YOUTUBE:
        return False
    query = parse_qs(urlparse(url).query)
    return "list" in query and ("v" in query or "youtu.be" in (urlparse(url).hostname or ""))
