from core.cookie_manager import CookieConfig
from core.download_history import STATUS_COMPLETED, STATUS_FAILED, DownloadHistory
from core.download_queue import DownloadQueue, TaskStatus
from core.quality_manager import build_spec
from core.task_runner import TaskCallbacks, TaskRunner
from engines.base_engine import CancelToken, EngineError, MediaInfo, PlaylistEntry
from tests.helpers import FakeEngine, FakeFfmpeg

SETTINGS = {"max_retries": 0, "retry_delay": 0, "conflict_policy": "rename",
            "filename_template": "{title}", "playlist_filename_template": "{index} - {title}"}


class Recorder(TaskCallbacks):
    def __init__(self):
        super().__init__()
        self.logs, self.playlist_calls, self.statuses = [], [], []
        self.log = lambda lvl, msg: self.logs.append((lvl, msg))
        self.playlist = lambda d, t, f: self.playlist_calls.append((d, t, f))
        self.status = lambda s, d: self.statuses.append(s)


def build(tmp_path, engines, url="https://www.youtube.com/watch?v=abc", cancel=None):
    queue = DownloadQueue()
    task = queue.add(url, build_spec("720", "mp4", 192), str(tmp_path / "dl"))
    history = DownloadHistory(tmp_path / "h.db")
    rec = Recorder()
    runner = TaskRunner(task, SETTINGS, CookieConfig(), FakeFfmpeg(), engines, history, rec, cancel or CancelToken())
    return runner, history, rec


def named_engine(info, behavior=None, **kw):
    return FakeEngine("yt-dlp", behavior, info=info, **kw)


def playlist_info(n=4, unavailable=()):
    entries = []
    for i in range(1, n + 1):
        entries.append(PlaylistEntry(i, f"Lesson {i}", f"https://www.youtube.com/watch?v=id{i}",
                                     i not in unavailable, f"id{i}"))
    return MediaInfo(url="u", title="Python Course", is_playlist=True, entries=entries)


def test_single_video_recorded_in_history(tmp_path):
    info = MediaInfo(url="u", title="My Video", id="abc")
    runner, history, rec = build(tmp_path, [named_engine(info, title="My Video")])
    status, msg = runner.run()
    assert status == TaskStatus.COMPLETED
    records = history.search()
    assert len(records) == 1 and records[0].status == STATUS_COMPLETED
    assert records[0].engine == "yt-dlp" and records[0].platform == "YouTube" and records[0].file_size > 0
    assert (tmp_path / "dl" / "My Video.mp4").exists()          # no index for single videos
    assert rec.statuses[:2] == [TaskStatus.EXTRACTING, TaskStatus.DOWNLOADING]


def test_invalid_url_fails_cleanly(tmp_path):
    runner, history, _ = build(tmp_path, [named_engine(None)], url="https://x")
    status, msg = runner.run()
    assert status == TaskStatus.FAILED and "host" in msg.lower()
    assert history.search()[0].status == STATUS_FAILED


def test_failure_recorded_with_error(tmp_path):
    def boom(request, attempt, cancel):
        raise EngineError("Video unavailable")
    runner, history, _ = build(tmp_path, [named_engine(None, boom)])
    status, msg = runner.run()
    assert status == TaskStatus.FAILED
    rec = history.search()[0]
    assert rec.status == STATUS_FAILED and rec.error and rec.spec_json


def test_metadata_failure_does_not_block_download(tmp_path):
    class NoInfo(FakeEngine):
        def fetch_info(self, *a, **k):
            raise EngineError("HTTP Error 500")
    runner, history, rec = build(tmp_path, [NoInfo("yt-dlp")])
    assert runner.run()[0] == TaskStatus.COMPLETED
    assert any("Metadata extraction failed" in m for _, m in rec.logs)


def test_playlist_folder_index_and_isolation(tmp_path):
    def behavior(request, attempt, cancel):
        if "id2" in request.url:
            raise EngineError("Video unavailable")      # entry 2 fails permanently
    engine = named_engine(playlist_info(4, unavailable={3}), behavior)
    runner, history, rec = build(tmp_path, [engine], url="https://www.youtube.com/playlist?list=PL1")
    status, msg = runner.run()
    folder = tmp_path / "dl" / "Python Course"
    names = sorted(p.name for p in folder.iterdir())
    assert names == ["01 - Lesson 1.mp4", "04 - Lesson 4.mp4"]      # indexes preserved, no temp leftovers
    assert status == TaskStatus.COMPLETED and "Completed with problems" in msg
    statuses = sorted(r.status for r in history.search())
    assert statuses == [STATUS_COMPLETED, STATUS_COMPLETED, STATUS_FAILED]
    assert rec.playlist_calls[-1] == (2, 4, 2)                      # done, total, failed+unavailable
    assert len(engine.calls) == 3                                   # unavailable entry never attempted


