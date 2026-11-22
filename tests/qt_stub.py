"""Minimal stand-in for PyQt6.QtCore so the non-visual Qt code (manager + workers) can be tested
headlessly. Signals call their slots synchronously; QThread wraps threading.Thread."""
from __future__ import annotations

import sys
import threading
import types

_current_sender = threading.local()


class _BoundSignal:
    def __init__(self, owner):
        self._owner = owner
        self._slots = []

    def connect(self, slot):
        # PyQt allows connecting a signal directly to another signal.
        self._slots.append(slot.emit if isinstance(slot, _BoundSignal) else slot)

    def emit(self, *args):
        for slot in list(self._slots):
            _current_sender.obj = self._owner
            try:
                slot(*args)
            finally:
                _current_sender.obj = None


class pyqtSignal:  # noqa: N801
    def __init__(self, *types_):
        self._name = None

    def __set_name__(self, owner, name):
        self._name = name

    def __get__(self, obj, objtype=None):
        if obj is None:
            return self
        store = obj.__dict__.setdefault("_signals", {})
        if self._name not in store:
            store[self._name] = _BoundSignal(obj)
        return store[self._name]


class QObject:
    def __init__(self, parent=None):
        self._parent = parent

    def sender(self):
        return getattr(_current_sender, "obj", None)

    def deleteLater(self):
        pass


class QThread(QObject):
    finished = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._thread = None

    def start(self):
        def target():
            try:
                self.run()
            finally:
                self.finished.emit()
        self._thread = threading.Thread(target=target, daemon=True)
        self._thread.start()

    def wait(self, timeout=None):
        if self._thread:
            self._thread.join((timeout or 10000) / 1000)
        return True

    def isRunning(self):
        return bool(self._thread and self._thread.is_alive())


class QTimer(QObject):
    timeout = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._active = False

    def setInterval(self, ms):
        pass

    def start(self):
        self._active = True

    def stop(self):
        self._active = False

    def isActive(self):
        return self._active


def install():
    pkg = types.ModuleType("PyQt6")
    core = types.ModuleType("PyQt6.QtCore")
    core.QObject, core.QThread, core.pyqtSignal, core.QTimer = QObject, QThread, pyqtSignal, QTimer
    pkg.QtCore = core
    sys.modules["PyQt6"] = pkg
    sys.modules["PyQt6.QtCore"] = core
