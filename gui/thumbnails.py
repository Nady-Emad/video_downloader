"""Thumbnail loading for the GUI. Network and disk work happens in threads; pixmaps are built on the GUI thread."""
from __future__ import annotations

from typing import Dict, List, Optional, Set, Tuple

from PyQt6.QtCore import QObject, QRectF, QSize, Qt, QThread, pyqtSignal
from PyQt6.QtGui import QColor, QGuiApplication, QPainter, QPainterPath, QPixmap

from gui.styles import MUTED, SURFACE_ALT
from utils.network import fetch_image
from utils.system_info import cache_dir


def _ratio() -> float:
    screen = QGuiApplication.primaryScreen()
    return float(screen.devicePixelRatio()) if screen is not None else 1.0


def _rounded(source: QPixmap, width: int, height: int, radius: int) -> QPixmap:
    ratio = _ratio()
    out = QPixmap(int(width * ratio), int(height * ratio))
    out.setDevicePixelRatio(ratio)
    out.fill(Qt.GlobalColor.transparent)
    painter = QPainter(out)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    path = QPainterPath()
    path.addRoundedRect(QRectF(0, 0, width, height), radius, radius)
    painter.setClipPath(path)
    painter.drawPixmap(0, 0, source)
    painter.end()
    return out


def placeholder_pixmap(width: int, height: int, radius: int = 8, text: str = "") -> QPixmap:
    """Neutral rounded box shown while a thumbnail is missing or loading."""
    ratio = _ratio()
    base = QPixmap(int(width * ratio), int(height * ratio))
    base.setDevicePixelRatio(ratio)
    base.fill(QColor(SURFACE_ALT))
    if text:
        painter = QPainter(base)
        painter.setPen(QColor(MUTED))
        painter.drawText(QRectF(0, 0, width, height), int(Qt.AlignmentFlag.AlignCenter), text)
        painter.end()
    return _rounded(base, width, height, radius)


def with_duration_badge(pixmap: QPixmap, text: str) -> QPixmap:
    """Copy of `pixmap` with a small dark 'm:ss' badge in the bottom-left corner (like video sites do)."""
    if not text:
        return pixmap
    out = QPixmap(pixmap)                       # copy; the cached pixmap stays untouched
    ratio = out.devicePixelRatio() or 1.0
    width, height = out.width() / ratio, out.height() / ratio
    painter = QPainter(out)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    font = painter.font()
    font.setPixelSize(max(9, int(height * 0.2)))      # pixel size: avoids Qt's "point size <= 0" warning
    font.setBold(True)
    painter.setFont(font)
    metrics = painter.fontMetrics()
    badge_w = metrics.horizontalAdvance(text) + 8
    badge_h = metrics.height() + 2
    rect = QRectF(4, height - badge_h - 4, badge_w, badge_h)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(0, 0, 0, 200))
    painter.drawRoundedRect(rect, 4, 4)
    painter.setPen(QColor("white"))
    painter.drawText(rect, int(Qt.AlignmentFlag.AlignCenter), text)
    painter.end()
    return out


class _FetchThread(QThread):
    done = pyqtSignal(str, str)     # url, local path ('' when it failed)

    def __init__(self, url: str, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self.url = url

    def run(self) -> None:
        path = fetch_image(self.url, cache_dir("thumbnails"))
        self.done.emit(self.url, str(path) if path else "")


class ThumbnailCache(QObject):
    """Requests thumbnails once, caches them on disk and as scaled pixmaps."""

    ready = pyqtSignal(str)         # url

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._paths: Dict[str, str] = {}
        self._failed: Set[str] = set()
        self._pending: Set[str] = set()
        self._threads: List[_FetchThread] = []
        self._waiting: List[str] = []           # queued URLs; at most MAX_PARALLEL downloads run at once
        self._scaled: Dict[Tuple[str, int, int, int], QPixmap] = {}

    def request(self, url: str) -> None:
        if not url or url in self._paths or url in self._pending or url in self._failed:
            return
        self._pending.add(url)
        self._waiting.append(url)
        self._pump()

    MAX_PARALLEL = 8

    def prefetch(self, urls) -> None:
        """Queue many thumbnails (e.g. a whole playlist) so they are ready before the user scrolls to them."""
        for url in urls:
            self.request(url)

    def _pump(self) -> None:
        while self._waiting and len(self._threads) < self.MAX_PARALLEL:
            url = self._waiting.pop(0)
            thread = _FetchThread(url, self)
            thread.done.connect(self._on_done)
            thread.finished.connect(self._on_thread_finished)
            self._threads.append(thread)
            thread.start()

    def _on_done(self, url: str, path: str) -> None:
        self._pending.discard(url)
        if path:
            self._paths[url] = path
            self.ready.emit(url)
        else:
            self._failed.add(url)

    def _on_thread_finished(self) -> None:
        thread = self.sender()
        if isinstance(thread, _FetchThread):
            if thread in self._threads:
                self._threads.remove(thread)
            thread.deleteLater()
        self._pump()

    def pixmap(self, url: str, width: int, height: int, radius: int = 8) -> Optional[QPixmap]:
        """Cover-cropped rounded pixmap, or None while the thumbnail is not available."""
        if not url:
            return None
        key = (url, width, height, radius)
        if key in self._scaled:
            return self._scaled[key]
        path = self._paths.get(url)
        if not path:
            self.request(url)
            return None
        source = QPixmap(path)
        if source.isNull():
            self._failed.add(url)
            self._paths.pop(url, None)
            return None
        ratio = _ratio()
        target = QSize(int(width * ratio), int(height * ratio))
        scaled = source.scaled(target, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                               Qt.TransformationMode.SmoothTransformation)
        cropped = scaled.copy((scaled.width() - target.width()) // 2, (scaled.height() - target.height()) // 2,
                              target.width(), target.height())
        cropped.setDevicePixelRatio(ratio)
        result = _rounded(cropped, width, height, radius)
        self._scaled[key] = result
        return result

    def local_pixmap(self, path: str, width: int, height: int, radius: int = 8) -> Optional[QPixmap]:
        """Same cover-crop rounded pixmap for an image file on disk (saved thumbnails, library previews)."""
        if not path:
            return None
        key = (path, width, height, radius)
        if key in self._scaled:
            return self._scaled[key]
        source = QPixmap(path)
        if source.isNull():
            return None
        ratio = _ratio()
        target = QSize(int(width * ratio), int(height * ratio))
        scaled = source.scaled(target, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                               Qt.TransformationMode.SmoothTransformation)
        cropped = scaled.copy((scaled.width() - target.width()) // 2, (scaled.height() - target.height()) // 2,
                              target.width(), target.height())
        cropped.setDevicePixelRatio(ratio)
        result = _rounded(cropped, width, height, radius)
        if len(self._scaled) > 600:                 # bound memory on very large libraries
            self._scaled.clear()
        self._scaled[key] = result
        return result

    def shutdown(self) -> None:
        self._waiting.clear()
        for thread in list(self._threads):
            thread.wait(3000)
