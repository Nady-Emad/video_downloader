"""End-to-end test of DownloadManager + DownloadWorker + TaskRunner using a stubbed Qt core."""
import threading

import pytest

from tests import qt_stub

qt_stub.install()

from config.settings import Settings  # noqa: E402
from core import download_worker  # noqa: E402
from core.download_history import DownloadHistory  # noqa: E402
from core.download_manager import DownloadManager  # noqa: E402
from core.download_queue import DownloadQueue, TaskStatus  # noqa: E402
from core.quality_manager import build_spec  # noqa: E402
from engines.base_engine import EngineError  # noqa: E402
from tests.helpers import FakeEngine, FakeFfmpeg  # noqa: E402


@pytest.fixture
def env(tmp_path, monkeypatch):
    engines = {"engines": []}

    def factory():
        return engines["engines"]

    monkeypatch.setattr(download_worker, "default_engines", factory)
    settings = Settings(tmp_path / "s.json")
    settings.update({"download_dir": str(tmp_path / "dl"), "retry_delay": 0, "max_retries": 0})
    history = DownloadHistory(tmp_path / "h.db")
    queue = DownloadQueue(tmp_path / "h.db")
    manager = DownloadManager(settings, history, queue)
    manager.ffmpeg = FakeFfmpeg()
    idle = threading.Event()
    manager.all_idle.connect(idle.set)
    logs, finished = [], []
    manager.log_message.connect(lambda lvl, msg: logs.append((lvl, msg)))
    manager.task_finished.connect(lambda t, s, m: finished.append((t, s, m)))
    return types_ns(engines=engines, manager=manager, history=history, queue=queue, idle=idle, logs=logs,
                    finished=finished, tmp=tmp_path)


def types_ns(**kw):
    import types
    return types.SimpleNamespace(**kw)


def wait_idle(env, timeout=10):
    assert env.idle.wait(timeout), "queue did not become idle"
    env.idle.clear()


def test_queue_runs_tasks_sequentially_and_records_history(env):
    order = []

    def behavior(request, attempt, cancel):
        order.append(request.url)
        assert env.manager.active_count == 1          # sequential by default
    env.engines["engines"] = [FakeEngine("yt-dlp", behavior, info=None)]
    spec = build_spec("720", "mp4", 192)
    env.manager.pause_queue()
    for i in range(3):
        env.manager.add_task(f"https://example.com/v{i}", spec)
    assert env.manager.add_task("https://example.com/v0", spec) is None      # duplicate rejected
    assert any("already in the queue" in m for _, m in env.logs)
    env.manager.start_queue()
    wait_idle(env)
    assert order == [f"https://example.com/v{i}" for i in range(3)]
    assert [s for _, s, _ in env.finished] == [TaskStatus.COMPLETED] * 3
    assert len(env.history.search()) == 3
    assert env.manager.add_task("not a url", spec) is None


def test_failure_then_retry(env):
    state = {"fail": True}

    def behavior(request, attempt, cancel):
        if state["fail"]:
            raise EngineError("Video unavailable")
    env.engines["engines"] = [FakeEngine("yt-dlp", behavior)]
    task = env.manager.add_task("https://example.com/v", build_spec("best", "mp4", 0))
    env.manager.start_queue()
    wait_idle(env)
    assert env.queue.get(task.id).status == TaskStatus.FAILED
    assert env.queue.get(task.id).error
    state["fail"] = False
    assert env.manager.retry_task(task.id)
    wait_idle(env)
    assert env.queue.get(task.id).status == TaskStatus.COMPLETED


def test_cancel_active_download(env):
    started, release = threading.Event(), threading.Event()

    def behavior(request, attempt, cancel):
        started.set()
        for _ in range(100):
            cancel.raise_if_cancelled()
            release.wait(0.05)
    env.engines["engines"] = [FakeEngine("yt-dlp", behavior)]
    task = env.manager.add_task("https://example.com/v", build_spec("best", "mp4", 0))
    env.manager.start_queue()
    assert started.wait(5)
    assert env.manager.cancel_active() == 1
    wait_idle(env)
    assert env.queue.get(task.id).status == TaskStatus.CANCELLED
    assert not list((env.tmp / "dl").glob("*.mp4"))
    assert not (env.tmp / "dl" / ".vdl_tmp").exists()


def test_cancel_pending_and_remove(env):
    env.engines["engines"] = [FakeEngine("yt-dlp")]
    env.manager.pause_queue()
    spec = build_spec("best", "mp4", 0)
    a = env.manager.add_task("https://example.com/a", spec)
    b = env.manager.add_task("https://example.com/b", spec)
    assert env.manager.cancel_task(a.id)
    assert env.queue.get(a.id).status == TaskStatus.CANCELLED
    assert env.manager.remove_task(b.id)
    assert env.manager.clear_completed() == 1 and env.queue.tasks() == []


