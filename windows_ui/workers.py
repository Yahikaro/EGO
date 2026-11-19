"""Qt signal boundary. Backend work never runs on the widget thread."""
from __future__ import annotations
import threading
from PySide6.QtCore import QObject, QRunnable, Signal, Slot


class Signals(QObject):
    progress = Signal(dict)
    finished = Signal(dict)


class Task(QRunnable):
    def __init__(self, operation):
        super().__init__()
        self.signals = Signals()
        self.cancel = threading.Event()
        self.operation = operation

    @Slot()
    def run(self):
        try:
            result = self.operation(self.signals.progress.emit, self.cancel)
        except Exception:
            # Exception strings may contain secrets or local request contents.
            result = {'status': 'FAIL', 'code': 'GUI_OPERATION_FAILED'}
        self.signals.finished.emit(result)
