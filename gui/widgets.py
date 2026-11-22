"""Reusable widgets: console, quality controls, format info and progress panel."""
from __future__ import annotations

from gui.i18n import tr

from datetime import datetime
from typing import Any, Dict, Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtCore import QSize
from PyQt6.QtGui import QColor, QIcon, QPixmap, QTextCharFormat, QTextCursor
from PyQt6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QFormLayout, QFrame, QGridLayout, QGroupBox, QHBoxLayout, QLabel,
    QListWidget, QListWidgetItem, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QVBoxLayout, QWidget,
)

from core import quality_manager as qm
from engines.base_engine import MediaInfo, ProgressInfo
from gui.styles import LOG_COLORS, MUTED
from gui.thumbnails import placeholder_pixmap, with_duration_badge

THUMB_W, THUMB_H = 192, 108          # 16:9 preview; thumbnails are cover-cropped, never stretched


# ----------------------------------------------------------------------------- formatting helpers
def format_bytes(value: Optional[float]) -> str:
    if not value or value <= 0:
        return "-"
    size = float(value)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return "-"


def format_speed(bytes_per_second: Optional[float]) -> str:
    return f"{format_bytes(bytes_per_second)}/s" if bytes_per_second and bytes_per_second > 0 else "-"


def format_eta(seconds: Optional[int]) -> str:
    if seconds is None or seconds < 0:
        return "-"
    hours, rest = divmod(int(seconds), 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes:02d}:{secs:02d}"


def format_duration(seconds: Optional[float]) -> str:
    if not seconds or seconds <= 0:
        return "-"
    hours, rest = divmod(int(seconds), 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


def ask_cancel_mode(parent: QWidget, count: int = 1) -> Optional[bool]:
    """Ask what to do with partial files. Returns keep_partial (True/False) or None when the user backs out."""
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Question)
    box.setWindowTitle("Cancel download")
    what = "this download" if count == 1 else f"these {count} downloads"
    box.setText(f"Cancel {what}?")
    box.setInformativeText("Keep the partial files so the download can be restarted later without starting from "
                           "zero, or delete them to free the disk space. Completed downloads are never deleted.")
    keep = box.addButton("Keep partial files", QMessageBox.ButtonRole.AcceptRole)
    delete = box.addButton("Delete partial files", QMessageBox.ButtonRole.DestructiveRole)
    back = box.addButton("Back", QMessageBox.ButtonRole.RejectRole)
    box.setDefaultButton(back)
    box.exec()
    clicked = box.clickedButton()
    if clicked is keep:
        return True
    if clicked is delete:
        return False
    return None