def test_recovery_after_restart(env, tmp_path):
    env.engines["engines"] = [FakeEngine("yt-dlp")]
    env.manager.pause_queue()
    env.manager.add_task("https://example.com/a", build_spec("best", "mp4", 0))
    queue2 = DownloadQueue(tmp_path / "h.db")
    manager2 = DownloadManager(env.manager.settings, env.history, queue2)
    assert manager2.recover() == 1 and manager2.paused          # waits for the user to start it
    assert queue2.tasks()[0].status == TaskStatus.PENDING


def test_unexpected_exception_in_worker_is_contained(env):
    class Exploding(FakeEngine):
        def download(self, *a, **k):
            raise RuntimeError("kaboom")
    env.engines["engines"] = [Exploding("yt-dlp")]
    task = env.manager.add_task("https://example.com/v", build_spec("best", "mp4", 0))
    env.manager.start_queue()
    wait_idle(env)
    assert env.queue.get(task.id).status == TaskStatus.FAILED
    assert env.manager.active_count == 0


def test_concurrent_downloads_respect_setting(env):
    peak = {"n": 0}
    lock = threading.Lock()
    active = {"n": 0}

    def behavior(request, attempt, cancel):
        with lock:
            active["n"] += 1
            peak["n"] = max(peak["n"], active["n"])
        threading.Event().wait(0.2)
        with lock:
            active["n"] -= 1
    env.engines["engines"] = [FakeEngine("yt-dlp", behavior)]
    env.manager.settings.update({"max_concurrent": 2})
    env.manager.pause_queue()
    for i in range(4):
        env.manager.add_task(f"https://example.com/c{i}", build_spec("best", "mp4", 0))
    env.manager.start_queue()
    wait_idle(env)
    assert peak["n"] == 2


# ----------------------------------------------------------------------------- pause / resume / network
def _partial_dirs(env):
    root = env.tmp / "dl" / ".vdl_tmp"
    return list(root.iterdir()) if root.exists() else []


def _blocking_behavior(started, finish_event=None):
    """Writes a partial file, then waits until cancelled/paused (or finish_event is set)."""
    def behavior(request, attempt, cancel):
        part = request.work_dir / "media.mp4.part"
        if part.exists() and finish_event is not None and finish_event.is_set():
            part.unlink()                       # resumed run: finish quickly
            return
        part.write_bytes(b"partial")
        started.set()
        for _ in range(200):
            cancel.raise_if_cancelled()
            threading.Event().wait(0.02)
    return behavior


def test_pause_keeps_partial_and_resume_completes(env):
    started, finish = threading.Event(), threading.Event()
    env.engines["engines"] = [FakeEngine("yt-dlp", _blocking_behavior(started, finish), resume=True)]
    task = env.manager.add_task("https://example.com/v", build_spec("best", "mp4", 0))
    env.manager.start_queue()
    assert started.wait(5)
    assert env.manager.pause_task(task.id)
    wait_idle(env)
    assert env.queue.get(task.id).status == TaskStatus.PAUSED
    assert not env.queue.get(task.id).waiting_network
    assert len(_partial_dirs(env)) == 1                                   # progress kept
    assert not env.history.search()                                       # a pause is not a failure

    finish.set()
    assert env.manager.resume_task(task.id)
    wait_idle(env)
    assert env.queue.get(task.id).status == TaskStatus.COMPLETED
    assert not _partial_dirs(env) and len(list((env.tmp / "dl").glob("*.mp4"))) == 1


def test_cancel_paused_task_deletes_partial(env):
    started = threading.Event()
    env.engines["engines"] = [FakeEngine("yt-dlp", _blocking_behavior(started), resume=True)]
    task = env.manager.add_task("https://example.com/v", build_spec("best", "mp4", 0))
    env.manager.start_queue()
    assert started.wait(5)
    env.manager.pause_task(task.id)
    wait_idle(env)
    assert _partial_dirs(env)
    assert env.manager.cancel_task(task.id)
    assert env.queue.get(task.id).status == TaskStatus.CANCELLED and not _partial_dirs(env)


def test_cancel_active_discards_partial(env):
    started = threading.Event()
    env.engines["engines"] = [FakeEngine("yt-dlp", _blocking_behavior(started), resume=True)]
    task = env.manager.add_task("https://example.com/v", build_spec("best", "mp4", 0))
    env.manager.start_queue()
    assert started.wait(5)
    env.manager.cancel_task(task.id)
    wait_idle(env)
    assert env.queue.get(task.id).status == TaskStatus.CANCELLED and not _partial_dirs(env)


def test_network_loss_pauses_then_auto_resumes(env):
    state = {"online": False}

    def behavior(request, attempt, cancel):
        part = request.work_dir / "media.mp4.part"
        if not state["online"]:
            part.write_bytes(b"half")
            raise EngineError("Connection timed out")
        part.unlink(missing_ok=True)
    env.engines["engines"] = [FakeEngine("yt-dlp", behavior, resume=True)]
    task = env.manager.add_task("https://example.com/v", build_spec("best", "mp4", 0))
    env.manager.start_queue()
    wait_idle(env)
    t = env.queue.get(task.id)
    assert t.status == TaskStatus.INTERRUPTED and "Interrupted" in t.error and "connection lost" in t.error
    assert env.manager._net_timer.isActive()                              # watcher armed
    assert not env.history.search() and _partial_dirs(env)

    env.manager._on_probe_result(False)                                   # still offline: nothing happens
    assert env.queue.get(task.id).status == TaskStatus.INTERRUPTED
    state["online"] = True
    env.manager._on_probe_result(True)                                    # internet is back
    wait_idle(env)
    assert env.queue.get(task.id).status == TaskStatus.COMPLETED
    assert not env.manager._net_timer.isActive() and not _partial_dirs(env)
    assert any("network is back" in m.lower() for _, m in env.logs)


