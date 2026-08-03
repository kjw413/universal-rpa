from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass

from PySide6.QtCore import QObject, Signal, Slot


@dataclass(frozen=True, slots=True)
class WorkerFailure:
    safe_message: str


class FunctionWorker(QObject):
    completed = Signal(object)
    failed = Signal(object)
    cancelled = Signal()
    finished = Signal()

    def __init__(self, operation: Callable[[threading.Event], object]) -> None:
        super().__init__()
        self._operation = operation
        self._cancelled = threading.Event()

    @Slot()
    def run(self) -> None:
        try:
            if self._cancelled.is_set():
                self.cancelled.emit()
                return
            result = self._operation(self._cancelled)
            if self._cancelled.is_set():
                self.cancelled.emit()
            else:
                self.completed.emit(result)
        except Exception:
            self.failed.emit(WorkerFailure("작업을 완료하지 못했습니다."))
        finally:
            self.finished.emit()

    @Slot()
    def cancel(self) -> None:
        self._cancelled.set()


__all__ = ["FunctionWorker", "WorkerFailure"]
