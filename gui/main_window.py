"""Main application window."""
from __future__ import annotations

from gui.i18n import tr

from pathlib import Path
from typing import Optional

from PyQt6.QtCore import QByteArray, Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtGui import QGuiApplication, QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QApplication, QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMessageBox, QPlainTextEdit,
    QPushButton, QScrollArea, QSplitter, QStackedWidget, QVBoxLayout, QWidget,
)

from config.settings import Settings
from core import cookie_manager
from core import quality_manager as qm
from core.download_history import DownloadHistory
from core.download_manager import DownloadManager
from core.download_queue import TaskStatus
from core.download_worker import InfoWorker, QuickPreviewWorker
from core.platform_detector import detect_platform
from engines.base_engine import MediaInfo, ProgressInfo
import app_info
from core.updater import check_for_update
from gui.dashboard_page import DashboardPage
from gui.diagnostics_page import DiagnosticsPage
from gui.history_widget import HistoryWidget
from gui.library_page import LibraryPage
from gui.sidebar import Sidebar
from gui.styles import build_qss
from gui.tray import TrayController
from gui.queue_widget import QueueWidget
from gui.settings_dialog import SettingsPage
from gui.thumbnails import ThumbnailCache
from gui.widgets import (
    THUMB_H, THUMB_W, ConsoleWidget, InfoPanel, PlaylistPanel, PreviewCard, ProgressPanel, QualityPanel, ask_cancel_mode,
)
from utils import file_manager
from utils.logger import set_level
from utils.system_info import dependency_status, js_runtime_found
from utils.validators import extract_urls


class _UpdateThread(QThread):
    """Asks the configured release address for a newer version (never blocks the GUI)."""

    found = pyqtSignal(str, str)         # version, page URL

    def __init__(self, endpoint: str, parent=None) -> None:
        super().__init__(parent)
        self.endpoint = endpoint

    def run(self) -> None:
        try:
            info = check_for_update(self.endpoint)
        except Exception:                # offline, bad address or bad JSON: silently no update
            return
        if info is not None:
            self.found.emit(info.version, info.url)


