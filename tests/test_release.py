"""Version, update check, scheduling, presets, speed limit and settings added for 1.1."""
import datetime as dt
import http.server
import json
import threading
import time

import pytest

import app_info
from config.settings import Settings
from core import quality_manager as qm
from core import scheduler
from core.download_queue import TaskStatus
from core.updater import check_for_update
from tests.helpers import FakeEngine
from tests.test_manager_stubbed import env, wait_idle  # noqa: F401  (fixture reuse)
from utils.validators import validate_settings


def test_version_parsing():
    assert app_info.parse_version("v1.2.3") == (1, 2, 3)
    assert app_info.is_newer("v1.10.0", "1.9.9") and not app_info.is_newer("1.1.0", "1.1.0")
    assert not app_info.is_newer("garbage", "1.0.0")


@pytest.fixture()
def release_server():
    payload = {"tag_name": "v9.0.0", "html_url": "https://example.com/rel", "body": "notes"}

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(payload).encode())

        def log_message(self, *a):
            pass
    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}/", payload
    srv.shutdown()


def test_update_check(release_server):
    url, payload = release_server
    info = check_for_update(url, current="1.0.0", allow_http_localhost=True)
    assert info.version == "9.0.0" and info.url == "https://example.com/rel"
    assert check_for_update(url, current="9.0.0", allow_http_localhost=True) is None
    payload["html_url"] = "http://evil.example/x"
    assert check_for_update(url, current="1.0.0", allow_http_localhost=True).url == ""   # non-https link dropped


def test_update_check_requires_https():
    with pytest.raises(ValueError):
        check_for_update("http://example.com/api")
    with pytest.raises(ValueError):
        check_for_update("file:///etc/passwd")


def test_schedule_window():
    t = dt.time
    assert scheduler.in_window(t(1, 0), t(6, 0), t(3, 0)) and not scheduler.in_window(t(1, 0), t(6, 0), t(7, 0))
    assert scheduler.in_window(t(22, 0), t(6, 0), t(23, 30)) and scheduler.in_window(t(22, 0), t(6, 0), t(2, 0))
    assert not scheduler.in_window(t(22, 0), t(6, 0), t(12, 0))
    cfg = {"schedule_enabled": True, "schedule_start": "22:00", "schedule_end": "06:00"}
    assert scheduler.downloads_allowed(cfg, dt.datetime(2026, 1, 1, 23, 0))
    assert not scheduler.downloads_allowed(cfg, dt.datetime(2026, 1, 1, 12, 0))
    assert scheduler.downloads_allowed({"schedule_enabled": False}, dt.datetime(2026, 1, 1, 12, 0))
    assert scheduler.downloads_allowed({"schedule_enabled": True, "schedule_start": "x", "schedule_end": "y"})


def test_queue_waits_outside_schedule_then_runs(env):  # noqa: F811
    env.engines["engines"] = [FakeEngine("yt-dlp")]
    now = dt.datetime.now()
    away = (now + dt.timedelta(hours=3)).strftime("%H:%M")
    away_end = (now + dt.timedelta(hours=4)).strftime("%H:%M")
    env.manager.settings.update({"schedule_enabled": True, "schedule_start": away, "schedule_end": away_end})
    task = env.manager.add_task("https://example.com/v", qm.build_spec("best", "mp4", 0))
    env.manager.start_queue()
    time.sleep(0.5)
    assert env.queue.get(task.id).status == TaskStatus.PENDING          # held back by the schedule
    assert any("scheduled download window" in m for _, m in env.logs)
    env.manager.settings.update({"schedule_enabled": False})
    env.manager.pump()
    wait_idle(env)
    assert env.queue.get(task.id).status == TaskStatus.COMPLETED


def test_presets():
    best, bal = qm.preset_spec("best"), qm.preset_spec("balanced")
    small, comp = qm.preset_spec("small"), qm.preset_spec("compatible")
    assert best.max_height is None and not best.compat
    assert bal.max_height == 1080 and bal.codec == "h264" and bal.compat
    assert small.max_height == 480 and small.priority == "size"
    assert comp.container == "mp4" and comp.compat and comp.max_height == 1080
    assert "vcodec:h264" in qm.ytdlp_sort(bal) and "+size" in qm.ytdlp_sort(small)


def test_speed_limit_reaches_engine(monkeypatch):
    from types import SimpleNamespace
    from engines.ytdlp_engine import YtdlpEngine
    opts = {}
    YtdlpEngine._speed_options(opts, SimpleNamespace(connections=4, use_aria2c=False, rate_limit_kbps=500), lambda l, m: None)
    assert opts["ratelimit"] == 500 * 1024
    opts = {}
    YtdlpEngine._speed_options(opts, SimpleNamespace(connections=4, use_aria2c=False, rate_limit_kbps=0), lambda l, m: None)
    assert "ratelimit" not in opts
    monkeypatch.setattr("engines.ytdlp_engine.shutil.which", lambda n: "/usr/bin/aria2c")
    opts = {}
    YtdlpEngine._speed_options(opts, SimpleNamespace(connections=4, use_aria2c=True, rate_limit_kbps=300), lambda l, m: None)
    assert "--max-overall-download-limit=300K" in opts["external_downloader_args"]["aria2c"]


def test_new_settings_validate_and_persist(tmp_path):
    s = Settings(tmp_path / "s.json")
    assert s.get("language") == "en" and s.get("theme") == "dark" and s.get("first_run_done") is False
    s.update({"language": "ar", "theme": "light", "speed_limit_kbps": 750, "clipboard_monitor": True,
              "schedule_enabled": True, "schedule_start": "22:30", "schedule_end": "05:00", "first_run_done": True})
    again = Settings(tmp_path / "s.json")
    assert (again.get("language"), again.get("theme"), again.get("speed_limit_kbps")) == ("ar", "light", 750)
    assert again.get("first_run_done") is True
    base = again.snapshot()
    for bad in ({"language": "fr"}, {"theme": "blue"}, {"speed_limit_kbps": -1}, {"schedule_start": "25:99"},
                {"update_url": "http://x.example"}, {"quality_preset": "x"}):
        assert validate_settings({**base, **bad}), bad
