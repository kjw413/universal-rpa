from __future__ import annotations

from tests.helpers.validation_fakes import runtime_environment
from universal_rpa.adapters.windows.target_request import CursorTargetRequestFactory
from universal_rpa.domain.targets import RuntimeEnvironment


class SnapshotSpy:
    """Records which window the factory asked to describe."""

    def __init__(self) -> None:
        self.requested: list[int] = []

    def snapshot(self, hwnd: int) -> RuntimeEnvironment:
        self.requested.append(hwnd)
        return runtime_environment()


def test_the_request_describes_the_top_level_window_under_the_pointer() -> None:
    """The pointer lands on a child control, but capture needs its top-level window."""

    probe = SnapshotSpy()
    factory = CursorTargetRequestFactory(
        probe=probe,
        cursor_position=lambda: (640, 360),
        window_from_point=lambda x, y: 55,
        top_level_window=lambda hwnd: 40,
    )

    request = factory()

    assert (request.screen_x, request.screen_y) == (640, 360)
    assert probe.requested == [40]
    assert request.focused_runtime_id is None


def test_the_request_reads_the_pointer_once_per_capture() -> None:
    """A second capture must follow the mouse, not replay the first position."""

    positions = iter(((10, 20), (30, 40)))
    factory = CursorTargetRequestFactory(
        probe=SnapshotSpy(),
        cursor_position=lambda: next(positions),
        window_from_point=lambda x, y: 55,
        top_level_window=lambda hwnd: 55,
    )

    first = factory()
    second = factory()

    assert (first.screen_x, first.screen_y) == (10, 20)
    assert (second.screen_x, second.screen_y) == (30, 40)
