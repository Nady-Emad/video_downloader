"""Media Library: grid / list view of everything in the download folder with search, filter, sort and file operations.
Scanning and preview-frame extraction run in worker threads so the window stays responsive."""
from __future__ import annotations

from gui.i18n import tr

from pathlib import Path
from typing import Dict, List, Optional

from PyQt6.QtCore import QSize, Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import (
    QAbstractItemView, QComboBox, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListView, QListWidget,
    QListWidgetItem, QMessageBox, QPushButton, QVBoxLayout, QWidget,
)

from config.settings import Settings
from core import media_library as lib
from core.download_manager import DownloadManager
from gui.thumbnails import ThumbnailCache, placeholder_pixmap
from gui.widgets import format_bytes
from utils import system_info
from utils.logger import get_logger

log = get_logger("library")
GRID_W, GRID_H = 176, 99


class _ScanThread(QThread):
    done = pyqtSignal(object)           # List[LibraryItem]

    def __init__(self, root: Path, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.root = root

    def run(self) -> None:
        try:
            self.done.emit(lib.scan(self.root))
        except Exception as exc:        # never let a bad folder kill the thread silently
            log.error("Library scan failed: %s", exc)
            self.done.emit([])


class _PreviewThread(QThread):
    """Creates preview frames one by one (FFmpeg), newest requests first-in first-out."""
    made = pyqtSignal(str, str)         # media path, preview jpg

    def __init__(self, ffmpeg: str, paths: List[str], parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.ffmpeg, self.paths, self._stop = ffmpeg, paths, False

    def stop(self) -> None:
        self._stop = True

    def run(self) -> None:
        cache = system_info.cache_dir("library")
        for path in self.paths:
            if self._stop:
                return
            out = lib.make_preview(self.ffmpeg, Path(path), cache, width=GRID_W * 2)
            if out is not None:
                self.made.emit(path, str(out))


class LibraryPage(QWidget):
    def __init__(self, settings: Settings, manager: DownloadManager, thumbs: ThumbnailCache,
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.settings, self.manager, self.thumbs = settings, manager, thumbs
        self._items: List[lib.LibraryItem] = []
        self._shown: List[lib.LibraryItem] = []
        self._previews: Dict[str, str] = {}
        self._scan: Optional[_ScanThread] = None
        self._preview_thread: Optional[_PreviewThread] = None
        self._dirty = True

        title = QLabel(tr("Media Library"))
        title.setObjectName("title")
        self.search = QLineEdit()
        self.search.setPlaceholderText(tr("Search by name..."))
        self.search.setClearButtonEnabled(True)
        self.kind_combo = QComboBox()
        for label, key in ((tr("All"), "all"), (tr("Video"), "video"), (tr("Audio"), "audio")):
            self.kind_combo.addItem(label, key)
        self.sort_combo = QComboBox()
        for label, key in ((tr("Newest"), "date"), (tr("Name"), "name"), (tr("Size"), "size"), (tr("Type"), "type")):
            self.sort_combo.addItem(label, key)
        self.view_button = QPushButton(tr("List view"))
        self.view_button.setCheckable(True)
        self.refresh_button = QPushButton(tr("Refresh"))
        bar = QHBoxLayout()
        bar.addWidget(self.search, 1)
        bar.addWidget(self.kind_combo)
        bar.addWidget(self.sort_combo)
        bar.addWidget(self.view_button)
        bar.addWidget(self.refresh_button)

        self.list = QListWidget()
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.list.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.list.setUniformItemSizes(True)
        self.empty = QLabel(tr("No media found in the download folder."))
        self.empty.setObjectName("emptyHint")
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.open_button = QPushButton(tr("Open"))
        self.folder_button = QPushButton(tr("Open Folder"))
        self.rename_button = QPushButton(tr("Rename"))
        self.delete_button = QPushButton(tr("Delete..."))
        self.delete_button.setObjectName("danger")
        self.stats_label = QLabel("")
        self.stats_label.setObjectName("muted")
        actions = QHBoxLayout()
        for b in (self.open_button, self.folder_button, self.rename_button, self.delete_button):
            actions.addWidget(b)
        actions.addStretch(1)
        actions.addWidget(self.stats_label)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(10)
        layout.addWidget(title)
        layout.addLayout(bar)
        layout.addWidget(self.list, 1)
        layout.addWidget(self.empty)
        layout.addLayout(actions)

        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(250)
        self._debounce.timeout.connect(self._apply)
        self.search.textChanged.connect(lambda _t: self._debounce.start())
        self.kind_combo.currentIndexChanged.connect(self._apply)
        self.sort_combo.currentIndexChanged.connect(self._apply)
        self.view_button.toggled.connect(self._on_view_toggled)
        self.refresh_button.clicked.connect(self.reload)
        self.list.itemDoubleClicked.connect(lambda _i: self._open())
        self.list.itemSelectionChanged.connect(self._update_buttons)
        self.open_button.clicked.connect(self._open)
        self.folder_button.clicked.connect(self._open_folder)
        self.rename_button.clicked.connect(self._rename)
        self.delete_button.clicked.connect(self._delete)
        manager.history_changed.connect(self._mark_dirty)
        self._on_view_toggled(False)
        self._update_buttons()

    # ------------------------------------------------------------------ loading
    def root(self) -> Path:
        return Path(str(self.settings.get("download_dir"))).expanduser()

    def _mark_dirty(self) -> None:
        self._dirty = True
        if self.isVisible():
            self.reload()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        if self._dirty:
            self.reload()

    def reload(self) -> None:
        if self._scan is not None and self._scan.isRunning():
            return
        self._dirty = False
        self.stats_label.setText(tr("Scanning..."))
        self._scan = _ScanThread(self.root(), self)
        self._scan.done.connect(self._on_scanned)
        self._scan.finished.connect(self._on_thread_finished)
        self._scan.start()

    def _on_thread_finished(self) -> None:
        thread = self.sender()
        if thread is self._scan:
            self._scan = None
        if thread is self._preview_thread:
            self._preview_thread = None
        if thread is not None:
            thread.deleteLater()

    def _on_scanned(self, items: object) -> None:
        self._items = list(items) if isinstance(items, list) else []
        self._apply()
        self._start_previews()

    def _start_previews(self) -> None:
        ffmpeg = self.manager.ffmpeg.ffmpeg_path if self.manager.ffmpeg.available else ""
        if self._preview_thread is not None:
            self._preview_thread.stop()
        videos = [str(i.path) for i in self._items if i.kind == "video" and not i.thumbnail
                  and str(i.path) not in self._previews]
        if not ffmpeg or not videos:
            return
        thread = _PreviewThread(ffmpeg, videos, self)
        thread.made.connect(self._on_preview)
        thread.finished.connect(self._on_thread_finished)
        self._preview_thread = thread
        thread.start()

    def _on_preview(self, media: str, jpg: str) -> None:
        self._previews[media] = jpg
        for row in range(self.list.count()):
            item = self.list.item(row)
            if item.data(Qt.ItemDataRole.UserRole) == media:
                self._set_icon(item, jpg)

    # ------------------------------------------------------------------ display
    def _grid(self) -> bool:
        return not self.view_button.isChecked()

    def _on_view_toggled(self, _checked: bool = False) -> None:
        grid = self._grid()
        self.view_button.setText(tr("List view") if grid else tr("Grid view"))
        self.list.setViewMode(QListView.ViewMode.IconMode if grid else QListView.ViewMode.ListMode)
        self.list.setIconSize(QSize(GRID_W, GRID_H) if grid else QSize(96, 54))
        self.list.setGridSize(QSize(GRID_W + 24, GRID_H + 58) if grid else QSize())
        self.list.setResizeMode(QListView.ResizeMode.Adjust)
        self.list.setWordWrap(True)
        self.list.setSpacing(6 if grid else 2)
        self.list.setMovement(QListView.Movement.Static)
        self._apply()

    def _set_icon(self, item: QListWidgetItem, image: str) -> None:
        w, h = (GRID_W, GRID_H) if self._grid() else (96, 54)
        pix = self.thumbs.local_pixmap(image, w, h, 8)
        if pix is not None:
            item.setIcon(QIcon(pix))

    def _apply(self) -> None:
        shown = lib.filter_sort(self._items, self.search.text(), str(self.kind_combo.currentData()),
                                str(self.sort_combo.currentData()), descending=self.sort_combo.currentData() in ("date", "size"))
        self._shown = shown
        self.list.setUpdatesEnabled(False)
        self.list.clear()
        w, h = (GRID_W, GRID_H) if self._grid() else (96, 54)
        for it in shown:
            text = it.name if self._grid() else f"{it.name}\n{format_bytes(it.size)} · {it.modified_text} · {it.folder}"
            row = QListWidgetItem(text)
            row.setData(Qt.ItemDataRole.UserRole, str(it.path))
            row.setToolTip(f"{it.path}\n{format_bytes(it.size)} · {it.modified_text}")
            if self._grid():
                row.setSizeHint(QSize(GRID_W + 20, GRID_H + 52))
                row.setTextAlignment(Qt.AlignmentFlag.AlignHCenter)
            else:
                row.setSizeHint(QSize(0, 62))
            image = it.thumbnail or self._previews.get(str(it.path), "")
            if image:
                self._set_icon(row, image)
            else:
                row.setIcon(QIcon(placeholder_pixmap(w, h, 8, "♪" if it.kind == "audio" else "▶")))
            self.list.addItem(row)
        self.list.setUpdatesEnabled(True)
        stats = lib.storage_stats(self._items, self.root())
        self.stats_label.setText(
            f"{len(shown)}/{stats['files']} {tr('files')} · {format_bytes(stats['bytes'])} · "
            f"{tr('Video')} {format_bytes(stats['video_bytes'])} · {tr('Audio')} {format_bytes(stats['audio_bytes'])}")
        self.empty.setVisible(not shown)
        self.list.setVisible(bool(shown))
        self._update_buttons()

    # ------------------------------------------------------------------ operations
    def _selected_paths(self) -> List[Path]:
        return [Path(str(i.data(Qt.ItemDataRole.UserRole))) for i in self.list.selectedItems()]

    def _update_buttons(self) -> None:
        count = len(self.list.selectedItems())
        self.open_button.setEnabled(count == 1)
        self.folder_button.setEnabled(count == 1)
        self.rename_button.setEnabled(count == 1)
        self.delete_button.setEnabled(count >= 1)

    def _open(self) -> None:
        paths = self._selected_paths()
        if paths and not system_info.open_path(paths[0]):
            QMessageBox.warning(self, tr("Open"), tr("The file could not be opened."))

    def _open_folder(self) -> None:
        paths = self._selected_paths()
        if paths:
            system_info.reveal_in_folder(paths[0])

    def _rename(self) -> None:
        paths = self._selected_paths()
        if len(paths) != 1:
            return
        new_name, ok = QInputDialog.getText(self, tr("Rename"), tr("New name (without extension):"), text=paths[0].stem)
        if not ok or not new_name.strip():
            return
        try:
            lib.rename(self.root(), paths[0], new_name)
        except (ValueError, OSError) as exc:
            QMessageBox.warning(self, tr("Rename"), str(exc))
            return
        self.reload()

    def _delete(self) -> None:
        paths = self._selected_paths()
        if not paths:
            return
        names = "\n".join(p.name for p in paths[:8]) + ("\n..." if len(paths) > 8 else "")
        answer = QMessageBox.question(
            self, tr("Delete files"), f"{tr('Permanently delete these files from your computer?')}\n\n{names}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel, QMessageBox.StandardButton.Cancel)
        if answer != QMessageBox.StandardButton.Yes:
            return
        failed = []
        for path in paths:
            try:
                lib.delete(self.root(), path)
            except (ValueError, OSError) as exc:
                failed.append(f"{path.name}: {exc}")
        if failed:
            QMessageBox.warning(self, tr("Delete files"), "\n".join(failed))
        self.reload()

    def shutdown(self) -> None:
        if self._preview_thread is not None:
            self._preview_thread.stop()
            self._preview_thread.wait(3000)
        if self._scan is not None:
            self._scan.wait(3000)
