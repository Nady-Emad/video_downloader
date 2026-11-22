"""Central coordinator between the GUI, the queue and the worker threads."""
from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from config.settings import Settings
from core import cookie_manager, scheduler
from core.diagnostics import ErrorStore
from core.download_history import DownloadHistory
from core.download_queue import DownloadQueue, DuplicateTaskError, QueueTask, TaskStatus
from core.download_worker import DownloadWorker, NetworkProbe
from core.ffmpeg_manager import FfmpegManager
from core.platform_detector import detect_platform
from core.quality_manager import QualitySpec
from utils import file_manager
from utils.logger import get_logger, level_enabled
from utils.validators import validate_url

log = get_logger("manager")


class DownloadManager(QObject):
    queue_changed = pyqtSignal()
    task_updated = pyqtSignal(str)                      # task_id
    log_message = pyqtSignal(str, str)                  # level, message
    progress = pyqtSignal(str, object)                  # task_id, ProgressInfo
    playlist_progress = pyqtSignal(str, int, int, int)  # task_id, done, total, failed
    playlist_item = pyqtSignal(str, int, str)           # task_id, item index, status
    engine_changed = pyqtSignal(str, str)               # task_id, engine
    thumbnail_changed = pyqtSignal(str, str)            # task_id, thumbnail URL
    history_changed = pyqtSignal()
    queue_paused_changed = pyqtSignal(bool)
    task_finished = pyqtSignal(str, str, str)           # task_id, status, message
    all_idle = pyqtSignal()
    error_recorded = pyqtSignal()                       # a new entry was added to the Error Center

    def __init__(self, settings: Settings, history: DownloadHistory, queue: DownloadQueue,
                 parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self.settings = settings
        self.history = history
        self.queue = queue
        self.ffmpeg = FfmpegManager(str(settings.get("ffmpeg_path")))
        self._workers: Dict[str, DownloadWorker] = {}
        self._finishing: List[DownloadWorker] = []   # done but QThread not yet finished: keep alive
        self._paused = False
        self._probe: Optional[NetworkProbe] = None
        self.errors = ErrorStore()
        self._schedule_notified = False
        self._schedule_timer = QTimer(self)          # re-checks the schedule window while tasks are waiting
        self._schedule_timer.setInterval(30_000)
        self._schedule_timer.timeout.connect(self.pump)
        self._schedule_timer.start()
        self._net_timer = QTimer(self)
        self._net_timer.setInterval(15_000)
        self._net_timer.timeout.connect(self._probe_network)

    # ------------------------------------------------------------------ startup / shutdown
    def recover(self) -> int:
        """Called once at startup: clean stale temp folders and report recovered tasks.
        Recovered tasks wait (queue paused) until the user starts the queue."""
        folders = {str(self.settings.get("download_dir"))}
        pending = [t for t in self.queue.tasks() if t.status in (TaskStatus.PENDING, TaskStatus.PAUSED, TaskStatus.INTERRUPTED)]
        folders.update(t.dest_dir for t in self.queue.tasks())
        for folder in folders:
            try:
                removed = file_manager.cleanup_stale_attempts(Path(folder).expanduser())
                if removed:
                    self.emit_log("INFO", f"Removed {removed} leftover temporary download folder(s) in {folder}.")
            except OSError as exc:
                log.warning("Stale cleanup failed for %s: %s", folder, exc)
        if pending and self.settings.get("auto_recover_queue"):
            self._set_paused(False)
            self.emit_log("INFO", f"Recovered {len(pending)} unfinished task(s); resuming automatically.")
        elif pending:
            self._set_paused(True)
            self.emit_log("INFO", f"Recovered {len(pending)} unfinished task(s); partial downloads will resume. "
                                  "Press Start Queue to continue.")
        self.queue_changed.emit()
        return len(pending)

    def shutdown(self, timeout_ms: int = 8000) -> None:
        """Pause running downloads (partial files are kept) and store their state for the next launch."""
        self._set_paused(True)
        self._schedule_timer.stop()
        self._net_timer.stop()
        workers = list(self._workers.values()) + list(self._finishing)
        for worker in self._workers.values():
            worker.request_pause()
        for worker in workers:
            worker.wait(timeout_ms)
        for worker in workers:
            # The event loop is no longer running, so record each worker's outcome directly.
            status, message = worker.result or (TaskStatus.PAUSED, "")
            if status in (TaskStatus.PAUSED, TaskStatus.INTERRUPTED):
                self.queue.set_status(worker.task_id, TaskStatus.PENDING)    # resumes at next launch
            else:
                self.queue.set_status(worker.task_id, status, message)

    def emit_log(self, level: str, message: str) -> None:
        if level_enabled(level):
            self.log_message.emit(level, message)

    def apply_settings(self) -> None:
        self.ffmpeg = FfmpegManager(str(self.settings.get("ffmpeg_path")))
        self.pump()

    # ------------------------------------------------------------------ state
    @property
    def paused(self) -> bool:
        return self._paused

    def _set_paused(self, value: bool) -> None:
        if self._paused != value:
            self._paused = value
            self.queue_paused_changed.emit(value)

    @property
    def active_count(self) -> int:
        return len(self._workers)

    def is_active(self, task_id: str) -> bool:
        return task_id in self._workers

    def active_task_ids(self) -> List[str]:
        return list(self._workers)

    # ------------------------------------------------------------------ adding
    def add_task(self, url: str, quality: QualitySpec, dest_dir: Optional[str] = None,
                 priority: int = 0, playlist_items: str = "") -> Optional[QueueTask]:
        try:
            cleaned = validate_url(url)
        except ValueError as exc:
            self.emit_log("ERROR", f"Invalid URL: {exc}")
            return None
        destination = str(Path(dest_dir or str(self.settings.get("download_dir"))).expanduser())
        try:
            task = self.queue.add(cleaned, quality, destination, priority, playlist_items)
        except DuplicateTaskError as exc:
            self.emit_log("WARNING", f"{exc} ({cleaned})")
            return None
        self.emit_log("INFO", f"Added to queue: {cleaned} [{detect_platform(cleaned)}]")
        self.queue_changed.emit()
        if self.settings.get("auto_process_queue") and not self._paused:
            self.pump()
        return task

    def is_paused(self) -> bool:
        return self._paused

    def start_queue(self) -> None:
        self._set_paused(False)
        self.emit_log("INFO", "Queue started.")
        self.pump()

    def pause_queue(self) -> None:
        """Let running downloads finish but do not start the next task."""
        self._set_paused(True)
        self.emit_log("INFO", "Queue paused. The current download will finish; no new task will start.")

    # ------------------------------------------------------------------ pumping
    def pump(self) -> None:
        if self._paused:
            return
        window = {k: self.settings.get(k) for k in ("schedule_enabled", "schedule_start", "schedule_end")}
        if not scheduler.downloads_allowed(window):
            if not self._schedule_notified:
                self._schedule_notified = True
                self.emit_log("WARNING", "Outside the scheduled download window; queued tasks wait for "
                                      f"{self.settings.get('schedule_start')}.")
            return
        self._schedule_notified = False
        limit = max(1, int(self.settings.get("max_concurrent")))
        while len(self._workers) < limit:
            task = self.queue.next_pending(exclude=set(self._workers))
            if task is None:
                break
            self._start_worker(task)
        if not self._workers and self.queue.next_pending() is None:
            self.all_idle.emit()

    def _start_worker(self, task: QueueTask) -> None:
        self.queue.set_status(task.id, TaskStatus.EXTRACTING)
        self.queue.update_info(task.id, progress=0.0, engine="")
        worker = DownloadWorker(task, self.settings.as_dict(), cookie_manager.from_settings(self.settings),
                                self.ffmpeg, self.history)
        worker.log_message.connect(self._on_worker_log)
        worker.progress.connect(self._on_worker_progress)
        worker.status_changed.connect(self._on_worker_status)
        worker.playlist_progress.connect(self._on_worker_playlist)
        worker.playlist_item.connect(self.playlist_item)      # signal-to-signal
        worker.engine_changed.connect(self._on_worker_engine)
        worker.title_changed.connect(self._on_worker_title)
        worker.thumbnail_changed.connect(self._on_worker_thumbnail)
        worker.history_changed.connect(self.history_changed)
        worker.task_done.connect(self._on_worker_done)
        worker.finished.connect(self._on_thread_finished)   # QThread.finished: safe to release then
        self._workers[task.id] = worker
        self.emit_log("INFO", f"Starting task: {task.url}")
        self.queue_changed.emit()
        worker.start()

    # ------------------------------------------------------------------ worker slots (GUI thread)
    def _on_worker_log(self, task_id: str, level: str, message: str) -> None:
        self.emit_log(level, message)

    def _on_worker_progress(self, task_id: str, info: object) -> None:
        percent = getattr(info, "percent", -1.0)
        if percent >= 0:
            self.queue.update_info(task_id, progress=float(percent))
        self.progress.emit(task_id, info)

    def _on_worker_status(self, task_id: str, status: str, detail: str) -> None:
        task = self.queue.get(task_id)
        if task is not None and task.status in TaskStatus.ACTIVE:
            self.queue.set_status(task_id, status, detail)
            self.task_updated.emit(task_id)
            self.queue_changed.emit()

    def _on_worker_playlist(self, task_id: str, done: int, total: int, failed: int) -> None:
        self.queue.update_info(task_id, playlist_done=done, playlist_total=total)
        self.playlist_progress.emit(task_id, done, total, failed)
        self.task_updated.emit(task_id)

    def _on_worker_engine(self, task_id: str, engine: str) -> None:
        self.queue.update_info(task_id, engine=engine)
        self.engine_changed.emit(task_id, engine)
        self.task_updated.emit(task_id)

    def _on_worker_title(self, task_id: str, title: str) -> None:
        self.queue.update_info(task_id, title=title)
        self.task_updated.emit(task_id)

    def _on_worker_thumbnail(self, task_id: str, url: str) -> None:
        self.queue.update_info(task_id, thumbnail=url)
        self.thumbnail_changed.emit(task_id, url)
        self.task_updated.emit(task_id)

    def _on_worker_done(self, task_id: str, status: str, message: str) -> None:
        self.queue.set_status(task_id, status, message)
        if status == TaskStatus.INTERRUPTED:
            self.emit_log("WARNING", "Connection lost. Progress is saved"
                          + ("; the download resumes automatically when the network is back."
                             if self.settings.get("auto_resume_network") else "; press Resume to continue."))
            self._update_net_timer()
        if status == TaskStatus.COMPLETED:
            self.queue.update_info(task_id, progress=100.0)
        if status == TaskStatus.FAILED and message:
            task = self.queue.get(task_id)
            self.errors.add(task.title if task else "", task.url if task else "", message)
            self.error_recorded.emit()
        worker = self._workers.pop(task_id, None)
        if worker is not None:
            self._finishing.append(worker)   # released in _on_thread_finished, after run() has returned
        self.task_updated.emit(task_id)
        self.queue_changed.emit()
        self.task_finished.emit(task_id, status, message)
        self.pump()

    def _on_thread_finished(self) -> None:
        worker = self.sender()
        if isinstance(worker, DownloadWorker):
            if worker in self._finishing:
                self._finishing.remove(worker)
            worker.deleteLater()

    # ------------------------------------------------------------------ user actions
    def cancel_task(self, task_id: str, keep_partial: bool = False) -> bool:
        """Cancel for good. Partial files are deleted unless keep_partial is True. Finished files are never touched."""
        worker = self._workers.get(task_id)
        if worker is not None:
            self.emit_log("WARNING", "Cancelling the active download...")
            worker.request_cancel(keep_partial)
            return True
        task = self.queue.get(task_id)
        if task and task.status in (TaskStatus.PENDING, TaskStatus.PAUSED, TaskStatus.INTERRUPTED):
            self.queue.set_status(task_id, TaskStatus.CANCELLED)
            if not keep_partial:
                file_manager.cleanup_task_partials(Path(task.dest_dir).expanduser(), task_id)
            self._update_net_timer()
            self.queue_changed.emit()
            return True
        return False

    def pause_task(self, task_id: str) -> bool:
        """Pause: an active download stops and keeps its partial file; a pending one just waits."""
        worker = self._workers.get(task_id)
        if worker is not None:
            self.emit_log("INFO", "Pausing... the progress will be saved.")
            worker.request_pause()
            return True
        ok = self.queue.pause_task(task_id)
        if ok:
            self.queue_changed.emit()
        return ok

    def pause_active(self) -> int:
        ids = list(self._workers)
        for task_id in ids:
            self.pause_task(task_id)
        return len(ids)

    def cancel_active(self) -> int:
        ids = list(self._workers)
        for task_id in ids:
            self.cancel_task(task_id)
        return len(ids)

    def retry_task(self, task_id: str) -> bool:
        if self.queue.retry(task_id):
            self.queue_changed.emit()
            self.pump()
            return True
        return False

    def remove_task(self, task_id: str) -> bool:
        task = self.queue.get(task_id)
        ok = self.queue.remove(task_id)
        if ok and task is not None:
            file_manager.cleanup_task_partials(Path(task.dest_dir).expanduser(), task_id)   # drop saved partials
            self._update_net_timer()
            self.queue_changed.emit()
        return ok

    def clear_completed(self) -> int:
        count = self.queue.clear_completed()
        self.queue_changed.emit()
        return count

    def change_priority(self, task_id: str, delta: int) -> bool:
        task = self.queue.get(task_id)
        if task is None:
            return False
        ok = self.queue.set_priority(task_id, task.priority + delta)
        self.queue_changed.emit()
        return ok

    def resume_task(self, task_id: str) -> bool:
        ok = self.queue.resume_task(task_id)
        if ok:
            self._update_net_timer()
            self.queue_changed.emit()
            self.pump()
        return ok

    def retry_from_history(self, record_id: int) -> Optional[QueueTask]:
        """Re-queue a failed history entry with its original quality settings."""
        import json

        record = self.history.get(record_id)
        if record is None:
            return None
        try:
            spec = QualitySpec.from_dict(json.loads(record.spec_json)) if record.spec_json else QualitySpec()
        except ValueError:
            spec = QualitySpec()
        return self.add_task(record.url, spec, record.dest_dir or None)

    # ------------------------------------------------------------------ network watcher
    def _update_net_timer(self) -> None:
        waiting = any(t.status == TaskStatus.INTERRUPTED for t in self.queue.tasks())
        if waiting and self.settings.get("auto_resume_network"):
            if not self._net_timer.isActive():
                self._net_timer.start()
        else:
            self._net_timer.stop()

    def _probe_network(self) -> None:
        if self._probe is not None and self._probe.isRunning():
            return
        self._probe = NetworkProbe(self)
        self._probe.result.connect(self._on_probe_result)
        self._probe.start()

    def _on_probe_result(self, online: bool) -> None:
        if not online:
            return
        resumed = 0
        for task in self.queue.tasks():
            if task.status == TaskStatus.INTERRUPTED and self.queue.resume_task(task.id):
                resumed += 1
        self._update_net_timer()
        if resumed:
            self.emit_log("SUCCESS", f"The network is back. Resuming {resumed} download(s).")
            self.queue_changed.emit()
            self.pump()
