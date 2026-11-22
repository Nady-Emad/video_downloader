"""Media library, dashboard statistics and diagnostics."""
import os
import time

import pytest

from core import diagnostics, media_library as lib
from core.download_history import DownloadHistory, HistoryRecord


def make(root, rel, size=10, age=0):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"x" * size)
    t = time.time() - age
    os.utime(p, (t, t))
    return p


def test_scan_filters_and_sorts(tmp_path):
    make(tmp_path, "a.mp4", 100, 30)
    make(tmp_path, "list/b.mp3", 50, 20)
    make(tmp_path, "list/c.mkv", 300, 10)
    make(tmp_path, "a.jpg", 5)                       # saved thumbnail
    make(tmp_path, "notes.txt")
    make(tmp_path, "d.mp4.part")
    make(tmp_path, ".vdl_tmp/abc/media.mp4", 999)
    items = lib.scan(tmp_path)
    assert sorted(i.name for i in items) == ["a", "b", "c"]
    a = next(i for i in items if i.name == "a")
    assert a.thumbnail.endswith("a.jpg")
    assert [i.name for i in lib.filter_sort(items, sort="size")] == ["c", "a", "b"]
    assert [i.name for i in lib.filter_sort(items, sort="date")] == ["c", "b", "a"]
    assert [i.name for i in lib.filter_sort(items, kind="audio")] == ["b"]
    assert [i.name for i in lib.filter_sort(items, text="C")] == ["c"]
    stats = lib.storage_stats(items, tmp_path)
    assert stats["files"] == 3 and stats["bytes"] == 450 and stats["audio_bytes"] == 50 and stats["disk_total"] > 0


def test_rename_never_overwrites_and_moves_thumbnail(tmp_path):
    a = make(tmp_path, "a.mp4")
    make(tmp_path, "a.jpg")
    make(tmp_path, "b.mp4")
    new = lib.rename(tmp_path, a, "b")
    assert new.name == "b (1).mp4" and (tmp_path / "b.mp4").exists()
    assert (tmp_path / "b (1).jpg").exists() and not (tmp_path / "a.jpg").exists()
    with pytest.raises(ValueError):
        lib.rename(tmp_path, new, "   ")
    outside = make(tmp_path.parent, "outside.mp4")
    with pytest.raises(ValueError):
        lib.rename(tmp_path, outside, "x")


def test_delete_is_restricted(tmp_path):
    media = make(tmp_path, "a.mp4")
    thumb = make(tmp_path, "a.jpg")
    note = make(tmp_path, "notes.txt")
    outside = make(tmp_path.parent, "outside2.mp4")
    for bad in (note, outside, tmp_path):
        with pytest.raises(ValueError):
            lib.delete(tmp_path, bad)
    lib.delete(tmp_path, media)
    assert not media.exists() and thumb.exists() and note.exists() and outside.exists()


def test_history_stats_and_find_completed(tmp_path):
    h = DownloadHistory(tmp_path / "h.db")
    f = make(tmp_path, "v.mp4", 7)
    h.add(HistoryRecord(title="v", url="https://x/y", file_path=str(f), file_size=7, status="Completed"))
    h.add(HistoryRecord(title="w", url="https://x/z", file_size=0, status="Failed"))
    assert h.stats() == {"completed": 1, "bytes": 7, "failed": 1}
    assert h.find_completed("https://x/y").title == "v"
    f.unlink()
    assert h.find_completed("https://x/y") is None
    assert h.find_completed("https://x/z") is None


def test_error_store_categories_and_redaction():
    store = diagnostics.ErrorStore(limit=2)
    store.add("t", "https://x/?token=abc", "FFmpeg is required to merge streams")
    store.add("t", "https://x", "Sign in to confirm you're not a bot")
    e = store.add("t", "https://x", "[WinError 32] used by another process", details="password=hunter2")
    assert len(store.items()) == 2 and store.items()[0] is e
    assert e.category == "Storage / permissions" and "hunter2" not in e.as_text()
    assert store.items()[1].category == "Authentication" and store.items()[1].hint
    assert diagnostics.categorize("connection timed out").transient


def test_report_hides_secrets(tmp_path):
    log = tmp_path / "app.log"
    log.write_text("line1\npassword=topsecret\nline3\n")
    entry = diagnostics.ErrorEntry("t", "https://x", "boom", "Other")
    report = diagnostics.build_report({"cookie_file": "C:/secret/cookies.txt", "theme": "dark"},
                                      {"FFmpeg": False, "yt-dlp": True}, [entry], log, {"JS runtime": "node"})
    assert "cookies.txt" not in report and "topsecret" not in report and "theme = dark" in report
    assert "FFmpeg: MISSING" in report and "yt-dlp: found" in report and "JS runtime: node" in report


from tests.helpers import FakeEngine  # noqa: E402
from tests.test_manager_stubbed import env, wait_idle  # noqa: E402,F401
from core.quality_manager import build_spec  # noqa: E402
from engines.base_engine import EngineError  # noqa: E402


def test_manager_records_failures_in_error_center(env):  # noqa: F811
    env.engines["engines"] = [FakeEngine("yt-dlp", lambda r, a, c: (_ for _ in ()).throw(
        EngineError("Sign in to confirm you're not a bot", "auth")))]
    signals = []
    env.manager.error_recorded.connect(lambda: signals.append(1))
    env.manager.add_task("https://example.com/v", build_spec("best", "mp4", 0))
    env.manager.start_queue()
    wait_idle(env)
    items = env.manager.errors.items()
    assert len(items) == 1 and items[0].category == "Authentication" and signals


import shutil  # noqa: E402
import subprocess  # noqa: E402


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")
def test_make_preview_creates_cached_jpeg(tmp_path):
    video = tmp_path / "v.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=duration=1:size=160x90:rate=5",
                    "-pix_fmt", "yuv420p", str(video)], check=True)
    cache = tmp_path / "cache"
    out = lib.make_preview("ffmpeg", video, cache)
    assert out and out.read_bytes()[:2] == b"\xff\xd8"
    mtime = out.stat().st_mtime_ns
    assert lib.make_preview("ffmpeg", video, cache) == out and out.stat().st_mtime_ns == mtime   # cached
    assert lib.make_preview("ffmpeg", tmp_path / "a.mp3", cache) is None
    assert lib.make_preview("", video, cache) is None