class MainWindow(QMainWindow):
    PAGES = ["Dashboard", "Downloader", "Download Queue", "Media Library", "Download History", "Diagnostics",
             "Settings"]
    DASHBOARD, DOWNLOADER, QUEUE, LIBRARY, HISTORY, DIAGNOSTICS, SETTINGS = range(7)

    def __init__(self, settings: Settings, history: DownloadHistory, manager: DownloadManager) -> None:
        super().__init__()
        self.settings = settings
        self.history = history
        self.manager = manager
        self._info: Optional[MediaInfo] = None
        self._info_workers: list = []
        self._analysis_url = ""
        self._quick_thumbnail = ""
        self._current_task: Optional[str] = None
        self._info_platform = ""
        self.thumbs = ThumbnailCache(self)

        self.setWindowTitle(app_info.APP_NAME)
        self.setAcceptDrops(True)
        self._last_clipboard = ""
        self._previous_length = 0
        self._update_thread = None
        self.resize(1280, 860)
        self.setMinimumSize(1080, 640)

        self._build_ui()
        self._connect_signals()
        self.tray = TrayController(self, self.manager, self.settings)
        self._apply_settings_to_ui()
        self._report_environment()
        self.manager.recover()
        QTimer.singleShot(1500, self._check_for_updates)

    # ------------------------------------------------------------------ UI construction
    def _build_ui(self) -> None:
        # ---- header
        title = QLabel(tr("Downloader"))
        title.setObjectName("title")
        self.console_toggle = QPushButton(tr("Console"))
        self.console_toggle.setObjectName("ghost")
        self.console_toggle.setCheckable(True)
        self.console_toggle.setChecked(False)
        self.console_toggle.setToolTip("Show or hide the live console (Ctrl+J)")
        header = QHBoxLayout()
        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(self.console_toggle)

        # ---- download tab
        self.url_edit = QPlainTextEdit()
        self.url_edit.setPlaceholderText(tr("Paste a video or playlist link - it is analysed automatically..."))
        self.url_edit.setFixedHeight(76)
        self.paste_button = QPushButton(tr("Paste URL"))
        side = QVBoxLayout()
        side.addWidget(self.paste_button)
        side.addStretch(1)
        url_row = QHBoxLayout()
        url_row.addWidget(self.url_edit, 1)
        url_row.addLayout(side)

        self.quality_panel = QualityPanel()
        self.info_panel = InfoPanel()
        self.preview_card = PreviewCard()
        self.preview_card.setMinimumHeight(THUMB_H + 28)
        self.playlist_panel = PlaylistPanel(self.thumbs)

        self.dest_edit = QLineEdit()
        self.dest_edit.setReadOnly(True)
        self.browse_button = QPushButton(tr("Browse..."))
        dest_row = QHBoxLayout()
        dest_row.addWidget(QLabel(tr("Save to")))
        dest_row.addWidget(self.dest_edit, 1)
        dest_row.addWidget(self.browse_button)

        self.start_button = QPushButton(tr("Start Download"))
        self.start_button.setObjectName("primary")
        self.add_button = QPushButton(tr("Add to Queue"))
        action_row = QHBoxLayout()
        action_row.addWidget(self.start_button)
        action_row.addWidget(self.add_button)
        action_row.addStretch(1)

        self.progress_panel = ProgressPanel()

        left = QVBoxLayout()
        left.setSpacing(12)
        left.addLayout(url_row)
        left.addWidget(self.quality_panel)
        left.addLayout(dest_row)
        left.addLayout(action_row)
        left.addWidget(self.progress_panel)
        left.addStretch(1)
        right = QVBoxLayout()
        right.setSpacing(12)
        right.addWidget(self.preview_card)
        right.addWidget(self.info_panel)
        right.addWidget(self.playlist_panel, 10)
        right.addStretch(1)
        download_tab = QWidget()
        d_layout = QHBoxLayout(download_tab)
        d_layout.setSpacing(16)
        d_layout.addLayout(left, 5)
        d_layout.addLayout(right, 4)
        d_layout.setContentsMargins(4, 12, 4, 4)
        download_tab.setMinimumHeight(620)
        download_tab.setMinimumWidth(900)

        self.queue_widget = QueueWidget(self.manager, self.thumbs)
        self.history_widget = HistoryWidget(self.history, self.manager, self.thumbs)
        self.settings_page = SettingsPage(self.settings)

        self.dashboard_page = DashboardPage(self.manager, self.history, self.settings, self.thumbs)
        self.library_page = LibraryPage(self.settings, self.manager, self.thumbs)
        self.diagnostics_page = DiagnosticsPage(self.settings, self.manager)

        # Scroll instead of squeezing widgets when the window is short.
        download_scroll = QScrollArea()
        download_scroll.setWidgetResizable(True)
        download_scroll.setFrameShape(QFrame.Shape.NoFrame)
        download_scroll.setWidget(download_tab)
        self.download_page = download_scroll
        self.tabs = QStackedWidget()                       # one page per sidebar entry (same order as PAGES)
        for page in (self.dashboard_page, download_scroll, self.queue_widget, self.library_page,
                     self.history_widget, self.diagnostics_page, self.settings_page):
            self.tabs.addWidget(page)
        self.sidebar = Sidebar(self.PAGES)

        # ---- console
        self.console = ConsoleWidget()
        console_label = QLabel(tr("Live console"))
        console_label.setObjectName("muted")
        self.console_box = console_box = QWidget()
        c_layout = QVBoxLayout(console_box)
        c_layout.setContentsMargins(0, 4, 0, 0)
        c_layout.addWidget(console_label)
        c_layout.addWidget(self.console)

        self.page_title = title
        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.addWidget(self.tabs)
        splitter.addWidget(console_box)
        splitter.setStretchFactor(0, 4)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([700, 160])
        self.console_box.setVisible(False)          # opens with the Console button, or by itself on an error

        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(16, 12, 16, 8)
        layout.setSpacing(8)
        layout.addLayout(header)
        layout.addWidget(splitter, 1)
        central = QWidget()
        outer = QHBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(self.sidebar)
        outer.addWidget(content, 1)
        self.setCentralWidget(central)
        self.tabs.setCurrentWidget(download_scroll)
        self._on_page_changed(self.tabs.currentIndex())

        self.ffmpeg_label = QLabel("")
        self.queue_label = QLabel("")
        self.statusBar().addWidget(self.ffmpeg_label, 1)
        self.statusBar().addPermanentWidget(self.queue_label)

    def _connect_signals(self) -> None:
        self.console_toggle.toggled.connect(lambda on: self.console_box.setVisible(on))
        self.settings_page.saved.connect(self._on_settings_saved)
        self.progress_panel.pause_requested.connect(self._pause_current)
        self.progress_panel.resume_requested.connect(self._resume_current)
        self.progress_panel.cancel_requested.connect(self._cancel_download)
        self.thumbs.ready.connect(self._on_thumbnail_ready)
        self.thumbs.ready.connect(lambda _u: self.playlist_panel.refresh_icons())
        self.manager.thumbnail_changed.connect(self._on_task_thumbnail)
        QShortcut(QKeySequence("Ctrl+Return"), self, activated=self._start_download)
        QShortcut(QKeySequence("Ctrl+L"), self, activated=self.url_edit.setFocus)
        QShortcut(QKeySequence("Ctrl+,"), self, activated=lambda: self.tabs.setCurrentWidget(self.settings_page))
        for number in range(len(self.PAGES)):
            QShortcut(QKeySequence(f"Ctrl+{number + 1}"), self,
                      activated=lambda n=number: self.tabs.setCurrentIndex(n))
        self.sidebar.page_selected.connect(self.tabs.setCurrentIndex)
        self.tabs.currentChanged.connect(self._on_page_changed)
        self.dashboard_page.open_downloader.connect(lambda: self.tabs.setCurrentIndex(self.DOWNLOADER))
        self.dashboard_page.open_settings.connect(lambda: self.tabs.setCurrentIndex(self.SETTINGS))
        QGuiApplication.clipboard().dataChanged.connect(self._on_clipboard_changed)
        QShortcut(QKeySequence("Ctrl+J"), self, activated=self.console_toggle.toggle)
        self.paste_button.clicked.connect(self._paste_url)
        self._analyze_timer = QTimer(self)
        self._analyze_timer.setSingleShot(True)
        self._analyze_timer.setInterval(600)
        self._elapsed_timer = QTimer(self)          # shows how long the analysis has been running
        self._elapsed_timer.setInterval(1000)
        self._elapsed_timer.timeout.connect(self._tick_analysis)
        self._analysis_seconds = 0
        self._info_cache: dict = {}
        self._analyze_timer.timeout.connect(self._analyze)
        self.url_edit.textChanged.connect(self._on_url_text_changed)
        self.browse_button.clicked.connect(self._browse_destination)
        self.start_button.clicked.connect(self._start_download)
        self.add_button.clicked.connect(self._add_to_queue)
        self.quality_panel.changed.connect(self._refresh_summary)

        m = self.manager
        m.log_message.connect(self.console.append_message)
        m.log_message.connect(self._open_console_on_error)
        m.progress.connect(self._on_progress)
        m.playlist_progress.connect(self._on_playlist_progress)
        m.playlist_item.connect(self._on_playlist_item)
        m.engine_changed.connect(self._on_engine_changed)
        m.task_updated.connect(self._on_task_updated)
        m.task_finished.connect(self._on_task_finished)
        m.task_updated.connect(lambda _id: self._update_cancel_button())
        m.queue_changed.connect(self._update_status_bar)
        m.queue_changed.connect(self._update_cancel_button)
        m.queue_changed.connect(self._update_tab_badge)

    def _apply_settings_to_ui(self) -> None:
        s = self.settings
        self.dest_edit.setText(str(s.get("download_dir")))
        self.quality_panel.set_defaults(str(s.get("default_video_quality")), str(s.get("default_container")),
                                        int(s.get("default_audio_bitrate")))
        self.quality_panel.set_codec_options(str(s.get("preferred_codec")), bool(s.get("compat_mode")))
        self.quality_panel.set_ffmpeg_available(self.manager.ffmpeg.available)
        self.quality_panel.set_preset(str(s.get("quality_preset")))
        geometry = str(s.get("window_geometry"))
        if geometry:
            self.restoreGeometry(QByteArray.fromBase64(geometry.encode("ascii")))
        self._update_status_bar()

    def _report_environment(self) -> None:
        status = dependency_status()
        for name, present in status.items():
            if name != "PyQt6" and not present:
                self.console.append_message(
                    "WARNING", f"{name} is not installed; the related engine is disabled (pip install {name}).")
        if not js_runtime_found():
            self.console.append_message(
                "WARNING", "No JavaScript runtime (Deno) found. YouTube downloads may miss formats or fail. "
                           "Install it with: winget install DenoLand.Deno  (then restart the app).")
        if self.manager.ffmpeg.available:
            self.console.append_message("INFO", self.manager.ffmpeg.status_text())
        else:
            self.console.append_message("WARNING", self.manager.ffmpeg.status_text())
            for line in self.manager.ffmpeg.limitations():
                self.console.append_message("WARNING", line)

    # ------------------------------------------------------------------ helpers
    def _urls(self) -> list:
        text = self.url_edit.toPlainText()
        urls = extract_urls(text)
        if not urls:
            urls = [line.strip() for line in text.splitlines() if line.strip()]   # let validation explain
        return urls

    def _destination(self) -> str:
        return self.dest_edit.text().strip() or str(self.settings.get("download_dir"))

    def _check_destination(self) -> bool:
        try:
            file_manager.ensure_dir(Path(self._destination()))
            return True
        except file_manager.FileError as exc:
            QMessageBox.warning(self, "Destination folder", str(exc))
            return False

    # ------------------------------------------------------------------ URL / analysis
    def _paste_url(self) -> None:
        text = QGuiApplication.clipboard().text()
        urls = extract_urls(text)
        if not urls:
            self.console.append_message("WARNING", "The clipboard does not contain a URL.")
            return
        existing = self.url_edit.toPlainText().strip()
        merged = [u for u in extract_urls(existing)]
        for url in urls:
            if url not in merged:
                merged.append(url)
        self.url_edit.setPlainText("\n".join(merged))
        self.console.append_message("INFO", f"Pasted {len(urls)} URL(s).")

    def _on_url_text_changed(self) -> None:
        """Re-analyse shortly after the text stops changing. A pasted link (big jump) starts almost at once."""
        text = self.url_edit.toPlainText()
        pasted = len(text) - self._previous_length > 8
        self._previous_length = len(text)
        self._analyze_timer.start(120 if pasted else 600)

    def _tick_analysis(self) -> None:
        self._analysis_seconds += 1
        if self._info is None and self._analysis_url:
            self.info_panel.show_text("title", f"{tr('Analyzing...')} {self._analysis_seconds}s")

    def _stop_analysis_timer(self) -> None:
        self._elapsed_timer.stop()

    def _analyze(self) -> None:
        urls = self._urls()
        url = urls[0] if urls else ""
        if url == self._analysis_url:
            return
        self._analysis_url = url            # results for any other URL that arrive later are ignored
        self._info = None
        self._elapsed_timer.stop()
        if not url or not extract_urls(url):
            self.info_panel.clear()
            self.preview_card.clear()
            self.playlist_panel.clear()
            self.quality_panel.apply_formats(None)
            return
        self.info_panel.setVisible(True)
        self.info_panel.clear()
        self.preview_card.clear("Loading preview...")
        self.playlist_panel.clear()
        self._info_platform = detect_platform(url)
        cached = self._info_cache.get(url)
        if cached is not None:                          # analysed before: instant
            self._on_info_ready(url, cached)
            return
        self._analysis_seconds = 0
        self.info_panel.show_text("title", tr("Analyzing..."))
        self._elapsed_timer.start()
        self.console.append_message("INFO", f"Analyzing {url} [{self._info_platform}]")
        quick = QuickPreviewWorker(url, self)           # instant title + thumbnail for YouTube
        quick.preview_ready.connect(self._on_quick_preview)
        quick.finished.connect(self._on_worker_finished)
        self._info_workers.append(quick)
        quick.start()
        worker = InfoWorker(url, cookie_manager.from_settings(self.settings), self)
        worker.info_ready.connect(self._on_info_ready)
        worker.info_failed.connect(self._on_info_failed)
        worker.log_message.connect(self.console.append_message)
        worker.finished.connect(self._on_worker_finished)
        self._info_workers.append(worker)
        worker.start()

    def _on_quick_preview(self, url: str, title: str, thumbnail: str) -> None:
        if url != self._analysis_url or self._info is not None:
            return
        if title:
            self.preview_card.title.setText(title)
        self._quick_thumbnail = thumbnail
        pixmap = self.thumbs.pixmap(thumbnail, THUMB_W, THUMB_H, 10)
        if pixmap is not None:
            self.preview_card.set_pixmap(pixmap)

    def _open_console_on_error(self, level: str, _message: str) -> None:
        if level.upper() == "ERROR" and not self.console_toggle.isChecked():
            self.console_toggle.setChecked(True)

    def _on_info_ready(self, url: str, info: object) -> None:
        if not isinstance(info, MediaInfo) or url != self._analysis_url:
            return                                      # stale result for a link that was replaced
        self._info = info
        self._stop_analysis_timer()
        if len(self._info_cache) >= 32:
            self._info_cache.pop(next(iter(self._info_cache)))      # keep the cache small
        self._info_cache[url] = info
        self.preview_card.show_info(info, self._info_platform)
        self._update_preview_thumbnail()
        if info.is_playlist:
            available = sum(1 for e in info.entries if e.available)
            self.info_panel.setVisible(False)           # the playlist list gets the space instead
            self.info_panel.clear()
            self.info_panel.show_text("title", f"Playlist: {info.title or 'Untitled'}")
            self.info_panel.show_text("resolutions", f"{len(info.entries)} items ({available} available)")
            self.info_panel.show_text("selected", "Chosen per item at download time")
            self.info_panel.notes.setText(
                "The whole playlist will be downloaded into a subfolder named after it, prefixed with each item's index.")
            self.quality_panel.apply_formats(None)
            self.preview_card.show_playlist_details(available, len(info.entries))
            self.playlist_panel.set_entries(info.entries)
            self.console.append_message("INFO", f"Playlist found: {info.title} ({len(info.entries)} items).")
        else:
            self.info_panel.setVisible(True)
            self.quality_panel.apply_formats(info)
            self._refresh_summary()
            self.console.append_message("SUCCESS", f"Formats loaded: {info.title or url}")

    def _on_info_failed(self, url: str, message: str) -> None:
        if url != self._analysis_url:
            return
        self._info = None
        self._stop_analysis_timer()
        self.info_panel.clear()
        self.info_panel.show_text("title", "Could not analyze this URL")
        self.info_panel.notes.setText(message)
        self.preview_card.title.setText("Could not analyze this URL")
        self.quality_panel.apply_formats(None)
        self.console.append_message("ERROR", f"Analysis failed: {message}")

    def _on_worker_finished(self) -> None:
        worker = self.sender()
        if worker in self._info_workers:
            self._info_workers.remove(worker)
        if worker is not None:
            worker.deleteLater()

    def _refresh_summary(self) -> None:
        spec = self.quality_panel.current_spec()
        ffmpeg_ok = self.manager.ffmpeg.available
        if self._info is None or self._info.is_playlist:
            return
        summary = qm.summarize_selection(self._info, spec, ffmpeg_ok)
        self.info_panel.show_summary(self._info.title, summary, qm.describe_ffmpeg_requirement(spec, ffmpeg_ok))
        self.preview_card.show_details(summary)

    def _update_preview_thumbnail(self) -> None:
        if self._info is None or not self._info.thumbnail:
            return
        pixmap = self.thumbs.pixmap(self._info.thumbnail, THUMB_W, THUMB_H, 10)   # async: None until loaded
        if pixmap is not None:
            self.preview_card.set_pixmap(pixmap)

    def _on_thumbnail_ready(self, url: str) -> None:
        if self._info is not None and url == self._info.thumbnail:
            self._update_preview_thumbnail()
        elif self._info is None and url == self._quick_thumbnail:
            pixmap = self.thumbs.pixmap(url, THUMB_W, THUMB_H, 10)
            if pixmap is not None:
                self.preview_card.set_pixmap(pixmap)

    def _on_task_thumbnail(self, task_id: str, url: str) -> None:
        self.thumbs.request(url)             # queue/history rows redraw when it is ready

    # ------------------------------------------------------------------ actions
    def _browse_destination(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Select destination folder", self._destination())
        if path:
            self.dest_edit.setText(path)
            self.settings.update({"download_dir": path})

    def _queue_urls(self, priority: int) -> int:
        urls = self._urls()
        if not urls:
            self.console.append_message("WARNING", "Enter at least one URL.")
            return 0
        if not self._check_destination():
            return 0
        spec = self.quality_panel.current_spec()
        items = ""
        if self._info is not None and self._info.is_playlist and self._analysis_url == urls[0]:
            picked = self.playlist_panel.selected_indices()
            if not picked:
                self.console.append_message("WARNING", "Select at least one playlist item.")
                return 0
            if not self.playlist_panel.all_selected():
                items = ",".join(str(i) for i in picked)
        added = 0
        for position, url in enumerate(urls):
            if self.manager.add_task(url, spec, self._destination(), priority, items if position == 0 else ""):
                added += 1
        return added

    def _start_download(self) -> None:
        added = self._queue_urls(priority=10)
        if added:
            self.manager.start_queue()
            self.progress_panel.reset()
            self.url_edit.clear()
        elif self.manager.queue.next_pending() is not None:
            self.manager.start_queue()

    def _add_to_queue(self) -> None:
        if self._queue_urls(priority=0):
            self.url_edit.clear()
            self.tabs.setCurrentIndex(self.QUEUE)

    def _active_id(self) -> Optional[str]:
        ids = self.manager.active_task_ids()
        if self._current_task in ids:
            return self._current_task
        return ids[0] if ids else self._current_task

    def _pause_current(self) -> None:
        task_id = self._active_id()
        if task_id:
            self.manager.pause_task(task_id)

    def _resume_current(self) -> None:
        task_id = self._current_task
        if task_id and self.manager.resume_task(task_id):
            self.manager.start_queue()

    def _cancel_download(self) -> None:
        task_id = self._active_id()
        if not task_id:
            self.console.append_message("INFO", "There is no download to cancel.")
            return
        keep = ask_cancel_mode(self)
        if keep is not None:
            self.manager.cancel_task(task_id, keep_partial=keep)

    # ------------------------------------------------------------------ manager signals
    def _on_progress(self, task_id: str, info: object) -> None:
        if not isinstance(info, ProgressInfo):
            return
        if task_id != self._current_task:
            self._current_task = task_id
        self.progress_panel.update_progress(info)
        self._update_cancel_button()
        self.queue_widget.set_progress(task_id, info.percent)

    def _on_playlist_progress(self, task_id: str, done: int, total: int, failed: int) -> None:
        self.progress_panel.set_playlist(done, total, failed)

    def _on_playlist_item(self, task_id: str, index: int, status: str) -> None:
        if task_id == self._current_task and self._info is not None and self._info.is_playlist:
            self.playlist_panel.set_item_status(index, status)

    def _on_engine_changed(self, task_id: str, engine: str) -> None:
        self.progress_panel.set_engine(engine)

    def _on_task_updated(self, task_id: str) -> None:
        task = self.manager.queue.get(task_id)
        if task is not None and task.status in TaskStatus.ACTIVE:
            self._current_task = task_id
            if task.title:
                self.progress_panel.set_title(task.title)

    def _on_task_finished(self, task_id: str, status: str, message: str) -> None:
        if status == TaskStatus.COMPLETED:
            self.progress_panel.bar.setRange(0, 1000)
            self.progress_panel.bar.setValue(1000)
            self.progress_panel.stage_label.setText(message or "Completed")
        elif status == TaskStatus.FAILED:
            self.progress_panel.reset()
            self.progress_panel.stage_label.setText(f"Failed: {message}")
        elif status == TaskStatus.INTERRUPTED:
            self.progress_panel.stage_label.setText(message or "Interrupted - will resume when the network is back")
        else:
            self.progress_panel.reset()
            self.progress_panel.stage_label.setText(status)
        if self.manager.active_count == 0:
            self.progress_panel.hide_playlist()
        self._update_cancel_button()

    def _update_cancel_button(self) -> None:
        """Enable Pause/Resume/Cancel for the task shown in the progress panel."""
        task = self.manager.queue.get(self._current_task) if self._current_task else None
        status = task.status if task else ""
        can_pause = status in TaskStatus.ACTIVE
        can_resume = status in (TaskStatus.PAUSED, TaskStatus.INTERRUPTED)
        self.progress_panel.set_controls(can_pause, can_resume, can_pause or can_resume or status == TaskStatus.PENDING)

    def _update_tab_badge(self) -> None:
        open_tasks = sum(1 for t in self.manager.queue.tasks() if t.status in TaskStatus.OPEN)
        self.sidebar.set_badge(self.QUEUE, open_tasks)

    def _update_status_bar(self) -> None:
        self.ffmpeg_label.setText(self.manager.ffmpeg.status_text())
        pending = self.manager.queue.count(TaskStatus.PENDING)
        state = "paused" if self.manager.paused else "running"
        self.queue_label.setText(f"Queue {state}: {self.manager.active_count} active, {pending} pending")

    # ------------------------------------------------------------------ settings / lifecycle
    def _on_settings_saved(self) -> None:
        set_level(str(self.settings.get("log_level")))
        self.manager.apply_settings()
        self.dest_edit.setText(str(self.settings.get("download_dir")))
        self.quality_panel.set_ffmpeg_available(self.manager.ffmpeg.available)
        self.quality_panel.set_defaults(str(self.settings.get("default_video_quality")),
                                        str(self.settings.get("default_container")),
                                        int(self.settings.get("default_audio_bitrate")))
        self.quality_panel.set_codec_options(str(self.settings.get("preferred_codec")),
                                             bool(self.settings.get("compat_mode")))
        QApplication.instance().setStyleSheet(build_qss(str(self.settings.get("theme"))))
        self.library_page.reload()
        self.diagnostics_page.refresh_dependencies()
        self.dashboard_page.refresh()
        self._update_status_bar()
        self.console.append_message("SUCCESS", "Settings saved.")

    # ------------------------------------------------------------------ page switching, drag & drop, clipboard
    def _on_page_changed(self, index: int) -> None:
        self.sidebar.set_current(index)
        self.page_title.setText(tr(self.PAGES[index]))
        if index == self.DASHBOARD:
            self.dashboard_page.refresh()

    @staticmethod
    def _dropped_text(mime) -> str:
        if mime.hasUrls():
            return "\n".join(u.toString() for u in mime.urls() if u.scheme() in ("http", "https"))
        return mime.text() if mime.hasText() else ""

    def dragEnterEvent(self, event) -> None:  # noqa: N802
        if extract_urls(self._dropped_text(event.mimeData())):
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:  # noqa: N802
        urls = extract_urls(self._dropped_text(event.mimeData()))
        if urls:
            self.url_edit.setPlainText("\n".join(urls))
            self.tabs.setCurrentIndex(self.DOWNLOADER)
            event.acceptProposedAction()

    def _on_clipboard_changed(self) -> None:
        """Optional clipboard monitor: offers a copied link, never overwrites text the user is working on."""
        if not self.settings.get("clipboard_monitor"):
            return
        text = QGuiApplication.clipboard().text().strip()
        urls = extract_urls(text) if 0 < len(text) < 2000 else []
        if not urls or text == self._last_clipboard or self.url_edit.toPlainText().strip():
            return
        self._last_clipboard = text
        self.url_edit.setPlainText(urls[0])
        self.tabs.setCurrentIndex(self.DOWNLOADER)
        self.statusBar().showMessage(tr("Link detected in the clipboard"), 5000)

    # ------------------------------------------------------------------ updates
    def _check_for_updates(self) -> None:
        endpoint = str(self.settings.get("update_url")).strip()
        if not (self.settings.get("check_updates") and endpoint):
            return
        self._update_thread = _UpdateThread(endpoint, self)
        self._update_thread.found.connect(self.dashboard_page.show_update)
        self._update_thread.finished.connect(self._update_thread.deleteLater)
        self._update_thread.start()

    # ------------------------------------------------------------------ minimise / close
    def changeEvent(self, event) -> None:  # noqa: N802
        super().changeEvent(event)
        tray = getattr(self, "tray", None)
        if event.type() == event.Type.WindowStateChange and self.isMinimized() and tray and tray.should_hide_on_minimize():
            QTimer.singleShot(0, self.hide)

    def closeEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        if self.tray.should_hide_on_close():
            event.ignore()
            self.hide()
            self.tray.hide_notice()
            return
        if self.manager.active_count:
            answer = QMessageBox.question(
                self, "Quit", "Downloads are in progress. Cancel them and quit?\n"
                              "Pending tasks are kept and restored next time.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
        for worker in list(self._info_workers):
            worker.wait(3000)
        self.manager.shutdown()
        self.library_page.shutdown()
        self.thumbs.shutdown()
        if self._update_thread is not None and self._update_thread.isRunning():
            self._update_thread.wait(3000)
        self.settings.update({"window_geometry": bytes(self.saveGeometry().toBase64()).decode("ascii")})
        event.accept()
        QTimer.singleShot(0, QApplication.quit)     # the app does not auto-quit: hiding to the tray must not end it
