from __future__ import annotations

import pytest

from universal_rpa.adapters.windows.dpi import (
    DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2,
    _reset_dpi_awareness_for_test,
    enable_per_monitor_v2_dpi_awareness,
)


class FakeDpiApi:
    def __init__(self, *, succeeds: bool, error: int = 0, awareness: int = 0) -> None:
        self.succeeds = succeeds
        self.error = error
        self.awareness = awareness
        self.calls: list[int] = []

    def set_process_dpi_awareness_context(self, context: int) -> bool:
        self.calls.append(context)
        return self.succeeds

    def get_last_error(self) -> int:
        return self.error

    def get_process_dpi_awareness(self) -> int:
        return self.awareness


@pytest.fixture(autouse=True)
def reset_dpi_state() -> None:
    _reset_dpi_awareness_for_test()


def test_per_monitor_v2_is_enabled_only_once() -> None:
    api = FakeDpiApi(succeeds=True)
    enable_per_monitor_v2_dpi_awareness(api)
    enable_per_monitor_v2_dpi_awareness(api)
    assert api.calls == [DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2]


def test_access_denied_is_accepted_only_when_process_is_already_per_monitor() -> None:
    api = FakeDpiApi(succeeds=False, error=5, awareness=2)
    enable_per_monitor_v2_dpi_awareness(api)
    assert len(api.calls) == 1


def test_unexpected_dpi_failure_is_reported() -> None:
    api = FakeDpiApi(succeeds=False, error=87, awareness=0)
    with pytest.raises(OSError):
        enable_per_monitor_v2_dpi_awareness(api)


def test_a_second_attempt_accepts_an_already_aware_process() -> None:
    """Exercises the real ctypes boundary the FakeDpiApi tests cannot reach.

    Once the process is per-monitor aware -- because this call just made it
    so, or because Qt or a packaged app's manifest did --
    SetProcessDpiAwarenessContext fails with ERROR_ACCESS_DENIED, and the
    fallback has to recognise that. It can only do so if the error is
    actually readable, which needs a handle opened with use_last_error.
    """

    try:
        enable_per_monitor_v2_dpi_awareness()
    except OSError as error:  # pragma: no cover - environment without DPI APIs
        pytest.skip(f"process DPI awareness cannot be established here: {error}")
    _reset_dpi_awareness_for_test()

    enable_per_monitor_v2_dpi_awareness()