# ----------------------------------------------------------------------------- console
class ConsoleWidget(QPlainTextEdit):
    """Read-only live console with one colour per message level."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("console")
        self.setReadOnly(True)
        self.setMaximumBlockCount(5000)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)

    def append_message(self, level: str, message: str) -> None:
        level = level.upper()
        stamp = datetime.now().strftime("%H:%M:%S")
        fmt = QTextCharFormat()
        fmt.setForeground(QColor(LOG_COLORS.get(level, LOG_COLORS["INFO"])))
        bar = self.verticalScrollBar()
        at_bottom = bar.value() >= bar.maximum() - 4
        cursor = self.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        muted = QTextCharFormat()
        muted.setForeground(QColor(MUTED))
        cursor.insertText(f"{stamp} ", muted)
        cursor.insertText(f"[{level}] {message}\n", fmt)
        if at_bottom:
            bar.setValue(bar.maximum())


# ----------------------------------------------------------------------------- quality controls
class QualityPanel(QGroupBox):
    """Video quality, format and audio quality selectors (+ custom quality group)."""

    changed = pyqtSignal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(tr("Quality"), parent)
        self._ffmpeg_ok = True
        self._applying = False                 # True while a preset or the analysis changes the controls

        self.preset_combo = QComboBox()
        for label, key in qm.PRESET_CHOICES:
            self.preset_combo.addItem(tr(label), key)
        preset_row = QHBoxLayout()
        preset_row.addWidget(QLabel(tr("Preset")))
        preset_row.addWidget(self.preset_combo, 1)

        self.video_combo = QComboBox()
        for label, key in qm.VIDEO_QUALITY_CHOICES:
            self.video_combo.addItem(label, key)
        self.format_combo = QComboBox()
        for label, key in qm.VIDEO_FORMAT_CHOICES:
            self.format_combo.addItem(label, key)
        self.audio_combo = QComboBox()
        for label, kbps in qm.AUDIO_QUALITY_CHOICES:
            self.audio_combo.addItem(label, kbps)

        top = QGridLayout()
        top.setHorizontalSpacing(12)
        top.addWidget(QLabel(tr("Video quality")), 0, 0)
        top.addWidget(QLabel(tr("Video format")), 0, 1)
        top.addWidget(QLabel(tr("Audio quality")), 0, 2)
        top.addWidget(self.video_combo, 1, 0)
        top.addWidget(self.format_combo, 1, 1)
        top.addWidget(self.audio_combo, 1, 2)
        top.setColumnStretch(0, 2)
        top.setColumnStretch(1, 1)
        top.setColumnStretch(2, 2)

        self.codec_combo = QComboBox()
        for label, key in (("Auto", "auto"), ("H.264 (most compatible)", "h264"), ("VP9", "vp9"), ("AV1", "av1")):
            self.codec_combo.addItem(label, key)
        self.compat_check = QCheckBox(tr("Compatibility mode (MP4 + H.264 + AAC)"))
        self.compat_check.setToolTip("Guarantees the file plays in every player (QQPlayer, TVs, phones). "
                                     "Streams are re-encoded only when they are not already compatible.")
        self.compat_check.setChecked(True)
        options = QHBoxLayout()
        options.addWidget(QLabel(tr("Preferred codec")))
        options.addWidget(self.codec_combo)
        options.addSpacing(12)
        options.addWidget(self.compat_check, 1)

        # Custom quality controls (shown only for "Custom Quality")
        self.custom_box = QWidget()
        form = QFormLayout(self.custom_box)
        form.setContentsMargins(0, 8, 0, 0)
        self.custom_height = QComboBox()
        self.custom_height.addItem("No limit", 0)
        for height in qm.HEIGHT_KEYS:
            self.custom_height.addItem(f"{height}p", height)
        self.custom_height.setCurrentIndex(self.custom_height.findData(1080))
        self.custom_container = QComboBox()
        for label, key in qm.VIDEO_FORMAT_CHOICES:
            self.custom_container.addItem(label, key)
        self.custom_audio_format = QComboBox()
        for label, key in qm.CUSTOM_AUDIO_FORMATS:
            self.custom_audio_format.addItem(label, key)
        self.custom_bitrate = QComboBox()
        for label, kbps in qm.AUDIO_QUALITY_CHOICES:
            self.custom_bitrate.addItem(label, kbps)
        self.custom_bitrate.setCurrentIndex(self.custom_bitrate.findData(192))
        self.custom_priority = QComboBox()
        for label, key in qm.PRIORITY_CHOICES:
            self.custom_priority.addItem(label, key)
        form.addRow(tr("Maximum resolution"), self.custom_height)
        form.addRow(tr("Preferred video format"), self.custom_container)
        form.addRow(tr("Preferred audio format"), self.custom_audio_format)
        form.addRow(tr("Audio bitrate"), self.custom_bitrate)
        form.addRow(tr("Priority"), self.custom_priority)
        self.custom_box.setVisible(False)

        self.note = QLabel("")
        self.note.setObjectName("muted")
        self.note.setWordWrap(True)
        self.note.setVisible(False)

        layout = QVBoxLayout(self)
        layout.addLayout(preset_row)
        layout.addLayout(top)
        layout.addLayout(options)
        layout.addWidget(self.custom_box)
        layout.addWidget(self.note)

        for combo in (self.video_combo, self.format_combo, self.audio_combo, self.custom_height,
                      self.custom_container, self.custom_audio_format, self.custom_bitrate, self.custom_priority,
                      self.codec_combo):
            combo.currentIndexChanged.connect(self._on_changed)
        self.compat_check.toggled.connect(self._on_changed)
        self.preset_combo.currentIndexChanged.connect(self._on_preset_selected)

    # ------------------------------------------------------------------ presets
    def _on_preset_selected(self) -> None:
        key = str(self.preset_combo.currentData())
        if key == "custom":
            return                                  # the manual controls stay as they are
        spec = qm.preset_spec(key, str(self.format_combo.currentData()), int(self.audio_combo.currentData() or 0))
        self._applying = True
        try:
            for combo, value in ((self.video_combo, spec.key), (self.format_combo, spec.container)):
                index = combo.findData(value)
                if index >= 0 and not combo.model().item(index).isEnabled() and combo is self.video_combo:
                    index = combo.findData("best")          # e.g. this video has no 1080p: use its best instead
                if index >= 0 and combo.model().item(index).isEnabled():
                    combo.setCurrentIndex(index)
            self.set_codec_options(spec.codec, spec.compat)
            self._on_changed()                      # still inside the guard: must not flip the preset to "Custom"
        finally:
            self._applying = False

    def set_preset(self, key: str) -> None:
        index = self.preset_combo.findData(key)
        if index >= 0:
            if self.preset_combo.currentIndex() == index:
                self._on_preset_selected()
            else:
                self.preset_combo.setCurrentIndex(index)

    # ------------------------------------------------------------------ state
    def _on_changed(self) -> None:
        if not self._applying and self.preset_combo.currentData() != "custom":
            self.preset_combo.blockSignals(True)     # a manual change means the preset no longer describes it
            self.preset_combo.setCurrentIndex(self.preset_combo.findData("custom"))
            self.preset_combo.blockSignals(False)
        key = self.video_combo.currentData()
        self.custom_box.setVisible(key == "custom")
        self.audio_combo.setEnabled(key == "audio")   # bitrate only matters when extracting audio
        is_audio = key == "audio"
        self.codec_combo.setEnabled(not is_audio and not self.compat_check.isChecked())
        self.compat_check.setEnabled(not is_audio)
        self.changed.emit()

    def current_spec(self) -> qm.QualitySpec:
        custom: Optional[Dict[str, Any]] = None
        if self.video_combo.currentData() == "custom":
            custom = {
                "max_height": self.custom_height.currentData() or None,
                "container": self.custom_container.currentData(),
                "audio_format": self.custom_audio_format.currentData(),
                "audio_bitrate": self.custom_bitrate.currentData(),
                "priority": self.custom_priority.currentData(),
            }
        spec = qm.build_spec(
            str(self.video_combo.currentData()), str(self.format_combo.currentData()),
            int(self.audio_combo.currentData() or 0), custom,
        )
        spec.codec = str(self.codec_combo.currentData() or "auto")
        spec.compat = self.compat_check.isChecked()
        if self.preset_combo.currentData() == "small":
            spec.priority = "size"
        return spec

    def set_codec_options(self, codec: str, compat: bool) -> None:
        index = self.codec_combo.findData(codec)
        if index >= 0:
            self.codec_combo.setCurrentIndex(index)
        self.compat_check.setChecked(bool(compat))

    def set_defaults(self, quality_key: str, container: str, audio_bitrate: int) -> None:
        self._applying = True
        try:
            for combo, value in ((self.video_combo, quality_key), (self.format_combo, container),
                                 (self.audio_combo, audio_bitrate)):
                index = combo.findData(value)
                if index >= 0:
                    combo.setCurrentIndex(index)
            self._on_changed()
        finally:
            self._applying = False

    def set_ffmpeg_available(self, available: bool) -> None:
        """Disable options that need FFmpeg and explain why."""
        self._ffmpeg_ok = available
        self._applying = True                        # enabling/disabling items is not a manual choice
        try:
            self._update_ffmpeg_items(available)
        finally:
            self._applying = False

    def _update_ffmpeg_items(self, available: bool) -> None:
        mp3_index = self.video_combo.findData("audio")
        self._set_item_enabled(self.video_combo, mp3_index, True)   # audio is still possible (original format)
        for index in range(self.audio_combo.count()):
            needs_ffmpeg = self.audio_combo.itemData(index) != 0
            self._set_item_enabled(self.audio_combo, index, available or not needs_ffmpeg)
        for index in range(self.format_combo.count()):
            needs_ffmpeg = self.format_combo.itemData(index) == "mkv"   # MKV needs remuxing; single-file WEBM does not
            self._set_item_enabled(self.format_combo, index, available or not needs_ffmpeg)
        if not available:
            self.audio_combo.setCurrentIndex(self.audio_combo.findData(0))
            self.format_combo.setCurrentIndex(self.format_combo.findData("mp4"))
            self.note.setText("FFmpeg not found: only single-file streams are used, MP3 conversion, bitrate "
                              "selection and MKV output are disabled. See Settings for installation help.")
        self.note.setVisible(not available)
        self._on_changed()

    def apply_formats(self, info: Optional[MediaInfo]) -> None:
        """Enable only the resolutions that exist for the analysed video."""
        enabled = qm.enabled_video_keys(info.formats) if info and info.formats else None
        for index in range(self.video_combo.count()):
            key = self.video_combo.itemData(index)
            self._set_item_enabled(self.video_combo, index, enabled is None or key in enabled)
        current = self.video_combo.currentIndex()
        model = self.video_combo.model()
        if enabled is not None and not model.item(current).isEnabled():
            self._applying = True                    # not a manual change: keep the chosen preset label
            try:
                self.video_combo.setCurrentIndex(self.video_combo.findData("best"))
                self._on_changed()
            finally:
                self._applying = False

    @staticmethod
    def _set_item_enabled(combo: QComboBox, index: int, enabled: bool) -> None:
        item = combo.model().item(index)
        if item is not None:
            item.setEnabled(enabled)


# ----------------------------------------------------------------------------- preview card
class PreviewCard(QFrame):
    """Thumbnail, title, platform, duration, resolutions and estimated size of the analysed URL."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("card")
        self._base_pixmap: Optional[QPixmap] = None
        self._duration = ""
        self.thumb = QLabel()
        self.thumb.setObjectName("thumb")
        self.thumb.setFixedSize(THUMB_W, THUMB_H)
        self.thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.title = QLabel(tr("Paste a link to see a preview"))
        self.title.setObjectName("previewTitle")
        self.title.setWordWrap(True)
        self.title.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.meta = QLabel("")
        self.meta.setObjectName("muted")
        self.meta.setWordWrap(True)
        self.resolutions = QLabel("")
        self.resolutions.setWordWrap(True)
        self.size = QLabel("")
        text = QVBoxLayout()
        text.setSpacing(6)
        text.addWidget(self.title)
        text.addWidget(self.meta)
        text.addWidget(self.resolutions)
        text.addWidget(self.size)
        text.addStretch(1)
        layout = QHBoxLayout(self)             # compact: thumbnail left, details right
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(14)
        layout.addWidget(self.thumb, 0, Qt.AlignmentFlag.AlignTop)
        layout.addLayout(text, 1)
        self.clear()

    def clear(self, message: str = "Paste a link to see a preview") -> None:
        self._duration = ""
        self.set_pixmap(None)
        self.title.setText(message)
        self.meta.setText("")
        self.resolutions.setText("")
        self.size.setText("")

    def set_pixmap(self, pixmap: Optional[QPixmap]) -> None:
        self._base_pixmap = pixmap
        self._render_thumb()

    def set_duration(self, seconds: Optional[float]) -> None:
        self._duration = format_duration(seconds) if seconds else ""
        self._render_thumb()

    def _render_thumb(self) -> None:
        """Thumbnail with the video length as a badge in its bottom-left corner."""
        base = self._base_pixmap if self._base_pixmap is not None else placeholder_pixmap(
            THUMB_W, THUMB_H, 10, "No thumbnail")
        self.thumb.setPixmap(with_duration_badge(base, self._duration))

    def show_info(self, info: MediaInfo, platform: str) -> None:
        self.title.setText(info.title or "Untitled")
        parts = [platform] if platform else []
        if info.is_playlist:
            parts.append(f"Playlist · {len(info.entries)} items")
        elif info.duration:
            parts.append(format_duration(info.duration))
        self.meta.setText("  ·  ".join(parts))
        self.set_duration(None if info.is_playlist else info.duration)

    def show_details(self, summary: qm.SelectionSummary) -> None:
        heights = ", ".join(f"{h}p" for h in summary.available_resolutions) or "-"
        self.resolutions.setText(f"Resolutions: {heights}")
        self.size.setText("Estimated size: " + (("~" + format_bytes(summary.estimated_size))
                                                  if summary.estimated_size else "unknown"))

    def show_playlist_details(self, available: int, total: int) -> None:
        self.resolutions.setText("Quality is chosen per item")
        self.size.setText(f"{available} of {total} items available")


