"""Backoff, keep-vs-delete cancel, resume verification and thumbnail post-processing."""
import http.server
import shutil
import subprocess
import threading
from pathlib import Path

import pytest

from core import thumbnail_tools
from core.cookie_manager import CookieConfig
from core.fallback_manager import DownloadFailed, FallbackManager
from core.ffmpeg_manager import FfmpegManager
from core.quality_manager import build_spec
from engines.base_engine import CancelToken, EngineError
from tests.helpers import FakeEngine, FakeFfmpeg
from utils import file_manager


def test_backoff_is_exponential_and_capped():
    m = FallbackManager([], FakeFfmpeg(), max_retries=5, retry_delay=3)
    assert [m.backoff_delay(i) for i in range(1, 6)] == [3, 6, 12, 24, 48]
    assert m.backoff_delay(20) == m.MAX_BACKOFF


def test_defaults_are_5_retries_3_seconds():
    m = FallbackManager([], FakeFfmpeg())
    assert (m.max_retries, m.retry_delay) == (5, 3.0)


def test_retries_use_backoff_delays(tmp_path, monkeypatch):
    delays = []
    monkeypatch.setattr("core.fallback_manager.interruptible_sleep", lambda d, e: delays.append(d) or True)
    eng = FakeEngine("yt-dlp", lambda r, a, c: (_ for _ in ()).throw(EngineError("Connection timed out")), resume=True)
    m = FallbackManager([eng], FakeFfmpeg(), max_retries=3, retry_delay=2)
    with pytest.raises(DownloadFailed) as err:
        m.run("https://x.com/v", "YouTube", build_spec("best", "mp4", 0), CookieConfig(), tmp_path, lambda t: t,
              lambda p: None, lambda l, m_: None, CancelToken(), resume_key="t1")
    assert delays == [2, 4, 8] and err.value.resumable and len(eng.calls) == 4


def test_cancel_token_priorities():
    t = CancelToken()
    t.cancel("pause")
    assert t.keep_partial and t.is_pause
    t.cancel("keep")
    assert t.keep_partial and not t.is_pause
    t.cancel("cancel")
    assert not t.keep_partial and t.is_cancelled
    t2 = CancelToken()
    t2.cancel("cancel")
    t2.cancel("pause")                       # a weaker request never downgrades a cancel
    assert not t2.keep_partial


def test_resume_dir_discards_partials_of_a_different_request(tmp_path):
    d = file_manager.resume_dir(tmp_path, "task1", "sigA")
    (d / "media.mp4.part").write_bytes(b"x" * 100)
    (d / "media.f137.mp4").write_bytes(b"")
    again = file_manager.resume_dir(tmp_path, "task1", "sigA")
    assert (again / "media.mp4.part").exists()                       # same request: kept
    assert not (again / "media.f137.mp4").exists()                   # 0-byte leftover removed
    other = file_manager.resume_dir(tmp_path, "task1", "sigB")       # quality changed: stale data dropped
    assert not (other / "media.mp4.part").exists()


def test_resume_keeps_partial_between_attempts(tmp_path):
    seen = []

    def behavior(request, attempt, cancel):
        part = request.work_dir / "media.mp4.part"
        seen.append(part.exists())
        if attempt == 1:
            part.write_bytes(b"half")
            raise EngineError("Connection timed out")
        part.unlink()
    eng = FakeEngine("yt-dlp", behavior, resume=True)
    m = FallbackManager([eng], FakeFfmpeg(), max_retries=1, retry_delay=0)
    m.run("https://x.com/v", "YouTube", build_spec("best", "mp4", 0), CookieConfig(), tmp_path, lambda t: t,
          lambda p: None, lambda l, m_: None, CancelToken(), resume_key="t1")
    assert seen == [False, True]                                     # second attempt saw the partial file


# ----------------------------------------------------------------------------- thumbnails
needs_ff = pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="no ffmpeg")


def _make(tmp_path):
    def ff(*a):
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *a], check=True)
    mp3 = tmp_path / "song.mp3"
    ff("-f", "lavfi", "-i", "sine=duration=1", "-c:a", "libmp3lame", str(mp3))
    png = tmp_path / "cover.png"
    ff("-f", "lavfi", "-i", "color=c=blue:s=64x64", "-frames:v", "1", str(png))
    return mp3, png


