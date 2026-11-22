"""Application entry point."""
from __future__ import annotations

import runpy
import sys

from utils.system_info import RUN_MODULE_FLAG


def _run_embedded_module() -> None:
    """Frozen builds run gallery-dl / you-get by re-launching this executable with a module name."""
    module = sys.argv[2]
    sys.argv = [module] + sys.argv[3:]
    runpy.run_module(module, run_name="__main__", alter_sys=True)


def main() -> int:
    from PyQt6.QtGui import QIcon
    from PyQt6.QtWidgets import QApplication, QMessageBox

    from config.settings import Settings
    from core.download_history import DownloadHistory
    from core.download_manager import DownloadManager
    from core.download_queue import DownloadQueue
    from PyQt6.QtCore import Qt

    import app_info
    from gui.main_window import MainWindow
    from gui import i18n
    from gui.styles import build_qss
    from gui.wizard import SetupWizard
    from utils import system_info
    from utils.logger import setup_logging

    settings = Settings()
    setup_logging(system_info.log_path(), str(settings.get("log_level")))

    if sys.platform == "win32":      # own taskbar identity, so the taskbar shows our icon instead of python.exe's
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("VideoDownloader.App")  # type: ignore[attr-defined]
        except Exception:
            pass
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)        # MainWindow quits explicitly; hiding to the tray keeps it alive
    app.setApplicationName(app_info.APP_NAME)
    app.setApplicationVersion(app_info.VERSION)
    icon_file = system_info.resource_path("assets", "icons", "app.ico")
    if icon_file.is_file():
        app.setWindowIcon(QIcon(str(icon_file)))
    i18n.set_language(str(settings.get("language")))
    if i18n.is_rtl():
        app.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
    app.setStyleSheet(build_qss(str(settings.get("theme"))))

    db_path = system_info.database_path()
    history = DownloadHistory(db_path)
    if not history.ok:
        QMessageBox.warning(None, "Database", "The history database could not be opened. "
                                              "History will not be saved in this session.")
    queue = DownloadQueue(db_path)
    manager = DownloadManager(settings, history, queue)
    if not settings.get("first_run_done"):          # first-run setup: folder, FFmpeg, Deno, aria2c, language, theme, quality
        wizard = SetupWizard(settings)
        if wizard.exec():
            try:
                settings.update(wizard.values())
            except Exception:
                pass
        settings.update({"first_run_done": True})   # shown once, even when skipped
        manager.apply_settings()                    # pick up the FFmpeg path chosen in the wizard
        i18n.set_language(str(settings.get("language")))
        if i18n.is_rtl():
            app.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        app.setStyleSheet(build_qss(str(settings.get("theme"))))
    window = MainWindow(settings, history, manager)
    window.show()
    return app.exec()


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == RUN_MODULE_FLAG:
        _run_embedded_module()
        sys.exit(0)
    sys.exit(main())