# ----------------------------------------------------------------------------- playlist selection
class PlaylistPanel(QGroupBox):
    """Checkable list of playlist items with thumbnails; the checked items are the ones downloaded."""

    selection_changed = pyqtSignal()
    _URL_ROLE = Qt.ItemDataRole.UserRole + 1
    _BADGED_ROLE = Qt.ItemDataRole.UserRole + 2

    def __init__(self, thumbs, parent: Optional[QWidget] = None) -> None:
        super().__init__(tr("Playlist items"), parent)
        self.thumbs = thumbs
        self._total = 0
        self._sizes: dict = {}
        self._titles: dict = {}
        self._durations: dict = {}
        self.list = QListWidget()
        self.list.setIconSize(QSize(96, 54))
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.list.setMinimumHeight(300)
        self.list.setWordWrap(True)
        self.list.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.list.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.all_button = QPushButton(tr("Select all"))
        self.none_button = QPushButton(tr("Select none"))
        self.count_label = QLabel("")
        self.count_label.setObjectName("muted")
        row = QHBoxLayout()
        row.addWidget(self.all_button)
        row.addWidget(self.none_button)
        row.addStretch(1)
        row.addWidget(self.count_label)
        layout = QVBoxLayout(self)
        layout.addLayout(row)
        layout.addWidget(self.list, 1)
        self.all_button.clicked.connect(lambda: self._set_all(True))
        self.none_button.clicked.connect(lambda: self._set_all(False))
        self.list.itemChanged.connect(self._on_item_changed)
        self.list.itemPressed.connect(self._remember_state)
        self.list.itemClicked.connect(self._toggle_on_row_click)
        self.setVisible(False)

    def set_entries(self, entries) -> None:
        self.list.blockSignals(True)
        self.list.clear()
        self._sizes.clear()
        self._titles.clear()
        self._durations.clear()
        self._total = len(entries)
        urls = []
        for entry in entries:
            # \u2068..\u2069 isolates the title so Arabic/Hebrew titles do not reorder the index number
            extra = []
            self._durations[entry.index] = format_duration(entry.duration) if entry.duration else ""
            if entry.size:
                extra.append("~" + format_bytes(entry.size))
            self._sizes[entry.index] = int(entry.size or 0)
            self._titles[entry.index] = (f"{entry.index:03d}   \u2068{entry.title}\u2069"
                                        + (f"   [{'  ·  '.join(extra)}]" if extra else ""))
            item = QListWidgetItem(self._titles[entry.index])
            item.setSizeHint(QSize(0, 64))
            item.setToolTip(entry.title)
            if entry.available and entry.url:
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(Qt.CheckState.Checked)
            else:
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
                item.setText(item.text() + "  (unavailable)")
            item.setData(Qt.ItemDataRole.UserRole, entry.index)
            item.setData(self._URL_ROLE, entry.thumbnail or "")
            self.list.addItem(item)
            if entry.thumbnail:
                urls.append(entry.thumbnail)
        self.list.blockSignals(False)
        self.thumbs.prefetch(urls)                  # all thumbnails load in parallel in the background
        self.refresh_icons()
        self._update_count()
        self.setVisible(bool(entries))

    def clear(self) -> None:
        self.list.clear()
        self._total = 0
        self._sizes.clear()
        self._titles.clear()
        self._durations.clear()
        self.setVisible(False)

    def set_item_status(self, index: int, status: str) -> None:
        """Shows what is happening to one playlist item (Downloading, Done, Failed, Skipped ...)."""
        marks = {"Downloading": "⏬", "Done": "✔", "Failed": "✖", "Interrupted": "⏸",
                 "Skipped (already downloaded)": "✔", "Unavailable": "—"}
        for row in range(self.list.count()):
            item = self.list.item(row)
            if int(item.data(Qt.ItemDataRole.UserRole)) == index:
                base = self._titles.get(index, item.text())
                item.setText(f"{marks.get(status, '')} {base}   —  {tr(status)}" if status else base)
                break

    def reset_statuses(self) -> None:
        for row in range(self.list.count()):
            item = self.list.item(row)
            item.setText(self._titles.get(int(item.data(Qt.ItemDataRole.UserRole)), item.text()))

    def refresh_icons(self) -> None:
        for row in range(self.list.count()):
            item = self.list.item(row)
            url = item.data(self._URL_ROLE)
            duration = self._durations.get(int(item.data(Qt.ItemDataRole.UserRole)), "")
            if item.data(self._BADGED_ROLE):
                continue                                  # real thumbnail with its badge is already set
            pixmap = self.thumbs.pixmap(url, 96, 54, 6) if url else None
            if pixmap is not None:
                item.setIcon(QIcon(with_duration_badge(pixmap, duration)))
                item.setData(self._BADGED_ROLE, True)
            elif duration and item.icon().isNull():       # no thumbnail (yet): neutral box carrying the time
                item.setIcon(QIcon(with_duration_badge(placeholder_pixmap(96, 54, 6), duration)))

    def _remember_state(self, item: QListWidgetItem) -> None:
        self._state_before_press = item.checkState()

    def _toggle_on_row_click(self, item: QListWidgetItem) -> None:
        """A click anywhere on the row toggles it. Clicking the box itself already toggled it, so do nothing then."""
        if not (item.flags() & Qt.ItemFlag.ItemIsUserCheckable):
            return
        if item.checkState() != getattr(self, "_state_before_press", item.checkState()):
            return
        item.setCheckState(Qt.CheckState.Unchecked if item.checkState() == Qt.CheckState.Checked
                           else Qt.CheckState.Checked)

    def _set_all(self, checked: bool) -> None:
        self.list.blockSignals(True)
        for row in range(self.list.count()):
            item = self.list.item(row)
            if item.flags() & Qt.ItemFlag.ItemIsUserCheckable:
                item.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
        self.list.blockSignals(False)
        self._on_item_changed()

    def _on_item_changed(self, *_args) -> None:
        self._update_count()
        self.selection_changed.emit()

    def _update_count(self) -> None:
        picked = self.selected_indices()
        known = sum(self._sizes.get(i, 0) for i in picked)
        text = f"{len(picked)} of {self._total} selected"
        if known:
            text += f"  ·  ~{format_bytes(known)}"
        self.count_label.setText(text)

    def selected_indices(self) -> list:
        out = []
        for row in range(self.list.count()):
            item = self.list.item(row)
            if item.flags() & Qt.ItemFlag.ItemIsUserCheckable and item.checkState() == Qt.CheckState.Checked:
                out.append(int(item.data(Qt.ItemDataRole.UserRole)))
        return out

    def all_selected(self) -> bool:
        usable = sum(1 for r in range(self.list.count())
                     if self.list.item(r).flags() & Qt.ItemFlag.ItemIsUserCheckable)
        return len(self.selected_indices()) == usable


