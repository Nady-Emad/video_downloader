"""Download queue tab."""
from __future__ import annotations

from gui.i18n import tr

from datetime import datetime
from typing import Dict, List, Optional

from PyQt6.QtCore import QItemSelectionModel, QSize, Qt
from PyQt6.QtGui import QColor, QIcon
from PyQt6.QtWidgets import (
    QAbstractItemView, QHBoxLayout, QHeaderView, QLabel, QMenu, QMessageBox, QPushButton, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from core.download_manager import DownloadManager
from core.download_queue import QueueTask, TaskStatus
from gui.styles import STATUS_COLORS
from gui.thumbnails import ThumbnailCache
from gui.widgets import ask_cancel_mode

COLUMNS = ["Title / URL", "Status", "Progress", "Quality", "Format", "Engine", "Added", "Error"]


class QueueWidget(QWidget):
    def __init__(self, manager: DownloadManager, thumbs: Optional[ThumbnailCache] = None,
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.thumbs = thumbs
        self.manager = manager
        self._rows: Dict[str, int] = {}

        self.empty_label = QLabel("The queue is empty.\nAdd a download from the Downloader tab.")
        self.empty_label.setObjectName("emptyHint")
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels([tr(c) for c in COLUMNS])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(62)
        self.table.setIconSize(QSize(96, 54))
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._show_menu)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setStretchLastSection(True)
        for column, width in enumerate((340, 150, 80, 110, 70, 90, 140)):
            self.table.setColumnWidth(column, width)

        self.start_button = QPushButton(tr("Start Queue"))
        self.start_button.setObjectName("primary")
        self.pause_button = QPushButton(tr("Pause Queue"))
        self.pause_task_button = QPushButton(tr("Pause"))
        self.resume_task_button = QPushButton(tr("Resume"))
        self.cancel_button = QPushButton(tr("Cancel Selected"))
        self.cancel_button.setObjectName("danger")
        self.retry_button = QPushButton(tr("Retry"))
        self.remove_button = QPushButton(tr("Remove"))
        self.clear_button = QPushButton(tr("Clear Completed"))
        self.up_button = QPushButton(tr("Priority ▲"))
        self.down_button = QPushButton(tr("Priority ▼"))
        self.summary = QLabel("")
        self.summary.setObjectName("muted")

        buttons = QHBoxLayout()
        for button in (self.start_button, self.pause_button, self.pause_task_button, self.resume_task_button,
                       self.cancel_button, self.retry_button,
                       self.remove_button, self.up_button, self.down_button, self.clear_button):
            buttons.addWidget(button)
        buttons.addStretch(1)
        buttons.addWidget(self.summary)

        layout = QVBoxLayout(self)
        layout.addLayout(buttons)
        layout.addWidget(self.table)
        layout.addWidget(self.empty_label)

        self.start_button.clicked.connect(self.manager.start_queue)
        self.pause_button.clicked.connect(self.manager.pause_queue)
        self.pause_task_button.clicked.connect(self._pause_selected)
        self.resume_task_button.clicked.connect(self._resume_selected)
        self.cancel_button.clicked.connect(self._cancel_selected)
        self.retry_button.clicked.connect(self._retry_selected)
        self.remove_button.clicked.connect(self._remove_selected)
        self.clear_button.clicked.connect(self.manager.clear_completed)
        self.up_button.clicked.connect(lambda: self._priority(+1))
        self.down_button.clicked.connect(lambda: self._priority(-1))
        self.table.itemSelectionChanged.connect(self._update_buttons)

        if self.thumbs is not None:
            self.thumbs.ready.connect(lambda _url: self.refresh())
        self.manager.queue_changed.connect(self.refresh)
        self.manager.task_updated.connect(self.update_task)
        self.manager.queue_paused_changed.connect(self._on_paused_changed)
        self.refresh()

    # ------------------------------------------------------------------ rendering
    def _selected_ids(self) -> List[str]:
        ids = []
        for index in self.table.selectionModel().selectedRows():
            item = self.table.item(index.row(), 0)
            if item is not None:
                ids.append(item.data(Qt.ItemDataRole.UserRole))
        return ids

    @staticmethod
    def _item(text: str, color: Optional[str] = None) -> QTableWidgetItem:
        item = QTableWidgetItem(text)
        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        if color:
            item.setForeground(QColor(color))
        return item

    def _fill_row(self, row: int, task: QueueTask) -> None:
        title = task.title or task.url
        first = self._item(title)
        first.setData(Qt.ItemDataRole.UserRole, task.id)
        first.setToolTip(task.url)
        pix = self.thumbs.pixmap(task.thumbnail, 96, 54, 6) if (self.thumbs and task.thumbnail) else None
        if pix is not None:
            first.setIcon(QIcon(pix))
        status = task.status
        if task.playlist_total and status in TaskStatus.ACTIVE:
            status = f"{status} ({task.playlist_done}/{task.playlist_total})"
        progress = f"{task.progress:.0f}%" if task.status in TaskStatus.ACTIVE or task.status == TaskStatus.COMPLETED else "-"
        error = self._item(task.error, "#ff5c5c" if task.status in (TaskStatus.FAILED, TaskStatus.INTERRUPTED) else None)
        error.setToolTip(task.error)
        cells = [
            first, self._item(tr(status), STATUS_COLORS.get(task.status)), self._item(progress),
            self._item(task.quality.label), self._item(task.format_label), self._item(task.engine or "-"),
            self._item(self._short_time(task.created_at)), error,
        ]
        for column, item in enumerate(cells):
            self.table.setItem(row, column, item)

    @staticmethod
    def _short_time(iso: str) -> str:
        try:
            return datetime.fromisoformat(iso).strftime("%d %b %H:%M")
        except ValueError:
            return iso

    def refresh(self) -> None:
        selected = set(self._selected_ids())
        tasks = sorted(self.manager.queue.tasks(), key=lambda t: (t.status not in TaskStatus.ACTIVE, -t.priority, t.seq))
        self.table.setUpdatesEnabled(False)
        self.table.setRowCount(len(tasks))
        self._rows = {}
        for row, task in enumerate(tasks):
            self._rows[task.id] = row
            self._fill_row(row, task)
            if task.id in selected:
                self.table.selectionModel().select(
                    self.table.model().index(row, 0),
                    QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows)
        self.table.setUpdatesEnabled(True)
        counts = {s: self.manager.queue.count(s) for s in (TaskStatus.PENDING, TaskStatus.COMPLETED, TaskStatus.FAILED)}
        self.summary.setText(f"{self.manager.active_count} running · {counts[TaskStatus.PENDING]} pending · "
                             f"{counts[TaskStatus.COMPLETED]} done · {counts[TaskStatus.FAILED]} failed")
        self.table.setVisible(bool(tasks))
        self.empty_label.setVisible(not tasks)
        self._update_buttons()

    def update_task(self, task_id: str) -> None:
        """Cheap in-place refresh of one row (progress, title, engine)."""
        row = self._rows.get(task_id)
        task = self.manager.queue.get(task_id)
        if row is None or task is None:
            return
        self._fill_row(row, task)

    def set_progress(self, task_id: str, percent: float) -> None:
        row = self._rows.get(task_id)
        if row is not None and percent >= 0:
            self.table.setItem(row, 2, self._item(f"{percent:.0f}%"))

    def _on_paused_changed(self, paused: bool) -> None:
        self.start_button.setText("Start Queue" if paused else "Queue Running")
        self._update_buttons()

    def _update_buttons(self) -> None:
        has_selection = bool(self._selected_ids())
        self.start_button.setEnabled(self.manager.paused or self.manager.queue.next_pending() is not None)
        self.pause_button.setEnabled(not self.manager.paused)
        self.cancel_button.setEnabled(has_selection or self.manager.active_count > 0)
        for button in (self.retry_button, self.remove_button, self.up_button, self.down_button,
                       self.pause_task_button, self.resume_task_button):
            button.setEnabled(has_selection)

    # ------------------------------------------------------------------ actions
    def _cancel_selected(self) -> None:
        ids = self._selected_ids() or self.manager.active_task_ids()
        if not ids:
            return
        keep = ask_cancel_mode(self, len(ids))
        if keep is None:
            return
        for task_id in ids:
            self.manager.cancel_task(task_id, keep_partial=keep)

    def _pause_selected(self) -> None:
        for task_id in self._selected_ids():
            self.manager.pause_task(task_id)

    def _resume_selected(self) -> None:
        for task_id in self._selected_ids():
            self.manager.resume_task(task_id)

    def _retry_selected(self) -> None:
        for task_id in self._selected_ids():
            self.manager.retry_task(task_id)

    def _remove_selected(self) -> None:
        ids = self._selected_ids()
        blocked = [t for t in ids if self.manager.is_active(t)]
        if blocked:
            QMessageBox.information(self, "Remove", "A running download cannot be removed. Cancel it first.")
        for task_id in ids:
            self.manager.remove_task(task_id)

    def _priority(self, delta: int) -> None:
        for task_id in self._selected_ids():
            self.manager.change_priority(task_id, delta)

    def _show_menu(self, position) -> None:
        ids = self._selected_ids()
        if not ids:
            return
        menu = QMenu(self)
        actions = {
            menu.addAction("Retry"): lambda: self._retry_selected(),
            menu.addAction("Cancel"): lambda: self._cancel_selected(),
            menu.addAction("Pause task"): lambda: [self.manager.pause_task(i) for i in ids],
            menu.addAction("Resume task"): lambda: [self.manager.resume_task(i) for i in ids],
            menu.addAction("Move up (priority)"): lambda: self._priority(+1),
            menu.addAction("Move down (priority)"): lambda: self._priority(-1),
            menu.addAction("Remove"): lambda: self._remove_selected(),
        }
        chosen = menu.exec(self.table.viewport().mapToGlobal(position))
        if chosen in actions:
            actions[chosen]()
