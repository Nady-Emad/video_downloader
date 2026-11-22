"""Dashboard: totals, active downloads, current speed, recent downloads, storage and dependency status."""
from __future__ import annotations

from gui.i18n import tr

import shutil
from pathlib import Path
from typing import Dict, Optional

from PyQt6.QtCore import QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import (
    QFrame, QGridLayout, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QProgressBar, QPushButton, QVBoxLayout,
    QWidget,
)

from config.settings import Settings
from core.download_history import DownloadHistory
from core.download_manager import DownloadManager
from core.download_queue import TaskStatus
from gui.thumbnails import ThumbnailCache
from gui.widgets import format_bytes
from utils.system_info import dependency_status, js_runtime_found


def format_speed(bytes_per_second: float) -> str:
    return f"{format_bytes(int(bytes_per_second))}/s" if bytes_per_second > 0 else "-"


class StatCard(QFrame):
    def __init__(self, label: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("card")
        self.value = QLabel("0")
        self.value.setObjectName("statValue")
        caption = QLabel(label.upper() if label.isascii() else label)
        caption.setObjectName("statLabel")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(4)
        layout.addWidget(caption)
        layout.addWidget(self.value)

    def set_value(self, text: str) -> None:
        self.value.setText(text)


class DashboardPage(QWidget):
    open_downloader = pyqtSignal()
    open_settings = pyqtSignal()

    def __init__(self, manager: DownloadManager, history: DownloadHistory, settings: Settings,
                 thumbs: ThumbnailCache, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.manager, self.history, self.settings, self.thumbs = manager, history, settings, thumbs
        self._speeds: Dict[str, float] = {}

        title = QLabel(tr("Dashboard"))
        title.setObjectName("title")
        self.update_banner = QLabel("")
        self.update_banner.setObjectName("card")
        self.update_banner.setOpenExternalLinks(True)
        self.update_banner.setVisible(False)
        self.update_banner.setContentsMargins(14, 10, 14, 10)

        self.total_card = StatCard(tr("Total downloads"))
        self.size_card = StatCard(tr("Downloaded size"))
        self.active_card = StatCard(tr("Active downloads"))
        self.speed_card = StatCard(tr("Current speed"))
        cards = QHBoxLayout()
        cards.setSpacing(12)
        for card in (self.total_card, self.size_card, self.active_card, self.speed_card):
            cards.addWidget(card, 1)

        self.recent = QListWidget()
        self.recent.setIconSize(QSize(96, 54))
        self.recent.setWordWrap(True)
        recent_box = self._card(tr("Recent downloads"), self.recent)

        self.storage_bar = QProgressBar()
        self.storage_bar.setRange(0, 100)
        self.storage_text = QLabel("")
        self.storage_text.setObjectName("muted")
        self.storage_text.setWordWrap(True)
        storage_inner = QWidget()
        s_layout = QVBoxLayout(storage_inner)
        s_layout.setContentsMargins(0, 0, 0, 0)
        s_layout.addWidget(self.storage_bar)
        s_layout.addWidget(self.storage_text)
        storage_box = self._card(tr("Storage"), storage_inner)

        self.deps_grid = QGridLayout()
        self.deps_grid.setHorizontalSpacing(12)
        deps_inner = QWidget()
        deps_layout = QVBoxLayout(deps_inner)
        deps_layout.setContentsMargins(0, 0, 0, 0)
        deps_layout.addLayout(self.deps_grid)
        self.fix_button = QPushButton(tr("Open Settings"))
        self.fix_button.clicked.connect(self.open_settings)
        deps_layout.addWidget(self.fix_button, 0, Qt.AlignmentFlag.AlignLeft)
        deps_box = self._card(tr("System status"), deps_inner)

        right = QVBoxLayout()
        right.setSpacing(12)
        right.addWidget(storage_box)
        right.addWidget(deps_box)
        right.addStretch(1)
        body = QHBoxLayout()
        body.setSpacing(12)
        body.addWidget(recent_box, 3)
        body.addLayout(right, 2)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(12)
        layout.addWidget(title)
        layout.addWidget(self.update_banner)
        layout.addLayout(cards)
        layout.addLayout(body, 1)

        self._timer = QTimer(self)
        self._timer.setInterval(1500)
        self._timer.timeout.connect(self._refresh_live)
        self._timer.start()
        manager.progress.connect(self._on_progress)
        manager.history_changed.connect(self.refresh)
        manager.queue_changed.connect(self._refresh_live)
        thumbs.ready.connect(lambda _u: self._fill_recent())
        self.refresh()

    @staticmethod
    def _card(title: str, content: QWidget) -> QFrame:
        frame = QFrame()
        frame.setObjectName("card")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(16, 14, 16, 14)
        heading = QLabel(title)
        heading.setObjectName("cardTitle")
        layout.addWidget(heading)
        layout.addWidget(content, 1)
        return frame

    # ------------------------------------------------------------------ data
    def _on_progress(self, task_id: str, info: object) -> None:
        speed = getattr(info, "speed", 0.0) or 0.0
        self._speeds[task_id] = float(speed)

    def _refresh_live(self) -> None:
        active_ids = {t.id for t in self.manager.queue.tasks() if t.status == TaskStatus.DOWNLOADING}
        for stale in [k for k in self._speeds if k not in active_ids]:
            self._speeds.pop(stale, None)
        self.active_card.set_value(str(len(active_ids)))
        self.speed_card.set_value(format_speed(sum(self._speeds.values())))

    def refresh(self) -> None:
        stats = self.history.stats()
        self.total_card.set_value(str(stats["completed"]))
        self.size_card.set_value(format_bytes(stats["bytes"]) if stats["bytes"] else "0 B")
        self._refresh_live()
        self._fill_recent()
        self._refresh_storage()
        self._refresh_dependencies()

    def _fill_recent(self) -> None:
        self.recent.clear()
        records = self.history.search(limit=12)
        if not records:
            item = QListWidgetItem(tr("No downloads yet. Paste a link in the Downloader to start."))
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            self.recent.addItem(item)
            return
        for rec in records:
            item = QListWidgetItem(f"{rec.title}\n{rec.platform} · {tr(rec.status)} · "
                                   f"{format_bytes(rec.file_size) if rec.file_size else '-'}")
            item.setSizeHint(QSize(0, 64))
            pix = self.thumbs.pixmap(rec.thumbnail, 96, 54, 6) if rec.thumbnail else None
            if pix is not None:
                item.setIcon(QIcon(pix))
            self.recent.addItem(item)

    def _refresh_storage(self) -> None:
        folder = Path(str(self.settings.get("download_dir"))).expanduser()
        probe = folder if folder.exists() else folder.parent if folder.parent.exists() else Path.home()
        try:
            usage = shutil.disk_usage(probe)
        except OSError:
            self.storage_text.setText(tr("Storage information is not available."))
            return
        used_pct = int(usage.used * 100 / usage.total) if usage.total else 0
        self.storage_bar.setValue(used_pct)
        self.storage_text.setText(f"{format_bytes(usage.free)} {tr('free of')} {format_bytes(usage.total)}  ·  {folder}")

    def _refresh_dependencies(self) -> None:
        while self.deps_grid.count():
            widget = self.deps_grid.takeAt(0).widget()
            if widget is not None:
                widget.deleteLater()
        ffmpeg = self.manager.ffmpeg
        runtime = js_runtime_found()
        rows = [
            ("yt-dlp", dependency_status().get("yt-dlp", False), tr("Required")),
            ("FFmpeg / ffprobe", bool(ffmpeg.available), tr("Recommended")),
            (tr("JavaScript runtime") + (f" ({runtime})" if runtime else ""), bool(runtime), tr("Recommended")),
            ("aria2c", bool(shutil.which("aria2c")), tr("Optional")),
        ]
        for row, (name, ok, level) in enumerate(rows):
            label = QLabel(name)
            kind = QLabel(level)
            kind.setObjectName("muted")
            state = QLabel(tr("OK") if ok else (tr("Missing") if level != tr("Optional") else tr("Not installed")))
            color = "#3ecf8e" if ok else ("#ff6161" if level == tr("Required") else "#f5a623")
            state.setStyleSheet(f"color: {color}; font-weight: 700;")
            self.deps_grid.addWidget(label, row, 0)
            self.deps_grid.addWidget(kind, row, 1)
            self.deps_grid.addWidget(state, row, 2)

    def show_update(self, version: str, url: str) -> None:
        link = f' <a href="{url}">{tr("Download")}</a>' if url else ""
        self.update_banner.setText(f"{tr('A new version is available:')} <b>{version}</b>{link}")
        self.update_banner.setVisible(True)
