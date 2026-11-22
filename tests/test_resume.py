import http.server
import sqlite3
import threading

import pytest

from core.cookie_manager import CookieConfig
from core.download_history import DownloadHistory, HistoryRecord
from core.fallback_manager import DownloadFailed, FallbackManager
from core.quality_manager import build_spec
from engines.base_engine import CancelToken, DownloadCancelled, EngineError
from engines.ytdlp_engine import _best_thumbnail
from tests.helpers import FakeEngine, FakeFfmpeg
from utils import file_manager as fm
from utils import network

SPEC = build_spec("720", "mp4", 192)


def run(manager, dest, cancel=None, key="a" * 32):
    logs = []
    result = manager.run("https://example.com/v", "YouTube", SPEC, CookieConfig(), dest, lambda t: t,
                         lambda p: None, lambda l, m: logs.append(m), cancel or CancelToken(),
                         title_hint="", resume_key=key)
    return result, logs


def mgr(engines, retries=0):
    return FallbackManager(engines, FakeFfmpeg(), max_retries=retries, retry_delay=0)


def test_cancel_token_semantics():
    t = CancelToken()
    t.cancel("pause")
    assert t.is_cancelled and t.is_pause
    t.cancel("cancel")                      # a real cancel wins over a pause
    assert not t.is_pause
    t2 = CancelToken()
    t2.cancel("cancel")
    t2.cancel("pause")                      # ...and a later pause cannot downgrade it
    assert not t2.is_pause


def test_network_loss_keeps_partial_then_resumes(tmp_path):
    seen = []

    def behavior(request, attempt, cancel):
        part = request.work_dir / "media.mp4.part"
        seen.append(part.exists())
        if attempt == 1:
            part.write_bytes(b"half")
            raise EngineError("Connection timed out")
        part.unlink()                        # engine "resumes" from the part file and finishes

    engine = FakeEngine("yt-dlp", behavior, resume=True)
    with pytest.raises(DownloadFailed) as err:
        run(mgr([engine]), tmp_path)
    assert err.value.resumable and err.value.info.transient
    kept = tmp_path / ".vdl_tmp" / ("a" * 32)
    assert (kept / "media.mp4.part").read_bytes() == b"half"          # progress preserved

    result, logs = run(mgr([engine]), tmp_path)                        # later: connection is back
    assert seen == [False, True] and result.file_path.exists()
    assert any("resuming" in m.lower() for m in logs)
    assert not kept.exists()                                           # cleaned after success


def test_retries_reuse_same_resumable_folder(tmp_path):
    dirs = []

    def behavior(request, attempt, cancel):
        dirs.append(request.work_dir)
        if attempt < 3:
            raise EngineError("Connection reset by peer")
    engine = FakeEngine("yt-dlp", behavior, resume=True)
    result, _ = run(mgr([engine], retries=3), tmp_path)
    assert len(set(dirs)) == 1 and result.file_path.exists()


def test_pause_keeps_cancel_discards(tmp_path):
    def stop_with(reason):
        token = CancelToken()

        def behavior(request, attempt, cancel):
            (request.work_dir / "media.mp4.part").write_bytes(b"x")
            token.cancel(reason)
            cancel.raise_if_cancelled()
        return token, FakeEngine("yt-dlp", behavior, resume=True)

    token, engine = stop_with("pause")
    with pytest.raises(DownloadCancelled):
        run(mgr([engine]), tmp_path, cancel=token)
    assert (tmp_path / ".vdl_tmp" / ("a" * 32) / "media.mp4.part").exists()

    token, engine = stop_with("cancel")
    with pytest.raises(DownloadCancelled):
        run(mgr([engine]), tmp_path, cancel=token)
    assert not (tmp_path / ".vdl_tmp").exists()                        # everything removed


def test_permanent_error_discards_partial(tmp_path):
    def behavior(request, attempt, cancel):
        (request.work_dir / "media.mp4.part").write_bytes(b"x")
        raise EngineError("Private video")
    with pytest.raises(DownloadFailed) as err:
        run(mgr([FakeEngine("yt-dlp", behavior, resume=True)]), tmp_path)
    assert not err.value.resumable and not (tmp_path / ".vdl_tmp").exists()


def test_non_resumable_engine_never_keeps_partial(tmp_path):
    def behavior(request, attempt, cancel):
        (request.work_dir / "media.mp4.part").write_bytes(b"x")
        raise EngineError("Connection timed out")
    with pytest.raises(DownloadFailed) as err:
        run(mgr([FakeEngine("pytubefix", behavior, resume=False)]), tmp_path)
    assert not err.value.resumable and not (tmp_path / ".vdl_tmp").exists()


