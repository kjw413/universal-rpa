"""Cooperative pause and cancellation control for a workflow run."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable

from universal_rpa.domain.errors import ErrorCode, RpaError
from universal_rpa.ports.automation import CancellationToken


class RunControl(CancellationToken):
    """Thread-safe control object shared by runner, adapters, waits and retries.

    Cancellation has priority over pause.  This is intentional: an emergency
    stop must wake a paused worker immediately instead of waiting for a resume.
    """

    def __init__(self, monotonic: Callable[[], float] = time.monotonic) -> None:
        super().__init__()
        self._paused = False
        self._monotonic = monotonic
        self._deadline: float | None = None
        self._condition = threading.Condition()

    def configure_deadline(self, max_runtime_seconds: int) -> None:
        if max_runtime_seconds <= 0:
            raise ValueError("max runtime must be positive")
        with self._condition:
            self._deadline = self._monotonic() + max_runtime_seconds
            self._condition.notify_all()

    def pause(self) -> None:
        with self._condition:
            if not self.is_cancelled():
                self._paused = True

    def resume(self) -> None:
        with self._condition:
            self._paused = False
            self._condition.notify_all()

    def cancel(self) -> None:
        super().cancel()
        with self._condition:
            self._paused = False
            self._condition.notify_all()

    @property
    def is_paused(self) -> bool:
        with self._condition:
            return self._paused

    def wait_if_paused(self) -> None:
        with self._condition:
            while self._paused and not self.is_cancelled():
                remaining = self._remaining_seconds()
                if remaining is not None and remaining <= 0:
                    break
                self._condition.wait(timeout=remaining)
        self.raise_if_cancelled()

    def raise_if_cancelled(self) -> None:
        if self.is_cancelled():
            raise RpaError(ErrorCode.CANCELLED, "실행이 취소되었습니다.")
        remaining = self._remaining_seconds()
        if remaining is not None and remaining <= 0:
            raise RpaError(ErrorCode.CONDITION_TIMEOUT, "업무의 최대 실행 시간을 초과했습니다.")

    def _remaining_seconds(self) -> float | None:
        deadline = self._deadline
        return None if deadline is None else deadline - self._monotonic()


__all__ = ["RunControl"]
