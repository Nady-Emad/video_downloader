import json
import time

import pytest

from config.settings import Settings
from core import cookie_manager as cm
from core.download_history import STATUS_COMPLETED, STATUS_FAILED, DownloadHistory, HistoryRecord
from core.download_queue import DownloadQueue, DuplicateTaskError, TaskStatus
from core.quality_manager import build_spec


# ----------------------------------------------------------------------------- history
def make_history(tmp_path):
    return DownloadHistory(tmp_path / "h.db")


def test_history_save_search_filter(tmp_path):
    h = make_history(tmp_path)
    f = tmp_path / "a.mp4"
    f.write_bytes(b"1")
    h.add(HistoryRecord(title="Python 100% Course_1", url="u1", platform="YouTube", file_path=str(f),
                        downloaded_at="2026-01-01T10:00:00"))
    h.add(HistoryRecord(title="Cats", url="u2", platform="TikTok", status=STATUS_FAILED, error="boom",
                        downloaded_at="2026-01-02T10:00:00"))
    assert len(h.search()) == 2
    assert [r.title for r in h.search(newest_first=True)] == ["Cats", "Python 100% Course_1"]
    assert [r.title for r in h.search(newest_first=False)][0] == "Python 100% Course_1"
    assert len(h.search(text="python")) == 1
    assert len(h.search(text="100%")) == 1          # LIKE wildcards are escaped
    assert len(h.search(text="%")) == 1
    assert len(h.search(text="_")) == 1
    assert [r.title for r in h.search(platform="TikTok")] == ["Cats"]
    assert [r.title for r in h.search(status=STATUS_FAILED)] == ["Cats"]
    assert h.platforms() == ["TikTok", "YouTube"]
    assert h.find_completed("u1").file_path == str(f)
    assert h.find_completed("u2") is None


def test_history_remove_keeps_file(tmp_path):
    h = make_history(tmp_path)
    f = tmp_path / "keep.mp4"
    f.write_bytes(b"data")
    rid = h.add(HistoryRecord(title="t", url="u", file_path=str(f)))
    assert h.remove(rid)
    assert h.get(rid) is None and f.exists()
    h.add(HistoryRecord(title="t2", url="u"))
    assert h.clear() and h.search() == []


def test_history_survives_bad_db(tmp_path):
    bad = tmp_path / "bad.db"
    bad.write_bytes(b"this is not a sqlite database" * 100)
    h = DownloadHistory(bad)
    assert not h.ok
    assert h.add(HistoryRecord(title="t", url="u")) is None
    assert h.search() == []


# ----------------------------------------------------------------------------- queue
def test_queue_add_duplicates_and_order(tmp_path):
    q = DownloadQueue(tmp_path / "q.db")
    spec = build_spec("720", "mp4", 192)
    a = q.add("https://a.com/1", spec, str(tmp_path))
    b = q.add("https://a.com/2", spec, str(tmp_path))
    with pytest.raises(DuplicateTaskError):
        q.add("https://a.com/1", spec, str(tmp_path))
    q.add("https://a.com/1", build_spec("480", "mp4", 192), str(tmp_path))   # different quality is allowed
    assert q.next_pending().id == a.id
    q.set_priority(b.id, 5)
    assert q.next_pending().id == b.id


def test_queue_status_transitions(tmp_path):
    q = DownloadQueue()
    spec = build_spec("best", "mp4", 0)
    t = q.add("https://a.com/1", spec, "/tmp")
    q.set_status(t.id, TaskStatus.DOWNLOADING)
    assert not q.remove(t.id)                       # active tasks cannot be removed
    q.set_status(t.id, TaskStatus.FAILED, "x")
    assert q.retry(t.id) and q.get(t.id).status == TaskStatus.PENDING and q.get(t.id).error == ""
    assert q.pause_task(t.id) and q.get(t.id).status == TaskStatus.PAUSED
    assert q.next_pending() is None
    assert q.resume_task(t.id)
    q.set_status(t.id, TaskStatus.COMPLETED)
    assert q.clear_completed() == 1 and q.tasks() == []


def test_queue_remove_pending(tmp_path):
    q = DownloadQueue(tmp_path / "q.db")
    t = q.add("https://a.com/1", build_spec("best", "mp4", 0), "/tmp")
    assert q.remove(t.id) and q.tasks() == []
    assert DownloadQueue(tmp_path / "q.db").tasks() == []