@needs_ff
def test_embed_cover_and_save_thumbnail(tmp_path):
    mp3, png = _make(tmp_path)
    logs = []
    settings = {"save_thumbnails": True, "embed_audio_thumbnail": True}
    thumbnail_tools.process(settings, FfmpegManager(), mp3, "http://t/x.png", lambda l, m: logs.append(m),
                            fetch=lambda url, d: png)
    jpg = tmp_path / "song.jpg"
    assert jpg.read_bytes()[:2] == b"\xff\xd8"                       # webp/png converted to JPEG
    kinds = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type", "-of", "csv=p=0",
                            str(mp3)], capture_output=True, text=True).stdout.split()
    assert "video" in kinds and "audio" in kinds
    assert not [p for p in tmp_path.iterdir() if p.name.startswith("vdl_thumb_")]   # temp cleaned


@needs_ff
def test_thumbnail_never_overwrites_existing_file(tmp_path):
    mp3, png = _make(tmp_path)
    (tmp_path / "song.jpg").write_bytes(b"mine")
    thumbnail_tools.process({"save_thumbnails": True}, FfmpegManager(), mp3, "http://t/x.png", lambda l, m: None,
                            fetch=lambda url, d: png)
    assert (tmp_path / "song.jpg").read_bytes() == b"mine"
    assert (tmp_path / "song (1).jpg").exists()


@needs_ff
def test_settings_off_means_nothing_happens(tmp_path):
    mp3, png = _make(tmp_path)
    before = mp3.read_bytes()
    thumbnail_tools.process({"save_thumbnails": False, "embed_audio_thumbnail": False}, FfmpegManager(), mp3,
                            "http://t/x.png", lambda l, m: None, fetch=lambda u, d: png)
    assert mp3.read_bytes() == before and not (tmp_path / "song.jpg").exists()


def test_thumbnail_failure_does_not_raise(tmp_path):
    f = tmp_path / "a.mp3"
    f.write_bytes(b"x")
    logs = []
    thumbnail_tools.process({"save_thumbnails": True}, FakeFfmpeg(), f, "http://t/x", lambda l, m: logs.append(l),
                            fetch=lambda u, d: None)
    assert "WARNING" in logs
    thumbnail_tools.process({"save_thumbnails": True}, FakeFfmpeg(), f, "http://t/x", lambda l, m: None,
                            fetch=lambda u, d: (_ for _ in ()).throw(OSError("offline")))


def test_ytdlp_speed_options(monkeypatch):
    from types import SimpleNamespace
    from engines.ytdlp_engine import YtdlpEngine
    logs = []
    opts = {}
    YtdlpEngine._speed_options(opts, SimpleNamespace(connections=12, use_aria2c=False), lambda l, m: logs.append(m))
    assert opts["concurrent_fragment_downloads"] == 12 and opts["http_chunk_size"] == 10 * 1024 * 1024
    assert "external_downloader" not in opts
    monkeypatch.setattr("engines.ytdlp_engine.shutil.which", lambda n: "/usr/bin/aria2c")
    opts = {}
    YtdlpEngine._speed_options(opts, SimpleNamespace(connections=99, use_aria2c=True), lambda l, m: None)
    assert opts["external_downloader"] == {"default": "aria2c"} and "-x16" in opts["external_downloader_args"]["aria2c"]
    monkeypatch.setattr("engines.ytdlp_engine.shutil.which", lambda n: None)
    opts = {}
    YtdlpEngine._speed_options(opts, SimpleNamespace(connections=4, use_aria2c=True), lambda l, m: logs.append(m))
    assert "external_downloader" not in opts and any("aria2c is not installed" in m for m in logs)


def test_js_runtime_is_passed_to_ytdlp(monkeypatch):
    from engines import ytdlp_engine
    monkeypatch.setattr(ytdlp_engine, "js_runtime_path", lambda: ("node", "/usr/bin/node"))
    opts = ytdlp_engine.YtdlpEngine()._base_opts(CookieConfig(), lambda l, m: None)
    assert opts["js_runtimes"] == {"node": {"path": "/usr/bin/node"}}


def test_windows_file_lock_is_retried_not_failed_over():
    from utils.network import classify_error
    info = classify_error(RuntimeError("[WinError 32] The process cannot access the file because it is being "
                                       "used by another process: 'x.part'"))
    assert info.transient and info.kind == "file_locked"