# ----------------------------------------------------------------------------- format info
class InfoPanel(QGroupBox):
    """Shows what will be downloaded for the analysed media (intelligent quality detection)."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__("Format details", parent)
        grid = QGridLayout(self)
        grid.setHorizontalSpacing(18)
        self._values: Dict[str, QLabel] = {}
        rows = [
            ("title", "Title"), ("resolutions", "Available resolutions"), ("selected", "Selected resolution"),
            ("vcodec", "Video codec"), ("acodec", "Audio codec"), ("size", "Estimated size"),
            ("container", "Container"), ("ffmpeg", "FFmpeg"),
        ]
        for row, (key, label) in enumerate(rows):
            name = QLabel(label)
            name.setObjectName("muted")
            value = QLabel(tr("-"))
            value.setObjectName("statusValue")
            value.setWordWrap(True)
            value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            grid.addWidget(name, row, 0, Qt.AlignmentFlag.AlignTop)
            grid.addWidget(value, row, 1)
            self._values[key] = value
        grid.setColumnStretch(1, 1)
        self.notes = QLabel("")
        self.notes.setObjectName("muted")
        self.notes.setWordWrap(True)
        grid.addWidget(self.notes, len(rows), 0, 1, 2)

    def clear(self) -> None:
        for label in self._values.values():
            label.setText("-")
        self.notes.setText("")

    def show_text(self, key: str, text: str) -> None:
        self._values[key].setText(text)

    def show_summary(self, title: str, summary: qm.SelectionSummary, ffmpeg_text: str) -> None:
        heights = ", ".join(f"{h}p" for h in summary.available_resolutions) or "-"
        selected = f"{summary.selected_resolution}p" if summary.selected_resolution else (
            "Not available" if not summary.possible else "Chosen at download time")
        self._values["title"].setText(title or "-")
        self._values["resolutions"].setText(heights)
        self._values["selected"].setText(selected)
        self._values["vcodec"].setText(summary.video_codec)
        self._values["acodec"].setText(summary.audio_codec)
        self._values["size"].setText(("~" + format_bytes(summary.estimated_size)) if summary.estimated_size else "Unknown")
        self._values["container"].setText(summary.container.upper())
        self._values["ffmpeg"].setText(ffmpeg_text)
        self.notes.setText("\n".join(summary.notes))


# ----------------------------------------------------------------------------- progress
class ProgressPanel(QGroupBox):
    """Progress bar with speed, ETA, size, current file, playlist counters, active engine and Pause/Resume/Cancel."""

    pause_requested = pyqtSignal()
    resume_requested = pyqtSignal()
    cancel_requested = pyqtSignal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(tr("Progress"), parent)
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.bar.setValue(0)
        self.playlist_bar = QProgressBar()
        self.playlist_bar.setVisible(False)
        self.playlist_bar.setFormat("Playlist %v / %m")

        self.title_label = QLabel(tr("No active download"))
        self.title_label.setObjectName("statusValue")
        self.title_label.setWordWrap(True)
        self.stage_label = QLabel("")
        self.stage_label.setObjectName("muted")
        self.speed_label = QLabel(tr("-"))
        self.eta_label = QLabel(tr("-"))
        self.size_label = QLabel(tr("-"))
        self.engine_label = QLabel(tr("-"))
        self.playlist_label = QLabel("")
        self.playlist_label.setObjectName("muted")

        stats = QGridLayout()
        for column, (caption, widget) in enumerate((("Speed", self.speed_label), ("ETA", self.eta_label),
                                                    ("Size", self.size_label), ("Engine", self.engine_label))):
            cap = QLabel(caption)
            cap.setObjectName("muted")
            stats.addWidget(cap, 0, column)
            widget.setObjectName("statusValue")
            stats.addWidget(widget, 1, column)
            stats.setColumnStretch(column, 1)

        self.pause_button = QPushButton(tr("Pause"))
        self.resume_button = QPushButton(tr("Resume"))
        self.cancel_button = QPushButton(tr("Cancel"))
        self.cancel_button.setObjectName("danger")
        self.pause_button.clicked.connect(self.pause_requested)
        self.resume_button.clicked.connect(self.resume_requested)
        self.cancel_button.clicked.connect(self.cancel_requested)
        buttons = QHBoxLayout()
        buttons.addWidget(self.pause_button)
        buttons.addWidget(self.resume_button)
        buttons.addWidget(self.cancel_button)
        buttons.addStretch(1)
        self.set_controls(False, False)

        layout = QVBoxLayout(self)
        layout.addWidget(self.title_label)
        layout.addWidget(self.bar)
        layout.addLayout(stats)
        layout.addWidget(self.playlist_label)
        layout.addWidget(self.playlist_bar)
        layout.addWidget(self.stage_label)
        layout.addLayout(buttons)

    def set_controls(self, can_pause: bool, can_resume: bool, can_cancel: Optional[bool] = None) -> None:
        self.pause_button.setEnabled(can_pause)
        self.resume_button.setEnabled(can_resume)
        self.cancel_button.setEnabled(can_pause or can_resume if can_cancel is None else can_cancel)

    def reset(self) -> None:
        self.bar.setRange(0, 1000)
        self.bar.setValue(0)
        self.bar.setFormat("%p%")
        self.speed_label.setText("-")
        self.eta_label.setText("-")
        self.size_label.setText("-")
        self.stage_label.setText("")

    def set_title(self, title: str) -> None:
        self.title_label.setText(title or "No active download")

    def set_engine(self, engine: str) -> None:
        self.engine_label.setText(engine or "-")

    def update_progress(self, info: ProgressInfo) -> None:
        if info.title:
            self.title_label.setText(info.title)
        if info.engine:
            self.engine_label.setText(info.engine)
        stage_text = {"merging": "Merging video and audio with FFmpeg...",
                      "converting": "Converting audio with FFmpeg...",
                      "finished": "Finalizing...", "downloading": ""}.get(info.stage, "")
        self.stage_label.setText(stage_text)
        if info.stage in ("merging", "converting") or info.percent < 0:
            self.bar.setRange(0, 0)            # busy indicator when no percentage is known
        else:
            self.bar.setRange(0, 1000)
            self.bar.setValue(int(info.percent * 10))
        self.speed_label.setText(format_speed(info.speed))
        self.eta_label.setText(format_eta(info.eta))
        if info.total:
            self.size_label.setText(f"{format_bytes(info.downloaded)} / {format_bytes(info.total)}")
        elif info.downloaded:
            self.size_label.setText(format_bytes(info.downloaded))

    def set_playlist(self, done: int, total: int, failed: int) -> None:
        if total <= 0:
            self.playlist_bar.setVisible(False)
            self.playlist_label.setText("")
            return
        self.playlist_bar.setVisible(True)
        self.playlist_bar.setRange(0, total)
        self.playlist_bar.setValue(min(done + failed, total))
        self.playlist_label.setText(f"Playlist: {done} of {total} downloaded" + (f", {failed} failed/unavailable" if failed else ""))

    def hide_playlist(self) -> None:
        self.playlist_bar.setVisible(False)
        self.playlist_label.setText("")