def test_resumable_failure_stops_chain_to_keep_progress(tmp_path):
    def behavior(request, attempt, cancel):
        raise EngineError("Connection timed out")
    second = FakeEngine("pytubefix")
    with pytest.raises(DownloadFailed):
        run(mgr([FakeEngine("yt-dlp", behavior, resume=True), second]), tmp_path)
    assert not second.calls


def test_stale_cleanup_keeps_resumable_but_expires_old(tmp_path):
    keep = fm.resume_dir(tmp_path, "b" * 32)
    (keep / fm.LOCK_NAME).write_text("999999999")                      # owner process is gone
    assert fm.cleanup_stale_attempts(tmp_path, max_age_hours=0.0) == 0 and keep.exists()
    import os, time
    old = time.time() - 8 * 86400
    os.utime(keep / fm.LOCK_NAME, (old, old))
    assert fm.cleanup_stale_attempts(tmp_path, max_age_hours=0.0) == 1 and not keep.exists()


def test_cleanup_task_partials_includes_playlist_folders(tmp_path):
    task = "c" * 32
    a = fm.resume_dir(tmp_path, task)
    b = fm.resume_dir(tmp_path / "Playlist", f"{task}-3")
    other = fm.resume_dir(tmp_path, "d" * 32)
    assert fm.cleanup_task_partials(tmp_path, task) == 2
    assert not a.exists() and not b.exists() and other.exists()


# ----------------------------------------------------------------------------- thumbnails
class _Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path == "/ok.jpg":
            body, ctype = b"\xff\xd8\xff" + b"0" * 500, "image/jpeg"
        elif self.path == "/page":
            body, ctype = b"<html></html>", "text/html"
        elif self.path == "/big.jpg":
            body, ctype = b"0" * (network.MAX_THUMBNAIL_BYTES + 10), "image/jpeg"
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture
def server():
    httpd = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


def test_fetch_image_ok_cached_and_rejections(tmp_path, server):
    path = network.fetch_image(server + "/ok.jpg", tmp_path)
    assert path and path.suffix == ".jpg" and path.stat().st_size > 100
    assert network.fetch_image("http://127.0.0.1:1/never-reached.jpg", tmp_path) is None
    assert network.fetch_image(server + "/page", tmp_path) is None          # not an image
    assert network.fetch_image(server + "/big.jpg", tmp_path) is None       # too large
    assert network.fetch_image(server + "/missing.jpg", tmp_path) is None  # 404
    assert network.fetch_image("file:///etc/passwd", tmp_path) is None      # scheme refused
    assert network.fetch_image("ftp://x.com/a.jpg", tmp_path) is None
    assert not list(tmp_path.glob("*.tmp"))
    # second call is served from the cache even when the URL is unreachable now
    assert network.fetch_image(server + "/ok.jpg", tmp_path) == path


def test_best_thumbnail_prefers_widest_jpeg():
    raw = {"thumbnail": "https://x/fallback.webp", "thumbnails": [
        {"url": "https://x/a.webp", "width": 1280}, {"url": "https://x/small.jpg", "width": 120},
        {"url": "https://x/big.jpg?sqp=1", "width": 640}, {"url": "ftp://x/evil.jpg", "width": 9999}]}
    assert _best_thumbnail(raw) == "https://x/big.jpg?sqp=1"
    assert _best_thumbnail({"thumbnail": "https://x/t.webp"}) == "https://x/t.webp"
    assert _best_thumbnail({}) == ""


def test_history_stores_thumbnail_and_migrates_old_db(tmp_path):
    db = tmp_path / "old.db"
    conn = sqlite3.connect(db)
    conn.executescript("""CREATE TABLE history (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL,
        url TEXT NOT NULL, platform TEXT NOT NULL DEFAULT '', file_path TEXT NOT NULL DEFAULT '',
        file_size INTEGER NOT NULL DEFAULT 0, resolution TEXT NOT NULL DEFAULT '', format TEXT NOT NULL DEFAULT '',
        engine TEXT NOT NULL DEFAULT '', downloaded_at TEXT NOT NULL, status TEXT NOT NULL,
        error TEXT NOT NULL DEFAULT '', spec_json TEXT NOT NULL DEFAULT '', dest_dir TEXT NOT NULL DEFAULT '');
        INSERT INTO history (title,url,downloaded_at,status) VALUES ('old','u','2025-01-01T00:00:00','Completed');""")
    conn.commit()
    conn.close()
    h = DownloadHistory(db)
    assert h.ok and h.search()[0].thumbnail == ""
    h.add(HistoryRecord(title="new", url="u2", thumbnail="https://x/t.jpg"))
    assert h.search(text="new")[0].thumbnail == "https://x/t.jpg"
