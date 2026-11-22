"""System tray icon, desktop notifications and close/minimize-to-tray behaviour."""
from __future__ import annotations

from gui.i18n import tr

from typing import Optional

from PyQt6.QtCore import QObject
from PyQt6.QtGui import QAction, QIcon
from PyQt6.QtWidgets import QApplication, QMainWindow, QMenu, QSystemTrayIcon

import app_info
from config.settings import Settings
from core.download_manager import DownloadManager
from core.download_queue import TaskStatus
from utils.system_info import resource_path


class TrayController(QObject):
    """Owns the tray icon. When the tray is unavailable or disabled, `available` is False and the window behaves
    normally (closing it quits the application)."""

    def __init__(self, window: QMainWindow, manager: DownloadManager, settings: Settings) -> None:
        super().__init__(window)
        self.window, self.manager, self.settings = window, manager, settings
        self.quitting = False
        self.tray: Optional[QSystemTrayIcon] = None
        if QSystemTrayIcon.isSystemTrayAvailable() and bool(settings.get("tray_enabled")):
            self._create()
        manager.task_finished.connect(self._on_task_finished)

    @property
    def available(self) -> bool:
        return self.tray is not None and self.tray.isVisible()

    def _create(self) -> None:
        icon_file = resource_path("assets", "icons", "app.ico")
        icon = QIcon(str(icon_file)) if icon_file.is_file() else self.window.windowIcon()
        self.tray = QSystemTrayIcon(icon, self)
        self.tray.setToolTip(app_info.APP_NAME)
        menu = QMenu()
        show = QAction(tr("Show window"), menu)
        show.triggered.connect(self.show_window)
        self.toggle_queue = QAction(tr("Pause queue"), menu)
        self.toggle_queue.triggered.connect(self._toggle_queue)
        quit_action = QAction(tr("Quit"), menu)
        quit_action.triggered.connect(self.quit)
        for action in (show, self.toggle_queue):
            menu.addAction(action)
        menu.addSeparator()
        menu.addAction(quit_action)
        self._menu = menu                      # keep a reference: QSystemTrayIcon does not take ownership
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._on_activated)
        self.manager.queue_paused_changed.connect(
            lambda paused: self.toggle_queue.setText(tr("Resume queue") if paused else tr("Pause queue")))
        self.tray.show()

    # ------------------------------------------------------------------ actions
    def show_window(self) -> None:
        self.window.showNormal()
        self.window.raise_()
        self.window.activateWindow()

    def _toggle_queue(self) -> None:
        if self.manager.is_paused():
            self.manager.start_queue()
        else:
            self.manager.pause_queue()

    def _on_activated(self, reason) -> None:
        if reason in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick):
            self.show_window()

    def quit(self) -> None:
        self.quitting = True
        self.window.close()
        QApplication.quit()

    # ------------------------------------------------------------------ behaviour hooks used by the window
    def should_hide_on_close(self) -> bool:
        return self.available and not self.quitting and bool(self.settings.get("close_to_tray"))

    def should_hide_on_minimize(self) -> bool:
        return self.available and bool(self.settings.get("minimize_to_tray"))

    def hide_notice(self) -> None:
        if self.tray is not None:
            self.tray.showMessage(app_info.APP_NAME, tr("Still running in the tray. Downloads continue."),
                                  QSystemTrayIcon.MessageIcon.Information, 3000)

    # ------------------------------------------------------------------ notifications
    def _on_task_finished(self, task_id: str, status: str, message: str) -> None:
        if self.tray is None or not self.settings.get("notifications"):
            return
        if status not in (TaskStatus.COMPLETED, TaskStatus.FAILED):
            return
        task = self.manager.queue.get(task_id)
        name = (task.title or task.url) if task else ""
        if status == TaskStatus.COMPLETED:
            self.tray.showMessage(tr("Download completed"), name, QSystemTrayIcon.MessageIcon.Information, 5000)
        else:
            self.tray.showMessage(tr("Download failed"), f"{name}\n{message}"[:240],
                                  QSystemTrayIcon.MessageIcon.Warning, 8000)
