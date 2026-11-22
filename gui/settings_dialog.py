"""Settings page (embedded as a tab) with validation before saving."""
from __future__ import annotations

from gui.i18n import tr

from typing import Optional

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFrame, QScrollArea, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QLabel,
    QLineEdit, QMessageBox, QPlainTextEdit, QPushButton, QSpinBox, QTabWidget, QVBoxLayout, QWidget,
)

from config.settings import DEFAULTS, Settings
from core import cookie_manager
from core import quality_manager as qm
from core.ffmpeg_manager import INSTALL_INSTRUCTIONS, FfmpegManager
from utils.logger import set_level


def _path_row(edit: QLineEdit, button: QPushButton) -> QWidget:
    holder = QWidget()
    row = QHBoxLayout(holder)
    row.setContentsMargins(0, 0, 0, 0)
    row.addWidget(edit, 1)
    row.addWidget(button)
    return holder


class SettingsPage(QWidget):
    """Settings tab. Emits `saved` after the values passed validation and were stored."""

    saved = pyqtSignal()

    def __init__(self, settings: Settings, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.settings = settings

        # ---- downloads
        self.dir_edit = QLineEdit(str(settings.get("download_dir")))
        browse_dir = QPushButton(tr("Browse..."))
        browse_dir.clicked.connect(self._browse_dir)
        self.quality_combo = QComboBox()
        for label, key in qm.VIDEO_QUALITY_CHOICES:
            self.quality_combo.addItem(label, key)
        self.container_combo = QComboBox()
        for label, key in qm.VIDEO_FORMAT_CHOICES:
            self.container_combo.addItem(label, key)
        self.bitrate_combo = QComboBox()
        for label, kbps in qm.AUDIO_QUALITY_CHOICES:
            self.bitrate_combo.addItem(label, kbps)
        self.connections = QSpinBox()
        self.connections.setRange(1, 16)
        self.connections.setToolTip("Parallel fragments / range requests per download. Higher is faster until "
                                    "your line or the server is the limit.")
        self.aria2c_check = QCheckBox(tr("Use aria2c (multi-connection, like a download manager) when installed"))
        self.codec_combo = QComboBox()
        for label, key in (("Auto", "auto"), ("H.264", "h264"), ("VP9", "vp9"), ("AV1", "av1")):
            self.codec_combo.addItem(label, key)
        self.compat_check = QCheckBox(tr("Compatibility mode: MP4 + H.264 + AAC (converts only when needed)"))
        self.save_thumbs = QCheckBox(tr("Save the thumbnail (.jpg) beside each download"))
        self.embed_thumbs = QCheckBox(tr("Embed the thumbnail as cover art in MP3 files"))
        self.conflict_combo = QComboBox()
        self.conflict_combo.addItem("Keep both (add a number)", "rename")
        self.conflict_combo.addItem("Skip if the file exists", "skip")
        self.conflict_combo.addItem("Overwrite existing file", "overwrite")
        self.filename_edit = QLineEdit(str(settings.get("filename_template")))
        self.playlist_filename_edit = QLineEdit(str(settings.get("playlist_filename_template")))
        self.filename_edit.setToolTip("Fields: {title} {id} {uploader} {date}")
        self.playlist_filename_edit.setToolTip("Fields: {index} {title} {id} {uploader} {playlist} {date}")

        downloads = QGroupBox(tr("Downloads"))
        form = QFormLayout(downloads)
        form.addRow(tr("Download folder"), _path_row(self.dir_edit, browse_dir))
        form.addRow(tr("Default video quality"), self.quality_combo)
        form.addRow(tr("Default video format"), self.container_combo)
        form.addRow(tr("Default audio bitrate"), self.bitrate_combo)
        form.addRow(tr("If a file already exists"), self.conflict_combo)
        form.addRow(tr("Filename template"), self.filename_edit)
        form.addRow(tr("Playlist filename template"), self.playlist_filename_edit)

        # ---- general (appearance, tray, notifications, updates)
        self.language_combo = QComboBox()
        self.language_combo.addItem("English", "en")
        self.language_combo.addItem("العربية", "ar")
        self.theme_combo = QComboBox()
        self.theme_combo.addItem(tr("Dark"), "dark")
        self.theme_combo.addItem(tr("Light"), "light")
        self.restart_note = QLabel(tr("Language changes apply after restarting the application."))
        self.restart_note.setObjectName("muted")
        self.tray_check = QCheckBox(tr("Show an icon in the system tray"))
        self.close_tray_check = QCheckBox(tr("Closing the window keeps downloads running in the tray"))
        self.min_tray_check = QCheckBox(tr("Minimizing the window hides it in the tray"))
        self.notify_check = QCheckBox(tr("Desktop notifications for completed and failed downloads"))
        self.clipboard_check = QCheckBox(tr("Watch the clipboard and offer links that you copy"))
        self.updates_check = QCheckBox(tr("Check for updates at start-up"))
        self.update_url = QLineEdit(str(settings.get("update_url")))
        self.update_url.setPlaceholderText("https://api.github.com/repos/<owner>/<repo>/releases/latest")
        general = QGroupBox(tr("Appearance"))
        g_form = QFormLayout(general)
        g_form.addRow(tr("Language"), self.language_combo)
        g_form.addRow(tr("Theme"), self.theme_combo)
        g_form.addRow(self.restart_note)
        behaviour = QGroupBox(tr("Behaviour"))
        b_form = QFormLayout(behaviour)
        for widget in (self.tray_check, self.close_tray_check, self.min_tray_check, self.notify_check,
                       self.clipboard_check):
            b_form.addRow("", widget)
        updates = QGroupBox(tr("Updates"))
        u_form = QFormLayout(updates)
        u_form.addRow("", self.updates_check)
        u_form.addRow(tr("Release address"), self.update_url)

        # ---- speed and scheduling
        self.speed_limit = QSpinBox()
        self.speed_limit.setRange(0, 1_000_000)
        self.speed_limit.setSuffix(" KB/s")
        self.speed_limit.setSpecialValueText(tr("Unlimited"))
        self.speed_limit.setToolTip(tr("Applies to each download (yt-dlp). 0 means unlimited."))
        self.schedule_check = QCheckBox(tr("Only start queued downloads inside this time window"))
        self.schedule_start = QLineEdit()
        self.schedule_end = QLineEdit()
        for edit in (self.schedule_start, self.schedule_end):
            edit.setPlaceholderText("HH:MM")
            edit.setMaximumWidth(100)
        speed = QGroupBox(tr("Speed and scheduling"))
        sp_form = QFormLayout(speed)
        sp_form.addRow(tr("Download connections"), self.connections)
        sp_form.addRow("", self.aria2c_check)
        sp_form.addRow(tr("Speed limit"), self.speed_limit)
        sp_form.addRow("", self.schedule_check)
        sp_form.addRow(tr("From"), self.schedule_start)
        sp_form.addRow(tr("Until"), self.schedule_end)
        self.schedule_check.toggled.connect(lambda on: (self.schedule_start.setEnabled(on),
                                                        self.schedule_end.setEnabled(on)))

        # ---- media
        media = QGroupBox(tr("Media"))
        m_form = QFormLayout(media)
        m_form.addRow(tr("Preferred codec"), self.codec_combo)
        m_form.addRow("", self.compat_check)
        m_form.addRow("", self.save_thumbs)
        m_form.addRow("", self.embed_thumbs)

        # ---- FFmpeg
        self.ffmpeg_edit = QLineEdit(str(settings.get("ffmpeg_path")))
        self.ffmpeg_edit.setPlaceholderText(tr("Leave empty to use FFmpeg from PATH"))
        browse_ffmpeg = QPushButton(tr("Browse..."))
        browse_ffmpeg.clicked.connect(self._browse_ffmpeg)
        detect = QPushButton(tr("Detect"))
        detect.clicked.connect(self._detect_ffmpeg)
        help_button = QPushButton(tr("Installation help"))
        help_button.clicked.connect(self._show_ffmpeg_help)
        self.ffmpeg_status = QLabel("")
        self.ffmpeg_status.setWordWrap(True)
        self.ffmpeg_status.setObjectName("muted")
        ffmpeg_box = QGroupBox(tr("FFmpeg"))
        ff_layout = QVBoxLayout(ffmpeg_box)
        ff_form = QFormLayout()
        ff_form.addRow(tr("FFmpeg path"), _path_row(self.ffmpeg_edit, browse_ffmpeg))
        ff_layout.addLayout(ff_form)
        buttons = QHBoxLayout()
        buttons.addWidget(detect)
        buttons.addWidget(help_button)
        buttons.addStretch(1)
        ff_layout.addLayout(buttons)
        ff_layout.addWidget(self.ffmpeg_status)

        # ---- cookies
        self.cookie_mode = QComboBox()
        self.cookie_mode.addItem("Do not use cookies", "none")
        self.cookie_mode.addItem("Use browser cookies", "browser")
        self.cookie_mode.addItem("Use a cookies.txt file", "file")
        self.cookie_browser = QComboBox()
        for label, key in cookie_manager.BROWSER_CHOICES:
            self.cookie_browser.addItem(label, key)
        self.cookie_file = QLineEdit(str(settings.get("cookie_file")))
        browse_cookie = QPushButton(tr("Browse..."))
        browse_cookie.clicked.connect(self._browse_cookie)
        validate_cookie = QPushButton(tr("Validate"))
        validate_cookie.clicked.connect(self._validate_cookie)
        self.cookie_status = QLabel(tr("Cookies stay on this computer and are never logged or uploaded."))
        self.cookie_status.setObjectName("muted")
        self.cookie_status.setWordWrap(True)
        cookies = QGroupBox(tr("Authentication cookies"))
        c_form = QFormLayout(cookies)
        c_form.addRow(tr("Cookie source"), self.cookie_mode)
        c_form.addRow(tr("Browser"), self.cookie_browser)
        file_row = _path_row(self.cookie_file, browse_cookie)
        file_row.layout().addWidget(validate_cookie)
        c_form.addRow(tr("Cookies file"), file_row)
        c_form.addRow(self.cookie_status)
        self.cookie_mode.currentIndexChanged.connect(self._update_cookie_controls)

        # ---- reliability
        self.retries = QSpinBox()
        self.retries.setRange(0, 10)
        self.retry_delay = QSpinBox()
        self.retry_delay.setRange(0, 600)
        self.retry_delay.setSuffix(" s")
        self.concurrent = QSpinBox()
        self.concurrent.setRange(1, 5)
        self.auto_queue = QCheckBox(tr("Start queued downloads automatically"))
        self.auto_network = QCheckBox(tr("Resume automatically when the network is back"))
        self.preserve_partials = QCheckBox(tr("Keep partial downloads when paused or interrupted"))
        self.auto_recover = QCheckBox(tr("Resume unfinished downloads automatically after a restart"))
        self.log_level = QComboBox()
        for name in ("Debug", "Info", "Warning", "Error"):
            self.log_level.addItem(name, name.lower())
        queue_box = QGroupBox(tr("Queue and reliability"))
        q_form = QFormLayout(queue_box)
        q_form.addRow(tr("Maximum retries"), self.retries)
        q_form.addRow(tr("Initial retry delay (doubles each attempt)"), self.retry_delay)
        q_form.addRow(tr("Concurrent downloads"), self.concurrent)
        q_form.addRow("", self.auto_queue)
        q_form.addRow("", self.auto_network)
        q_form.addRow("", self.preserve_partials)
        q_form.addRow("", self.auto_recover)
        q_form.addRow(tr("Log verbosity"), self.log_level)

        self.save_button = QPushButton(tr("Save settings"))
        self.save_button.setObjectName("primary")
        self.save_button.clicked.connect(self._save)
        self.revert_button = QPushButton(tr("Revert"))
        self.revert_button.clicked.connect(self.reload)
        self.reset_button = QPushButton(tr("Reset to defaults"))
        self.reset_button.clicked.connect(self._reset_defaults)

        def category(*boxes: QWidget) -> QWidget:
            content = QWidget()
            inner = QVBoxLayout(content)
            for box in boxes:
                inner.addWidget(box)
            inner.addStretch(1)
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QFrame.Shape.NoFrame)
            scroll.setWidget(content)
            return scroll

        self.categories = QTabWidget()
        self.categories.addTab(category(general, behaviour, updates), tr("General"))
        self.categories.addTab(category(downloads, media), tr("Downloads"))
        self.categories.addTab(category(queue_box, speed), tr("Queue and speed"))
        self.categories.addTab(category(ffmpeg_box, cookies), tr("Tools and sign-in"))
        footer = QHBoxLayout()
        footer.addWidget(self.reset_button)
        footer.addStretch(1)
        footer.addWidget(self.revert_button)
        footer.addWidget(self.save_button)
        layout = QVBoxLayout(self)
        layout.addWidget(self.categories, 1)
        layout.addLayout(footer)

        self._load()
        self._update_cookie_controls()
        self._detect_ffmpeg()

    # ------------------------------------------------------------------ load / save
    @staticmethod
    def _select(combo: QComboBox, value: object) -> None:
        index = combo.findData(value)
        if index >= 0:
            combo.setCurrentIndex(index)

    def _load(self, source=None) -> None:
        s = source if source is not None else self.settings
        self._select(self.language_combo, s.get("language"))
        self._select(self.theme_combo, s.get("theme"))
        self.tray_check.setChecked(bool(s.get("tray_enabled")))
        self.close_tray_check.setChecked(bool(s.get("close_to_tray")))
        self.min_tray_check.setChecked(bool(s.get("minimize_to_tray")))
        self.notify_check.setChecked(bool(s.get("notifications")))
        self.clipboard_check.setChecked(bool(s.get("clipboard_monitor")))
        self.updates_check.setChecked(bool(s.get("check_updates")))
        self.update_url.setText(str(s.get("update_url")))
        self.speed_limit.setValue(int(s.get("speed_limit_kbps")))
        self.schedule_check.setChecked(bool(s.get("schedule_enabled")))
        self.schedule_start.setText(str(s.get("schedule_start")))
        self.schedule_end.setText(str(s.get("schedule_end")))
        self.schedule_start.setEnabled(self.schedule_check.isChecked())
        self.schedule_end.setEnabled(self.schedule_check.isChecked())
        self._select(self.quality_combo, s.get("default_video_quality"))
        self._select(self.container_combo, s.get("default_container"))
        self._select(self.bitrate_combo, s.get("default_audio_bitrate"))
        self._select(self.conflict_combo, s.get("conflict_policy"))
        self._select(self.cookie_mode, s.get("cookie_mode"))
        self._select(self.cookie_browser, s.get("cookie_browser"))
        self._select(self.log_level, str(s.get("log_level")).lower())
        self.retries.setValue(int(s.get("max_retries")))
        self.retry_delay.setValue(int(s.get("retry_delay")))
        self.concurrent.setValue(int(s.get("max_concurrent")))
        self.auto_queue.setChecked(bool(s.get("auto_process_queue")))
        self.connections.setValue(int(s.get("download_connections")))
        self.aria2c_check.setChecked(bool(s.get("use_aria2c")))
        self._select(self.codec_combo, s.get("preferred_codec"))
        self.compat_check.setChecked(bool(s.get("compat_mode")))
        self.save_thumbs.setChecked(bool(s.get("save_thumbnails")))
        self.embed_thumbs.setChecked(bool(s.get("embed_audio_thumbnail")))
        self.auto_network.setChecked(bool(s.get("auto_resume_network")))
        self.preserve_partials.setChecked(bool(s.get("preserve_partials")))
        self.auto_recover.setChecked(bool(s.get("auto_recover_queue")))
        self.dir_edit.setText(str(s.get("download_dir")))
        self.ffmpeg_edit.setText(str(s.get("ffmpeg_path")))
        self.filename_edit.setText(str(s.get("filename_template")))
        self.playlist_filename_edit.setText(str(s.get("playlist_filename_template")))
        self.cookie_file.setText(str(s.get("cookie_file")))

    def _reset_defaults(self) -> None:
        answer = QMessageBox.question(self, tr("Reset to defaults"),
                                      tr("Show default values? Nothing is saved until you press Save settings."))
        if answer == QMessageBox.StandardButton.Yes:
            self._load(DEFAULTS)
            self._update_cookie_controls()

    def reload(self) -> None:
        self._load()
        self._update_cookie_controls()
        self._detect_ffmpeg()

    def _collect(self) -> dict:
        return {
            "download_dir": self.dir_edit.text().strip(),
            "default_video_quality": self.quality_combo.currentData(),
            "default_container": self.container_combo.currentData(),
            "default_audio_bitrate": self.bitrate_combo.currentData(),
            "conflict_policy": self.conflict_combo.currentData(),
            "filename_template": self.filename_edit.text().strip(),
            "playlist_filename_template": self.playlist_filename_edit.text().strip(),
            "ffmpeg_path": self.ffmpeg_edit.text().strip(),
            "cookie_mode": self.cookie_mode.currentData(),
            "cookie_browser": self.cookie_browser.currentData(),
            "cookie_file": self.cookie_file.text().strip(),
            "max_retries": self.retries.value(),
            "retry_delay": self.retry_delay.value(),
            "max_concurrent": self.concurrent.value(),
            "auto_process_queue": self.auto_queue.isChecked(),
            "log_level": self.log_level.currentData(),
            "download_connections": self.connections.value(),
            "use_aria2c": self.aria2c_check.isChecked(),
            "preferred_codec": self.codec_combo.currentData(),
            "compat_mode": self.compat_check.isChecked(),
            "save_thumbnails": self.save_thumbs.isChecked(),
            "embed_audio_thumbnail": self.embed_thumbs.isChecked(),
            "auto_resume_network": self.auto_network.isChecked(),
            "preserve_partials": self.preserve_partials.isChecked(),
            "auto_recover_queue": self.auto_recover.isChecked(),
            "language": self.language_combo.currentData(),
            "theme": self.theme_combo.currentData(),
            "tray_enabled": self.tray_check.isChecked(),
            "close_to_tray": self.close_tray_check.isChecked(),
            "minimize_to_tray": self.min_tray_check.isChecked(),
            "notifications": self.notify_check.isChecked(),
            "clipboard_monitor": self.clipboard_check.isChecked(),
            "check_updates": self.updates_check.isChecked(),
            "update_url": self.update_url.text().strip(),
            "speed_limit_kbps": self.speed_limit.value(),
            "schedule_enabled": self.schedule_check.isChecked(),
            "schedule_start": self.schedule_start.text().strip() or "00:00",
            "schedule_end": self.schedule_end.text().strip() or "06:00",
        }

    def _save(self) -> None:
        values = self._collect()
        errors = self.settings.update(values)
        if errors:
            QMessageBox.warning(self, "Invalid settings", "\n".join(f"• {e}" for e in errors))
            return
        if values["cookie_mode"] == "file":
            check = cookie_manager.validate_cookie_file(values["cookie_file"])
            if not check.ok:
                QMessageBox.warning(self, "Cookies file", check.message)
        set_level(str(values["log_level"]))
        self.saved.emit()

    # ------------------------------------------------------------------ helpers
    def _browse_dir(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Select download folder", self.dir_edit.text())
        if path:
            self.dir_edit.setText(path)

    def _browse_ffmpeg(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Select the FFmpeg executable")
        if path:
            self.ffmpeg_edit.setText(path)
            self._detect_ffmpeg()

    def _detect_ffmpeg(self) -> None:
        manager = FfmpegManager(self.ffmpeg_edit.text().strip())
        self.ffmpeg_status.setText(manager.status_text() + ("" if manager.available else
                                   "\n" + "\n".join("• " + line for line in manager.limitations())))

    def _show_ffmpeg_help(self) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("Install FFmpeg")
        dialog.setMinimumSize(560, 360)
        text = QPlainTextEdit(INSTALL_INSTRUCTIONS)
        text.setReadOnly(True)
        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close.rejected.connect(dialog.reject)
        close.clicked.connect(dialog.reject)
        layout = QVBoxLayout(dialog)
        layout.addWidget(text)
        layout.addWidget(close)
        dialog.exec()

    def _browse_cookie(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Select cookies.txt", "", "Cookie files (*.txt);;All files (*)")
        if path:
            self.cookie_file.setText(path)
            self._validate_cookie()

    def _validate_cookie(self) -> None:
        check = cookie_manager.validate_cookie_file(self.cookie_file.text().strip())
        self.cookie_status.setText(("✔ " if check.ok else "✘ ") + check.message)

    def _update_cookie_controls(self) -> None:
        mode = self.cookie_mode.currentData()
        self.cookie_browser.setEnabled(mode == "browser")
        self.cookie_file.setEnabled(mode == "file")
