"""Failure screenshots are tied to the observed window and fail closed."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QImage

from universal_rpa.domain.targets import (
    CoordinateFallback,
    NormalizedRect,
    RelativePoint,
    RuntimeEnvironment,
    TargetSpec,
    UiaSelector,
    WindowsTarget,
)
from universal_rpa.infrastructure.screenshots import ClientCapture, FailureScreenshotService
from universal_rpa.infrastructure.sensitive_regions import SensitiveRegionProvider


def runtime_environment(
    *,
    process_id: int = 41,
    top_level_hwnd: int = 901,
    client_width: int = 200,
    client_height: int = 100,
) -> RuntimeEnvironment:
    return RuntimeEnvironment(
        interactive_desktop=True,
        process_id=process_id,
        process_executable="mis.exe",
        top_level_hwnd=top_level_hwnd,
        window_title="MIS",
        window_class="MisMainWindow",
        foreground_hwnd=top_level_hwnd,
        dpi_x=96,
        dpi_y=96,
        client_width=client_width,
        client_height=client_height,
        monitor_scale=1.0,
    )


def windows_target(
    *,
    mandatory_sensitive_regions: tuple[NormalizedRect, ...] = (),
    user_sensitive_regions: tuple[NormalizedRect, ...] = (),
    selector_only: bool = False,
) -> TargetSpec:
    target = WindowsTarget(
        selector=UiaSelector(automation_id="grid"),
        coordinate_fallback=None
        if selector_only
        else CoordinateFallback(
            recorded_process_executable="mis.exe",
            recorded_window_class="MisMainWindow",
            point=RelativePoint(x=0.5, y=0.5),
            recorded_dpi_x=96,
            recorded_dpi_y=96,
            recorded_client_width=200,
            recorded_client_height=100,
        ),
        mandatory_sensitive_regions=mandatory_sensitive_regions,
        user_sensitive_regions=user_sensitive_regions,
    )
    return TargetSpec.model_validate(
        {"adapter_id": "windows", "payload": target.model_dump(mode="json")}
    )


def selector_only_target() -> TargetSpec:
    return windows_target(selector_only=True)


def white_image(width: int, height: int) -> QImage:
    image = QImage(width, height, QImage.Format.Format_RGB32)
    image.fill(QColor(Qt.GlobalColor.white))
    return image


class SpyExactWindowCapture:
    def __init__(
        self,
        runtime: RuntimeEnvironment,
        *,
        width: int | None = None,
        height: int | None = None,
        process_id: int | None = None,
        hwnd: int | None = None,
        client_screen: tuple[int, int] = (0, 0),
        result_is_none: bool = False,
    ) -> None:
        self._runtime = runtime
        self._width = width if width is not None else runtime.client_width
        self._height = height if height is not None else runtime.client_height
        self._process_id = process_id if process_id is not None else runtime.process_id
        self._hwnd = hwnd if hwnd is not None else runtime.top_level_hwnd
        self._client_screen = client_screen
        self._result_is_none = result_is_none
        self.calls: list[tuple[int, int]] = []
        self.executable_or_class_queries: list[str] = []

    def capture_client(self, process_id: int, hwnd: int) -> ClientCapture | None:
        self.calls.append((process_id, hwnd))
        if self._result_is_none:
            return None
        return ClientCapture(
            process_id=self._process_id,
            hwnd=self._hwnd,
            client_screen_x=self._client_screen[0],
            client_screen_y=self._client_screen[1],
            width=self._width,
            height=self._height,
            image=white_image(self._width, self._height),
        )


class FakePasswordRegions:
    def __init__(self, *, hwnd: int, screen_rects: tuple[tuple[int, int, int, int], ...]) -> None:
        self._hwnd = hwnd
        self._screen_rects = screen_rects
        self.calls: list[int] = []

    def password_screen_rects(self, hwnd: int) -> Iterable[tuple[int, int, int, int]]:
        self.calls.append(hwnd)
        return self._screen_rects if hwnd == self._hwnd else ()


def screenshot_service(
    capture: SpyExactWindowCapture,
    *,
    password_probe: FakePasswordRegions | None = None,
) -> FailureScreenshotService:
    return FailureScreenshotService(
        capture=capture,
        regions=SensitiveRegionProvider(password_probe=password_probe),
    )


def test_failure_masks_mandatory_user_and_live_password_regions(tmp_path: Path) -> None:
    runtime = runtime_environment()
    service = FailureScreenshotService(
        capture=SpyExactWindowCapture(runtime),
        regions=SensitiveRegionProvider(
            password_probe=FakePasswordRegions(hwnd=901, screen_rects=((50, 20, 20, 10),))
        ),
    )
    target = windows_target(
        mandatory_sensitive_regions=(NormalizedRect(x=0.10, y=0.10, width=0.10, height=0.20),),
        user_sensitive_regions=(NormalizedRect(x=0.70, y=0.10, width=0.10, height=0.20),),
    )

    path = service.capture_failure(target, runtime, tmp_path / "failure.png")

    assert path is not None
    image = QImage(str(path))
    assert image.pixelColor(25, 15) == QColor(Qt.GlobalColor.black)
    assert image.pixelColor(145, 15) == QColor(Qt.GlobalColor.black)
    assert image.pixelColor(55, 25) == QColor(Qt.GlobalColor.black)
    assert image.pixelColor(100, 70) == QColor(Qt.GlobalColor.white)


def test_selector_only_target_uses_observed_pid_hwnd_without_reresolution(
    tmp_path: Path,
) -> None:
    runtime = runtime_environment()
    capture = SpyExactWindowCapture(runtime)
    service = screenshot_service(capture)

    service.capture_failure(selector_only_target(), runtime, tmp_path / "failure.png")

    assert capture.calls == [(41, 901)]
    assert capture.executable_or_class_queries == []


@pytest.mark.parametrize("field", ["process_id", "top_level_hwnd", "client_size"])
def test_runtime_identity_or_client_basis_mismatch_fails_closed(
    tmp_path: Path, field: str
) -> None:
    runtime = runtime_environment()
    overrides: dict[str, object] = {
        "process_id": {"process_id": 42},
        "top_level_hwnd": {"hwnd": 902},
        "client_size": {"width": 320},
    }[field]
    capture = SpyExactWindowCapture(runtime, **overrides)  # type: ignore[arg-type]
    service = screenshot_service(capture)
    destination = tmp_path / "failure.png"

    assert service.capture_failure(windows_target(), runtime, destination) is None
    assert not destination.exists()
    assert tuple(tmp_path.iterdir()) == ()


def test_unavailable_capture_writes_no_image(tmp_path: Path) -> None:
    runtime = runtime_environment()
    service = screenshot_service(SpyExactWindowCapture(runtime, result_is_none=True))
    destination = tmp_path / "failure.png"

    assert service.capture_failure(windows_target(), runtime, destination) is None
    assert tuple(tmp_path.iterdir()) == ()


def test_probe_failure_writes_no_image(tmp_path: Path) -> None:
    class ExplodingProbe:
        def password_screen_rects(self, hwnd: int) -> Iterable[tuple[int, int, int, int]]:
            raise OSError(f"probe unavailable for {hwnd}")

    runtime = runtime_environment()
    service = FailureScreenshotService(
        capture=SpyExactWindowCapture(runtime),
        regions=SensitiveRegionProvider(password_probe=ExplodingProbe()),
    )
    destination = tmp_path / "failure.png"

    assert service.capture_failure(windows_target(), runtime, destination) is None
    assert tuple(tmp_path.iterdir()) == ()


def test_password_regions_outside_the_client_are_clipped(tmp_path: Path) -> None:
    runtime = runtime_environment()
    service = FailureScreenshotService(
        capture=SpyExactWindowCapture(runtime, client_screen=(1000, 500)),
        regions=SensitiveRegionProvider(
            password_probe=FakePasswordRegions(hwnd=901, screen_rects=((1190, 590, 40, 40),))
        ),
    )

    path = service.capture_failure(windows_target(), runtime, tmp_path / "failure.png")

    assert path is not None
    image = QImage(str(path))
    assert image.pixelColor(195, 95) == QColor(Qt.GlobalColor.black)
    assert image.pixelColor(150, 50) == QColor(Qt.GlobalColor.white)


def test_non_windows_target_is_not_captured(tmp_path: Path) -> None:
    runtime = runtime_environment()
    capture = SpyExactWindowCapture(runtime)
    service = screenshot_service(capture)
    target = TargetSpec(adapter_id="clipboard", payload={"any": 1})

    assert service.capture_failure(target, runtime, tmp_path / "failure.png") is None
    assert capture.calls == []
    assert tuple(tmp_path.iterdir()) == ()
