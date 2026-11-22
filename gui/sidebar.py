"""Left navigation bar."""
from __future__ import annotations

from gui.i18n import tr

from typing import List, Optional

from PyQt6.QtCore import QSize, Qt, pyqtSignal
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QButtonGroup, QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

import app_info
from utils.system_info import resource_path


class Sidebar(QFrame):
    """Vertical list of checkable navigation buttons. `page_selected` carries the index of the chosen page."""

    page_selected = pyqtSignal(int)

    def __init__(self, labels: List[str], parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("sidebar")
        self.setFixedWidth(220)
        self._labels = list(labels)
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._buttons: List[QPushButton] = []

        logo = QLabel()
        icon_file = resource_path("assets", "icons", "app.png")
        if icon_file.is_file():
            logo.setPixmap(QIcon(str(icon_file)).pixmap(QSize(32, 32)))
        brand = QLabel(app_info.APP_NAME)
        brand.setObjectName("brand")
        head = QHBoxLayout()
        head.setSpacing(10)
        head.addWidget(logo)
        head.addWidget(brand, 1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 18, 14, 14)
        layout.setSpacing(4)
        layout.addLayout(head)
        layout.addSpacing(18)
        for index, label in enumerate(labels):
            button = QPushButton(tr(label))
            button.setObjectName("nav")
            button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setToolTip(f"Ctrl+{index + 1}")
            self._group.addButton(button, index)
            self._buttons.append(button)
            layout.addWidget(button)
        layout.addStretch(1)
        version = QLabel(f"v{app_info.VERSION}")
        version.setObjectName("version")
        layout.addWidget(version)
        self._group.idClicked.connect(self.page_selected)
        self.set_current(0)

    def set_current(self, index: int) -> None:
        if 0 <= index < len(self._buttons):
            self._buttons[index].setChecked(True)

    def set_badge(self, index: int, count: int) -> None:
        """Append a count to a button label (e.g. open tasks in the queue)."""
        if 0 <= index < len(self._buttons):
            base = tr(self._labels[index])
            self._buttons[index].setText(f"{base}  ({count})" if count else base)
