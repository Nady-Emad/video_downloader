"""Download history tab."""
from __future__ import annotations

from gui.i18n import tr

from datetime import datetime
from pathlib import Path
from typing import List, Optional

from PyQt6.QtCore import Qt
from PyQt6.QtCore import QSize
from PyQt6.QtGui import QColor, QIcon
from PyQt6.QtWidgets import (
    QAbstractItemView, QComboBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMessageBox, QPushButton,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from core.download_history import STATUS_COMPLETED, STATUS_FAILED, DownloadHistory, HistoryRecord
from core.download_manager import DownloadManager
from core.platform_detector import ALL_PLATFORMS
from gui.styles import ERROR, SUCCESS
from gui.thumbnails import ThumbnailCache
from gui.widgets import format_bytes
from utils import system_info

COLUMNS = ["Title", "Platform", "Status", "Resolution", "Format", "Size", "Engine", "Date", "File / Error"]


class HistoryWidget(QWidget):
    def __init__(self, history: DownloadHistory, manager: DownloadManager, thumbs: Optional[ThumbnailCache] = None,
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.thumbs = thumbs
        self.history = history
        self.manager = manager
        self._records: List[HistoryRecord] = []

        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText(tr("Search by title..."))
        self.search_edit.setClearButtonEnabled(True)
        self.platform_combo = QComboBox()
        self.status_combo = QComboBox()
        self.status_combo.addItem("All statuses", "")
        self.status_combo.addItem("Completed", STATUS_COMPLETED)
        self.status_combo.addItem("Failed", STATUS_FAILED)
        self.sort_combo = QComboBox()
        self.sort_combo.addItem("Newest first", True)
        self.sort_combo.addItem("Oldest first", False)
        self._fill_platforms()

        filters = QHBoxLayout()
        filters.addWidget(self.search_edit, 3)
        filters.addWidget(self.platform_combo, 1)
        filters.addWidget(self.status_combo, 1)
        filters.addWidget(self.sort_combo, 1)

        self.empty_label = QLabel("No downloads yet.\nCompleted and failed downloads appear here.")
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
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setStretchLastSection(True)
        for column, width in enumerate((300, 100, 90, 90, 70, 80, 90, 140)):
            self.table.setColumnWidth(column, width)

        self.open_button = QPushButton(tr("Open File"))
        self.folder_button = QPushButton(tr("Open Folder"))
        self.retry_button = QPushButton(tr("Retry Failed"))
        self.remove_button = QPushButton(tr("Remove Record"))
        self.clear_button = QPushButton(tr("Clear History"))
        self.clear_button.setObjectName("danger")
        self.count_label = QLabel("")
        self.count_label.setObjectName("muted")
        buttons = QHBoxLayout()
        for button in (self.open_button, self.folder_button, self.retry_button, self.remove_button):
            buttons.addWidget(button)
        buttons.addStretch(1)
        buttons.addWidget(self.count_label)
        buttons.addWidget(self.clear_button)

        layout = QVBoxLayout(self)
        layout.addLayout(filters)
        layout.addWidget(self.table)
        layout.addWidget(self.empty_label)
        layout.addLayout(buttons)

        self.search_edit.textChanged.connect(self.refresh)
        self.platform_combo.currentIndexChanged.connect(self.refresh)
        self.status_combo.currentIndexChanged.connect(self.refresh)
        self.sort_combo.currentIndexChanged.connect(self.refresh)
        self.table.itemSelectionChanged.connect(self._update_buttons)
        self.table.itemDoubleClicked.connect(lambda _item: self._open_file())
        self.open_button.clicked.connect(self._open_file)
        self.folder_button.clicked.connect(self._open_folder)
        self.retry_button.clicked.connect(self._retry)
        self.remove_button.clicked.connect(self._remove)
        self.clear_button.clicked.connect(self._clear)
        self.manager.history_changed.connect(self.refresh)
        if self.thumbs is not None:
            self.thumbs.ready.connect(lambda _url: self.refresh())
        self.refresh()

    def _fill_platforms(self) -> None:
        current = self.platform_combo.currentData()
        self.platform_combo.blockSignals(True)
        self.platform_combo.clear()
        self.platform_combo.addItem("All platforms", "")
        known = list(ALL_PLATFORMS)
        for name in self.history.platforms():
            if name not in known:
                known.append(name)
        for name in known:
            self.platform_combo.addItem(name, name)
        index = self.platform_combo.findData(current) if current else 0
        self.platform_combo.setCurrentIndex(max(index, 0))
        self.platform_combo.blockSignals(False)

    def _thumb_icon(self, url: str) -> Optional[QIcon]:
        pix = self.thumbs.pixmap(url, 96, 54, 6) if (self.thumbs and url) else None
        return QIcon(pix) if pix is not None else None

    @staticmethod
    def _item(text: str, color: Optional[str] = None) -> QTableWidgetItem:
        item = QTableWidgetItem(text)
        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        if color:
            item.setForeground(QColor(color))
        return item

    def refresh(self, *_args) -> None:
        self._records = self.history.search(
            text=self.search_edit.text(), platform=str(self.platform_combo.currentData() or ""),
            status=str(self.status_combo.currentData() or ""), newest_first=bool(self.sort_combo.currentData()),
        )
        self.table.setUpdatesEnabled(False)
        self.table.setRowCount(len(self._records))
        for row, rec in enumerate(self._records):
            color = SUCCESS if rec.status == STATUS_COMPLETED else ERROR
            try:
                date = datetime.fromisoformat(rec.downloaded_at).strftime("%Y-%m-%d %H:%M")
            except ValueError:
                date = rec.downloaded_at
            last = rec.file_path if rec.status == STATUS_COMPLETED else rec.error
            cells = [
                self._item(rec.title), self._item(rec.platform), self._item(tr(rec.status), color),
                self._item(rec.resolution), self._item(rec.format.upper()),
                self._item(format_bytes(rec.file_size)), self._item(rec.engine or "-"), self._item(date),
                self._item(last),
            ]
            icon = self._thumb_icon(getattr(rec, "thumbnail", ""))
            if icon is not None:
                cells[0].setIcon(icon)
            cells[0].setData(Qt.ItemDataRole.UserRole, rec.id)
            cells[-1].setToolTip(last)
            for column, item in enumerate(cells):
                self.table.setItem(row, column, item)
        self.table.setUpdatesEnabled(True)
        self.count_label.setText(f"{len(self._records)} records")
        self.table.setVisible(bool(self._records))
        self.empty_label.setVisible(not self._records)
        self._update_buttons()

    def showEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        self._fill_platforms()
        self.refresh()
        super().showEvent(event)

    # ------------------------------------------------------------------ selection helpers
    def _selected(self) -> List[HistoryRecord]:
        rows = sorted({i.row() for i in self.table.selectionModel().selectedRows()})
        return [self._records[r] for r in rows if 0 <= r < len(self._records)]

    def _update_buttons(self) -> None:
        selected = self._selected()
        self.open_button.setEnabled(len(selected) == 1 and bool(selected[0].file_path))
        self.folder_button.setEnabled(len(selected) == 1)
        self.retry_button.setEnabled(any(r.status == STATUS_FAILED for r in selected))
        self.remove_button.setEnabled(bool(selected))
        self.clear_button.setEnabled(bool(self._records))

    # ------------------------------------------------------------------ actions
    def _open_file(self) -> None:
        selected = self._selected()
        if len(selected) != 1 or not selected[0].file_path:
            return
        path = Path(selected[0].file_path)
        if not path.is_file():
            QMessageBox.warning(self, "Open file", "The file no longer exists at its original location.")
        elif not system_info.open_path(path):
            QMessageBox.warning(self, "Open file", "This file type cannot be opened from the application.")

    def _open_folder(self) -> None:
        selected = self._selected()
        if len(selected) != 1:
            return
        rec = selected[0]
        target = Path(rec.file_path) if rec.file_path else Path(rec.dest_dir or ".")
        if not system_info.reveal_in_folder(target):
            QMessageBox.warning(self, "Open folder", "The folder could not be opened.")

    def _retry(self) -> None:
        count = 0
        for rec in self._selected():
            if rec.status == STATUS_FAILED and rec.id is not None and self.manager.retry_from_history(rec.id):
                count += 1
        if count:
            self.manager.emit_log("INFO", f"{count} failed download(s) added back to the queue.")

    def _remove(self) -> None:
        selected = self._selected()
        if not selected:
            return
        with_files = [r for r in selected if r.file_path and Path(r.file_path).is_file()]
        box = QMessageBox(self)
        box.setWindowTitle("Remove records")
        box.setText(f"Remove {len(selected)} history record(s)?")
        box.setInformativeText("By default the downloaded files are kept on disk.")
        remove_only = box.addButton("Remove records only", QMessageBox.ButtonRole.AcceptRole)
        remove_files = None
        if with_files:
            remove_files = box.addButton(f"Also delete {len(with_files)} file(s)", QMessageBox.ButtonRole.DestructiveRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(remove_only)
        box.exec()
        clicked = box.clickedButton()
        if clicked is not remove_only and clicked is not remove_files:
            return
        if clicked is remove_files:
            for rec in with_files:
                path = Path(rec.file_path)
                if system_info.is_safe_to_open(path):      # media files only
                    try:
                        path.unlink()
                    except OSError as exc:
                        QMessageBox.warning(self, "Delete file", f"Could not delete {path.name}: {exc.strerror or exc}")
        for rec in selected:
            if rec.id is not None:
                self.history.remove(rec.id)
        self.refresh()

    def _clear(self) -> None:
        answer = QMessageBox.question(
            self, "Clear history", "Remove all history records? Downloaded files are not deleted.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
        if answer == QMessageBox.StandardButton.Yes:
            self.history.clear()
            self.refresh()