def test_queue_recovery_after_restart(tmp_path):
    db = tmp_path / "q.db"
    q = DownloadQueue(db)
    spec = build_spec("1080", "mkv", 320)
    pending = q.add("https://a.com/1", spec, "/d")
    running = q.add("https://a.com/2", spec, "/d")
    done = q.add("https://a.com/3", spec, "/d")
    failed = q.add("https://a.com/4", spec, "/d")
    q.set_status(running.id, TaskStatus.DOWNLOADING)
    q.set_status(done.id, TaskStatus.COMPLETED)
    q.set_status(failed.id, TaskStatus.FAILED, "err")

    q2 = DownloadQueue(db)                         # "restart"
    by_url = {t.url: t for t in q2.tasks()}
    assert set(by_url) == {"https://a.com/1", "https://a.com/2", "https://a.com/4"}
    assert by_url["https://a.com/2"].status == TaskStatus.PENDING     # interrupted -> pending
    assert by_url["https://a.com/4"].status == TaskStatus.FAILED and by_url["https://a.com/4"].error == "err"
    assert by_url["https://a.com/1"].quality == spec
    new = q2.add("https://a.com/9", spec, "/d")
    assert new.seq > max(t.seq for t in q2.tasks() if t.id != new.id)


# ----------------------------------------------------------------------------- settings
def test_settings_persist_and_validate(tmp_path):
    path = tmp_path / "s.json"
    s = Settings(path)
    assert s.update({"download_dir": str(tmp_path / "dl"), "max_retries": 5, "default_container": "mkv"}) == []
    s2 = Settings(path)
    assert s2.get("max_retries") == 5 and s2.get("default_container") == "mkv"
    errors = s2.update({"max_retries": 99, "retry_delay": -1, "max_concurrent": 0, "conflict_policy": "nope",
                        "log_level": "loud", "filename_template": "{bogus}"})
    assert len(errors) >= 5
    assert s2.get("max_retries") == 5                # nothing applied on error
    assert Settings(path).get("max_retries") == 5


def test_settings_cookie_rules(tmp_path):
    s = Settings(tmp_path / "s.json")
    assert any("cookies file" in e for e in s.update({"cookie_mode": "file", "cookie_file": str(tmp_path / "none.txt")}))
    assert s.update({"cookie_mode": "browser", "cookie_browser": "firefox"}) == []


def test_settings_corrupt_file_uses_defaults(tmp_path):
    path = tmp_path / "s.json"
    path.write_text("{not json")
    assert Settings(path).get("max_retries") == 5
    path.write_text(json.dumps({"max_retries": "many", "max_concurrent": 2, "unknown": 1}))
    s = Settings(path)
    assert s.get("max_retries") == 5 and s.get("max_concurrent") == 2


# ----------------------------------------------------------------------------- cookies
COOKIE_LINE = ".example.com\tTRUE\t/\tTRUE\t{exp}\tsession\tSECRETVALUE"


def test_cookie_file_validation(tmp_path):
    good = tmp_path / "c.txt"
    good.write_text("# Netscape HTTP Cookie File\n" + COOKIE_LINE.format(exp=int(time.time()) + 3600) + "\n")
    check = cm.validate_cookie_file(str(good))
    assert check.ok and check.total == 1 and check.expired == 0
    assert "SECRETVALUE" not in check.message

    expired = tmp_path / "e.txt"
    expired.write_text(COOKIE_LINE.format(exp=1000) + "\n")
    check = cm.validate_cookie_file(str(expired))
    assert check.ok and "expired" in check.message

    junk = tmp_path / "j.txt"
    junk.write_text("hello world\n")
    assert not cm.validate_cookie_file(str(junk)).ok
    assert not cm.validate_cookie_file(str(tmp_path / "missing.txt")).ok


def test_cookie_options_and_private_copy(tmp_path):
    f = tmp_path / "c.txt"
    f.write_text(COOKIE_LINE.format(exp=2000000000) + "\n")
    cfg = cm.CookieConfig(mode="file", file=str(f))
    work = tmp_path / "work"
    work.mkdir()
    private = cm.prepare_private_copy(cfg, work)
    assert private.file != str(f) and (work / cm.COPY_NAME).exists()
    assert cm.ytdlp_options(private) == {"cookiefile": private.file}
    assert cm.ytdlp_options(cm.CookieConfig("browser", "firefox")) == {"cookiesfrombrowser": ("firefox",)}
    assert cm.ytdlp_options(cm.CookieConfig()) == {}
    assert cm.gallery_dl_args(cm.CookieConfig("browser", "chrome")) == ["--cookies-from-browser", "chrome"]
    assert cm.youget_args(cm.CookieConfig("browser", "chrome")) == []
    assert str(f) not in cfg.describe()