def test_playlist_resume_skips_existing(tmp_path):
    engine = named_engine(playlist_info(3))
    runner, _, _ = build(tmp_path, [engine], url="https://www.youtube.com/playlist?list=PL1")
    runner.run()
    assert len(engine.calls) == 3
    engine2 = named_engine(playlist_info(3))
    runner2, _, rec2 = build(tmp_path, [engine2], url="https://www.youtube.com/playlist?list=PL1")
    assert runner2.run()[0] == TaskStatus.COMPLETED
    assert not engine2.calls and sum("already downloaded" in m for _, m in rec2.logs) == 3
    assert len(list((tmp_path / "dl" / "Python Course").iterdir())) == 3


def test_playlist_cancel_keeps_completed_entries(tmp_path):
    token = CancelToken()

    def behavior(request, attempt, cancel):
        if "id2" in request.url:
            token.cancel()
            cancel.raise_if_cancelled()
    engine = named_engine(playlist_info(4), behavior)
    runner, history, _ = build(tmp_path, [engine], url="https://www.youtube.com/playlist?list=PL1", cancel=token)
    status, _ = runner.run()
    assert status == TaskStatus.CANCELLED
    assert [p.name for p in (tmp_path / "dl" / "Python Course").iterdir()] == ["01 - Lesson 1.mp4"]
    assert len(history.search()) == 1


def test_playlist_all_fail_is_failed(tmp_path):
    def boom(request, attempt, cancel):
        raise EngineError("Video unavailable")
    runner, _, _ = build(tmp_path, [named_engine(playlist_info(2), boom)], url="https://www.youtube.com/playlist?list=PL1")
    assert runner.run()[0] == TaskStatus.FAILED


def test_duplicate_titles_get_distinct_files(tmp_path):
    info = playlist_info(2)
    info.entries[1].title = "Lesson 1"
    runner, _, _ = build(tmp_path, [named_engine(info)], url="https://www.youtube.com/playlist?list=PL1")
    assert runner.run()[0] == TaskStatus.COMPLETED
    assert sorted(p.name for p in (tmp_path / "dl" / "Python Course").iterdir()) == ["01 - Lesson 1.mp4", "02 - Lesson 1.mp4"]


def test_watch_link_with_list_downloads_the_whole_playlist(tmp_path):
    engine = named_engine(playlist_info(3))
    runner, history, rec = build(tmp_path, [engine], url="https://www.youtube.com/watch?v=id1&list=PL1&index=1")
    status, _ = runner.run()
    assert status == TaskStatus.COMPLETED
    assert sorted(p.name for p in (tmp_path / "dl" / "Python Course").iterdir()) == [
        "01 - Lesson 1.mp4", "02 - Lesson 2.mp4", "03 - Lesson 3.mp4"]
    assert rec.playlist_calls[-1] == (3, 3, 0)


def test_unreadable_playlist_fails_instead_of_downloading_blindly(tmp_path):
    engine = named_engine(None)
    runner, history, rec = build(tmp_path, [engine], url="https://www.youtube.com/playlist?list=PL1")
    status, msg = runner.run()
    assert status == TaskStatus.FAILED and "playlist" in msg.lower() and not engine.calls


def test_only_selected_playlist_items_are_downloaded(tmp_path):
    engine = named_engine(playlist_info(5))
    runner, history, rec = build(tmp_path, [engine], url="https://www.youtube.com/playlist?list=PL1")
    runner.task.playlist_items = "2,5"
    status, _ = runner.run()
    assert status == TaskStatus.COMPLETED
    assert sorted(p.name for p in (tmp_path / "dl" / "Python Course").iterdir()) == ["02 - Lesson 2.mp4", "05 - Lesson 5.mp4"]
    assert len(engine.calls) == 2 and rec.playlist_calls[-1] == (2, 2, 0)


def test_playlist_selection_survives_restart(tmp_path):
    from core.download_queue import DownloadQueue
    q = DownloadQueue(tmp_path / "q.db")
    t = q.add("https://www.youtube.com/playlist?list=PL1", build_spec("720", "mp4", 0), str(tmp_path), 0, "1,3")
    assert DownloadQueue(tmp_path / "q.db").get(t.id).playlist_items == "1,3"
    other = q.add("https://www.youtube.com/playlist?list=PL1", build_spec("720", "mp4", 0), str(tmp_path), 0, "2")
    assert other.id != t.id                         # different selection is not a duplicate


def test_playlist_item_statuses_are_reported(tmp_path):
    engine = named_engine(playlist_info(3))
    runner, history, rec = build(tmp_path, [engine], url="https://www.youtube.com/playlist?list=PL1")
    events = []
    runner.cb.item = lambda index, status: events.append((index, status))
    (tmp_path / "dl" / "Python Course").mkdir(parents=True)
    (tmp_path / "dl" / "Python Course" / "02 - Lesson 2.mp4").write_bytes(b"x" * 10)
    status, _ = runner.run()
    assert status == TaskStatus.COMPLETED
    assert (1, "Downloading") in events and (1, "Done") in events and (3, "Done") in events
    assert (2, "Skipped (already downloaded)") in events and (2, "Downloading") not in events
