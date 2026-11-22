"""First-run setup wizard. Required, recommended and optional items are labelled; every step can be skipped."""
from __future__ import annotations

from gui.i18n import tr

import shutil
from typing import Callable, Dict, Optional

from PyQt6.QtGui import QGuiApplication
from PyQt6.QtWidgets import (
    QComboBox, QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWizard,
    QWizardPage, QWidget,
)

import app_info
from config.settings import Settings
from core import quality_manager as qm
from core.ffmpeg_manager import FfmpegManager
from utils import system_info


class _Page(QWizardPage):
    def __init__(self, title: str, subtitle: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setTitle(title)
        self.setSubTitle(subtitle)
        self.layout_ = QVBoxLayout(self)


class _ToolPage(_Page):
    """A dependency check with a level badge (Required / Recommended / Optional) and install guidance."""

    def __init__(self, title: str, level: str, why: str, install_cmd: str, check: Callable[[], str],
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(title, f"{level}. {why}", parent)
        self._check = check
        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.status.setStyleSheet("font-weight: 700;")
        self.command = QLineEdit(install_cmd)
        self.command.setReadOnly(True)
        self.copy = QPushButton(tr("Copy command"))
        self.recheck = QPushButton(tr("Check again"))
        self.skip_note = QLabel(tr("You can skip this step and install it later; the Dashboard shows its status."))
        self.skip_note.setObjectName("muted")
        self.skip_note.setWordWrap(True)
        row = QHBoxLayout()
        row.addWidget(self.command, 1)
        row.addWidget(self.copy)
        self.layout_.addWidget(self.status)
        self.layout_.addSpacing(8)
        self.layout_.addWidget(QLabel(tr("How to install:")))
        self.layout_.addLayout(row)
        self.layout_.addWidget(self.recheck)
        self.layout_.addStretch(1)
        self.layout_.addWidget(self.skip_note)
        self.copy.clicked.connect(lambda: QGuiApplication.clipboard().setText(self.command.text()))
        self.recheck.clicked.connect(self.refresh)
        self.refresh()

    def refresh(self) -> None:
        found = self._check()
        if found:
            self.status.setText(f"✔ {tr('Found')}: {found}")
            self.status.setStyleSheet("font-weight: 700; color: #3ecf8e;")
        else:
            self.status.setText(f"✖ {tr('Not found')}")
            self.status.setStyleSheet("font-weight: 700; color: #f5a623;")


class SetupWizard(QWizard):
    def __init__(self, settings: Settings, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.settings = settings
        self.setWindowTitle(f"{tr('Welcome to')} {app_info.APP_NAME}")
        self.setWizardStyle(QWizard.WizardStyle.ModernStyle)
        self.setMinimumSize(640, 480)
        self.setOption(QWizard.WizardOption.NoCancelButtonOnLastPage, True)
        self._ffmpeg_path = str(settings.get("ffmpeg_path"))

        # 1. download folder
        folder = _Page(tr("Download folder"), tr("Required. Choose where finished downloads are saved."))
        self.dir_edit = QLineEdit(str(settings.get("download_dir")))
        browse = QPushButton(tr("Browse..."))
        browse.clicked.connect(self._browse)
        row = QHBoxLayout()
        row.addWidget(self.dir_edit, 1)
        row.addWidget(browse)
        folder.layout_.addLayout(row)
        folder.layout_.addStretch(1)
        self.addPage(folder)

        # 2-4 tools
        self.ffmpeg_page = _ToolPage(
            "FFmpeg", tr("Recommended"),
            tr("Needed to merge video and audio, convert to MP3 and verify files. Without it only low-quality "
               "single-file formats work."),
            "winget install --id Gyan.FFmpeg", self._check_ffmpeg)
        browse_ff = QPushButton(tr("I already have it - choose ffmpeg..."))
        browse_ff.clicked.connect(self._browse_ffmpeg)
        self.ffmpeg_page.layout_.insertWidget(5, browse_ff)
        self.addPage(self.ffmpeg_page)
        self.addPage(_ToolPage(
            tr("JavaScript runtime (Deno)"), tr("Recommended for YouTube"),
            tr("YouTube needs Deno (or Node/Bun) so that all formats and resolutions are available."),
            "winget install DenoLand.Deno", lambda: (system_info.js_runtime_path() or ("", ""))[0]))
        self.addPage(_ToolPage(
            "aria2c", tr("Optional"),
            tr("Multi-connection downloads for extra speed. The built-in downloader already uses parallel parts."),
            "winget install aria2.aria2", lambda: shutil.which("aria2c") or ""))

        # 5-6 language and theme
        look = _Page(tr("Language and theme"), tr("Optional. The language applies after the application restarts."))
        self.language_combo = QComboBox()
        self.language_combo.addItem("English", "en")
        self.language_combo.addItem("العربية", "ar")
        self.theme_combo = QComboBox()
        self.theme_combo.addItem(tr("Dark"), "dark")
        self.theme_combo.addItem(tr("Light"), "light")
        form = QFormLayout()
        form.addRow(tr("Language"), self.language_combo)
        form.addRow(tr("Theme"), self.theme_combo)
        look.layout_.addLayout(form)
        look.layout_.addStretch(1)
        self._select(self.language_combo, str(settings.get("language")))
        self._select(self.theme_combo, str(settings.get("theme")))
        self.addPage(look)

        # 7 quality
        quality = _Page(tr("Default quality"), tr("Optional. You can change it for every download."))
        self.preset_combo = QComboBox()
        for label, key in qm.PRESET_CHOICES:
            if key != "custom":
                self.preset_combo.addItem(tr(label), key)
        self._select(self.preset_combo, str(settings.get("quality_preset")))
        q_form = QFormLayout()
        q_form.addRow(tr("Quality preset"), self.preset_combo)
        quality.layout_.addLayout(q_form)
        quality.layout_.addStretch(1)
        self.addPage(quality)

    @staticmethod
    def _select(combo: QComboBox, data: str) -> None:
        index = combo.findData(data)
        if index >= 0:
            combo.setCurrentIndex(index)

    def _browse(self) -> None:
        path = QFileDialog.getExistingDirectory(self, tr("Download folder"), self.dir_edit.text())
        if path:
            self.dir_edit.setText(path)

    def _check_ffmpeg(self) -> str:
        manager = FfmpegManager(self._ffmpeg_path)
        return manager.status_text() if manager.available else ""

    def _browse_ffmpeg(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "ffmpeg", "", "ffmpeg (ffmpeg ffmpeg.exe);;All files (*)")
        if path:
            self._ffmpeg_path = path
            self.ffmpeg_page.refresh()

    def values(self) -> Dict[str, object]:
        """Settings chosen in the wizard (only keys that exist in Settings)."""
        preset = str(self.preset_combo.currentData())
        spec = qm.preset_spec(preset)
        out: Dict[str, object] = {
            "download_dir": self.dir_edit.text().strip() or str(self.settings.get("download_dir")),
            "language": self.language_combo.currentData(), "theme": self.theme_combo.currentData(),
            "quality_preset": preset, "default_video_quality": spec.key if spec.key != "best" else "best",
            "compat_mode": spec.compat if preset != "best" else bool(self.settings.get("compat_mode")),
            "first_run_done": True,
        }
        if self._ffmpeg_path:
            out["ffmpeg_path"] = self._ffmpeg_path
        return out