def test_auto_resume_can_be_disabled(env):
    env.engines["engines"] = [FakeEngine("yt-dlp", lambda r, a, c: (_ for _ in ()).throw(
        EngineError("Connection timed out")), resume=True)]
    env.manager.settings.update({"auto_resume_network": False})
    env.manager.add_task("https://example.com/v", build_spec("best", "mp4", 0))
    env.manager.start_queue()
    wait_idle(env)
    assert not env.manager._net_timer.isActive()


def test_shutdown_pauses_and_next_launch_resumes(env, tmp_path):
    started, finish = threading.Event(), threading.Event()
    env.engines["engines"] = [FakeEngine("yt-dlp", _blocking_behavior(started, finish), resume=True)]
    task = env.manager.add_task("https://example.com/v", build_spec("best", "mp4", 0))
    env.manager.start_queue()
    assert started.wait(5)
    env.manager.shutdown()
    assert env.queue.get(task.id).status == TaskStatus.PENDING            # will continue next launch
    assert len(_partial_dirs(env)) == 1                                   # partial file survived

    queue2 = DownloadQueue(tmp_path / "h.db")                             # "restart"
    manager2 = DownloadManager(env.manager.settings, env.history, queue2)
    manager2.ffmpeg = FakeFfmpeg()
    idle2 = threading.Event()
    manager2.all_idle.connect(idle2.set)
    assert manager2.recover() == 1 and len(_partial_dirs(env)) == 1       # stale cleanup keeps resumable data
    finish.set()
    manager2.start_queue()
    assert idle2.wait(10)
    assert queue2.get(task.id).status == TaskStatus.COMPLETED and not _partial_dirs(env)


def test_cancel_active_keep_partial_then_delete_flag(env):
    started = threading.Event()
    env.engines["engines"] = [FakeEngine("yt-dlp", _blocking_behavior(started), resume=True)]
    task = env.manager.add_task("https://example.com/v", build_spec("best", "mp4", 0))
    env.manager.start_queue()
    assert started.wait(5)
    env.manager.cancel_task(task.id, keep_partial=True)
    wait_idle(env)
    assert env.queue.get(task.id).status == TaskStatus.CANCELLED
    assert _partial_dirs(env)                                       # partial kept on request
    assert env.manager.retry_task(task.id)                          # and the task can be retried


def test_cancel_active_delete_partial(env):
    started = threading.Event()
    env.engines["engines"] = [FakeEngine("yt-dlp", _blocking_behavior(started), resume=True)]
    task = env.manager.add_task("https://example.com/v", build_spec("best", "mp4", 0))
    env.manager.start_queue()
    assert started.wait(5)
    env.manager.cancel_task(task.id, keep_partial=False)
    wait_idle(env)
    assert env.queue.get(task.id).status == TaskStatus.CANCELLED and not _partial_dirs(env)


def test_cancel_interrupted_task_keep_and_completed_untouched(env):
    env.engines["engines"] = [FakeEngine("yt-dlp", lambda r, a, c: (_ for _ in ()).throw(
        EngineError("Connection timed out")), resume=True)]
    env.manager.settings.update({"auto_resume_network": False})
    done = env.tmp / "dl" / "finished.mp4"
    done.parent.mkdir(exist_ok=True)
    done.write_bytes(b"completed file")
    task = env.manager.add_task("https://example.com/v", build_spec("best", "mp4", 0))
    env.manager.start_queue()
    wait_idle(env)
    assert env.queue.get(task.id).status == TaskStatus.INTERRUPTED
    assert env.manager.cancel_task(task.id, keep_partial=True)
    assert _partial_dirs(env) and done.read_bytes() == b"completed file"
    task2 = env.manager.retry_task(task.id)
    assert task2


def test_interrupted_manual_resume(env):
    state = {"online": False}

    def behavior(request, attempt, cancel):
        part = request.work_dir / "media.mp4.part"
        if not state["online"]:
            part.write_bytes(b"half")
            raise EngineError("Connection timed out")
        assert part.exists()                                        # resumed from the preserved partial
        part.unlink()
    env.engines["engines"] = [FakeEngine("yt-dlp", behavior, resume=True)]
    env.manager.settings.update({"auto_resume_network": False})
    task = env.manager.add_task("https://example.com/v", build_spec("best", "mp4", 0))
    env.manager.start_queue()
    wait_idle(env)
    assert env.queue.get(task.id).status == TaskStatus.INTERRUPTED
    state["online"] = True
    assert env.manager.resume_task(task.id)
    wait_idle(env)
    assert env.queue.get(task.id).status == TaskStatus.COMPLETED
