"""Error Center and diagnostics: readable errors, technical details on request, copy/export, logs, dependency checks."""
from __future__ import annotations

from gui.i18n import tr

import shutil
from pathlib import Path
from typing import List, Optional

from PyQt6.QtCore import QThread, Qt, pyqtSignal
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtWidgets import (
    QCheckBox, QFileDialog, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMessageBox, QPlainTextEdit,
    QPushButton, QSplitter, QTabWidget, QVBoxLayout, QWidget,
)

import app_info
from config.settings import Settings
from core import diagnostics, engine_updater
from core.download_manager import DownloadManager
from utils import system_info
from utils.logger import redact


class _EngineUpdateThread(QThread):
    finished_update = pyqtSignal(bool, str)

    def run(self) -> None:
        ok, text = engine_updater.update_engines()
        self.finished_update.emit(ok, text)


class DiagnosticsPage(QWidget):
    def __init__(self, settings: Settings, manager: DownloadManager, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.settings, self.manager = settings, manager
        self._entries: List[diagnostics.ErrorEntry] = []

        title = QLabel(tr("Diagnostics"))
        title.setObjectName("title")

        # ---- errors tab
        self.error_list = QListWidget()
        self.detail = QPlainTextEdit()
        self.detail.setReadOnly(True)
        self.detail.setObjectName("console")
        self.details_check = QCheckBox(tr("Show technical details"))
        self.copy_button = QPushButton(tr("Copy details"))
        self.clear_button = QPushButton(tr("Clear list"))
        self.empty = QLabel(tr("No errors. Failed downloads will be explained here."))
        self.empty.setObjectName("emptyHint")
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        buttons = QHBoxLayout()
        buttons.addWidget(self.details_check)
        buttons.addStretch(1)
        buttons.addWidget(self.copy_button)
        buttons.addWidget(self.clear_button)
        right = QWidget()
        r_layout = QVBoxLayout(right)
        r_layout.setContentsMargins(0, 0, 0, 0)
        r_layout.addWidget(self.detail, 1)
        r_layout.addLayout(buttons)
        split = QSplitter(Qt.Orientation.Horizontal)
        split.addWidget(self.error_list)
        split.addWidget(right)
        split.setSizes([380, 520])
        errors_tab = QWidget()
        e_layout = QVBoxLayout(errors_tab)
        e_layout.addWidget(self.empty)
        e_layout.addWidget(split, 1)

        # ---- dependencies tab
        self.deps_view = QPlainTextEdit()
        self.deps_view.setReadOnly(True)
        self.deps_view.setObjectName("console")
        self.recheck_button = QPushButton(tr("Check again"))
        deps_tab = QWidget()
        d_layout = QVBoxLayout(deps_tab)
        d_layout.addWidget(self.deps_view, 1)
        self.update_button = QPushButton(tr("Update download engines"))
        self.update_button.setObjectName("primary")
        self.update_button.setToolTip(tr("Fixes errors such as 'Unexpected response from webpage request'."))
        buttons_row = QHBoxLayout()
        buttons_row.addWidget(self.recheck_button)
        buttons_row.addWidget(self.update_button)
        buttons_row.addStretch(1)
        d_layout.addLayout(buttons_row)
        self._update_thread: Optional[_EngineUpdateThread] = None

        # ---- log tab
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setObjectName("console")
        self.reload_log_button = QPushButton(tr("Reload log"))
        self.open_log_button = QPushButton(tr("Open log folder"))
        log_row = QHBoxLayout()
        log_row.addWidget(self.reload_log_button)
        log_row.addWidget(self.open_log_button)
        log_row.addStretch(1)
        log_tab = QWidget()
        l_layout = QVBoxLayout(log_tab)
        l_layout.addWidget(self.log_view, 1)
        l_layout.addLayout(log_row)

        self.tabs = QTabWidget()
        self.tabs.addTab(errors_tab, tr("Errors"))
        self.tabs.addTab(deps_tab, tr("Dependencies"))
        self.tabs.addTab(log_tab, tr("Application log"))

        self.export_button = QPushButton(tr("Export diagnostic report..."))
        self.export_button.setObjectName("primary")
        header = QHBoxLayout()
        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(self.export_button)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addLayout(header)
        layout.addWidget(self.tabs, 1)

        self.error_list.currentRowChanged.connect(self._show_entry)
        self.details_check.toggled.connect(lambda _c: self._show_entry(self.error_list.currentRow()))
        self.copy_button.clicked.connect(self._copy)
        self.clear_button.clicked.connect(self._clear)
        self.recheck_button.clicked.connect(self.refresh_dependencies)
        self.update_button.clicked.connect(self.update_engines)
        self.reload_log_button.clicked.connect(self.reload_log)
        self.open_log_button.clicked.connect(self._open_log_folder)
        self.export_button.clicked.connect(self._export)
        manager.error_recorded.connect(self.refresh_errors)
        self.refresh_errors()
        self.refresh_dependencies()
        self.reload_log()

    # ------------------------------------------------------------------ errors
    def refresh_errors(self) -> None:
        self._entries = self.manager.errors.items()
        self.error_list.blockSignals(True)
        self.error_list.clear()
        for entry in self._entries:
            item = QListWidgetItem(f"{entry.time}   {entry.category}\n{entry.title or entry.url}")
            item.setToolTip(entry.message)
            self.error_list.addItem(item)
        self.error_list.blockSignals(False)
        self.empty.setVisible(not self._entries)
        if self._entries:
            self.error_list.setCurrentRow(0)
            self._show_entry(0)
        else:
            self.detail.clear()

    def _show_entry(self, row: int) -> None:
        if 0 <= row < len(self._entries):
            self.detail.setPlainText(self._entries[row].as_text(with_details=self.details_check.isChecked()))

    def _copy(self) -> None:
        text = self.detail.toPlainText()
        if text:
            QGuiApplication.clipboard().setText(redact(text))

    def _clear(self) -> None:
        self.manager.errors.clear()
        self.refresh_errors()

    # ------------------------------------------------------------------ dependencies
    def dependency_lines(self) -> dict:
        ffmpeg = self.manager.ffmpeg
        packages = system_info.dependency_status()
        runtime = system_info.js_runtime_path()
        info = {name: ok for name, ok in packages.items()}
        info["FFmpeg"] = ffmpeg.status_text() if ffmpeg.available else False
        info["ffprobe"] = bool(getattr(ffmpeg, "ffprobe_path", None))
        info["JavaScript runtime (Deno/Node/Bun)"] = f"{runtime[0]} ({runtime[1]})" if runtime else False
        info["aria2c (optional)"] = bool(shutil.which("aria2c"))
        return info

    def refresh_dependencies(self) -> None:
        lines = []
        hints = {"FFmpeg": tr("Install: winget install Gyan.FFmpeg"), "ffprobe": tr("Comes with FFmpeg."),
                 "JavaScript runtime (Deno/Node/Bun)": tr("Install: winget install DenoLand.Deno"),
                 "aria2c (optional)": tr("Optional: winget install aria2.aria2")}
        for name, value in self.dependency_lines().items():
            ok = bool(value)
            state = (value if isinstance(value, str) else tr("found")) if ok else tr("MISSING")
            line = f"{'OK     ' if ok else 'MISSING'}  {name}: {state}"
            if not ok and name in hints:
                line += f"\n         -> {hints[name]}"
            elif not ok:
                line += f"\n         -> pip install {name.split()[0]}"
            lines.append(line)
        self.deps_view.setPlainText("\n".join(lines))

    def update_engines(self) -> None:
        if self._update_thread is not None:
            return
        self.update_button.setEnabled(False)
        self.update_button.setText(tr("Updating..."))
        self.deps_view.setPlainText(tr("Updating yt-dlp, gallery-dl, you-get and pytubefix... this can take a minute."))
        thread = _EngineUpdateThread(self)
        thread.finished_update.connect(self._engines_updated)
        self._update_thread = thread
        thread.start()

    def _engines_updated(self, ok: bool, text: str) -> None:
        thread, self._update_thread = self._update_thread, None
        if thread is not None:
            thread.wait()
        self.update_button.setEnabled(True)
        self.update_button.setText(tr("Update download engines"))
        self.refresh_dependencies()
        head = tr("Engines updated. Restart the application to use the new versions.") if ok \
            else tr("Engine update failed.")
        self.deps_view.setPlainText(head + "\n\n" + redact(text) + "\n\n" + self.deps_view.toPlainText())

    # ------------------------------------------------------------------ logs and export
    def reload_log(self) -> None:
        self.log_view.setPlainText(redact(diagnostics.tail(system_info.log_path(), lines=400)))
        self.log_view.verticalScrollBar().setValue(self.log_view.verticalScrollBar().maximum())

    def _open_log_folder(self) -> None:
        system_info.reveal_in_folder(system_info.log_path())

    def _export(self) -> None:
        default = str(Path.home() / f"video-downloader-report-{app_info.VERSION}.txt")
        path, _ = QFileDialog.getSaveFileName(self, tr("Export diagnostic report"), default, "Text (*.txt)")
        if not path:
            return
        settings = self.settings.snapshot()
        report = diagnostics.build_report(settings, self.dependency_lines(), self.manager.errors.items(),
                                          system_info.log_path())
        try:
            Path(path).write_text(report, encoding="utf-8")
        except OSError as exc:
            QMessageBox.warning(self, tr("Export diagnostic report"), str(exc))
            return
        QMessageBox.information(self, tr("Export diagnostic report"),
                                f"{tr('Saved. Cookies and passwords are never included.')}\n{path}")
