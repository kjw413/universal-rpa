"""UIA-first Windows target resolution with guarded coordinate fallback."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from universal_rpa.domain.errors import ErrorCode, RpaError
from universal_rpa.domain.targets import RuntimeEnvironment, TargetSpec, UiaSelector, WindowsTarget

from .environment import WindowsEnvironmentProbe
from .foreground import ForegroundGuard, WindowIdentity
from .window_catalog import PyWin32WindowFacade, Win32WindowFacade


class UiaSearchPort(Protocol):
    def find(self, hwnd: int, selector: UiaSelector) -> Iterable[object]: ...


class PywinautoUiaSearch:
    def find(self, hwnd: int, selector: UiaSelector) -> Iterable[object]:
        from pywinauto import Desktop  # type: ignore[import-untyped]

        criteria: dict[str, str] = {}
        if selector.automation_id:
            criteria["auto_id"] = selector.automation_id
        if selector.control_type:
            criteria["control_type"] = selector.control_type
        if selector.name:
            criteria["title"] = selector.name
        if selector.class_name:
            criteria["class_name"] = selector.class_name
        window = Desktop(backend="uia").window(handle=hwnd)
        return tuple(window.descendants(**criteria))


@dataclass(frozen=True, slots=True)
class ResolvedUiaTarget:
    window: WindowIdentity
    element: object


@dataclass(frozen=True, slots=True)
class ResolvedCoordinateTarget:
    window: WindowIdentity
    client_point: tuple[int, int]
    screen_point: tuple[int, int]


ResolvedTarget = ResolvedUiaTarget | ResolvedCoordinateTarget


class WindowsTargetResolver:
    def __init__(
        self,
        probe: WindowsEnvironmentProbe,
        guard: ForegroundGuard,
        *,
        win32: Win32WindowFacade | None = None,
        uia: UiaSearchPort | None = None,
    ) -> None:
        self._probe = probe
        self._guard = guard
        self._win32 = win32 or PyWin32WindowFacade()
        self._uia = uia or PywinautoUiaSearch()

    def resolve(
        self,
        target: TargetSpec,
        runtime: RuntimeEnvironment,
        *,
        has_postcondition_or_assertion: bool,
    ) -> ResolvedTarget:
        if target.adapter_id != "windows":
            raise RpaError(
                ErrorCode.INVALID_SCHEMA, "Windows 작업에는 Windows 대상만 사용할 수 있습니다."
            )
        try:
            captured = WindowsTarget.model_validate(target.payload)
        except Exception:
            raise RpaError(
                ErrorCode.INVALID_SCHEMA, "Windows 대상 정보가 올바르지 않습니다."
            ) from None
        current = self._probe.snapshot(runtime.top_level_hwnd)
        identity = WindowIdentity(
            process_id=current.process_id,
            process_executable=current.process_executable,
            top_level_hwnd=current.top_level_hwnd,
            window_class=current.window_class,
        )
        if captured.selector is not None:
            matches = tuple(self._uia.find(identity.top_level_hwnd, captured.selector))
            if len(matches) == 1:
                return ResolvedUiaTarget(identity, matches[0])
            if len(matches) > 1 and captured.coordinate_fallback is None:
                raise RpaError(ErrorCode.TARGET_AMBIGUOUS, "UI 요소가 여러 개 발견되었습니다.")
        if captured.coordinate_fallback is None:
            raise RpaError(ErrorCode.TARGET_NOT_FOUND, "UI 요소를 찾을 수 없습니다.")
        return self._resolve_coordinate(captured, current, identity, has_postcondition_or_assertion)

    def _resolve_coordinate(
        self,
        captured: WindowsTarget,
        current: RuntimeEnvironment,
        identity: WindowIdentity,
        has_postcondition_or_assertion: bool,
    ) -> ResolvedCoordinateTarget:
        fallback = captured.coordinate_fallback
        if fallback is None:  # defensive narrowing for type checkers
            raise RpaError(ErrorCode.TARGET_NOT_FOUND, "좌표 대상 정보가 없습니다.")
        same_executable = (
            Path(current.process_executable).name.casefold()
            == Path(fallback.recorded_process_executable).name.casefold()
        )
        width_ratio = (
            abs(current.client_width - fallback.recorded_client_width)
            / fallback.recorded_client_width
        )
        height_ratio = (
            abs(current.client_height - fallback.recorded_client_height)
            / fallback.recorded_client_height
        )
        if (
            not same_executable
            or current.window_class != fallback.recorded_window_class
            or current.dpi_x != fallback.recorded_dpi_x
            or current.dpi_y != fallback.recorded_dpi_y
            or width_ratio > 0.02
            or height_ratio > 0.02
            or not has_postcondition_or_assertion
        ):
            raise RpaError(
                ErrorCode.ENVIRONMENT_MISMATCH,
                "좌표 입력에 필요한 실행 환경이 기록 시점과 다릅니다.",
            )
        self._guard.verify(identity)
        x = round(fallback.point.x * (current.client_width - 1))
        y = round(fallback.point.y * (current.client_height - 1))
        if not (0 <= x < current.client_width and 0 <= y < current.client_height):
            raise RpaError(
                ErrorCode.ENVIRONMENT_MISMATCH, "좌표가 대상 창의 클라이언트 영역을 벗어났습니다."
            )
        geometry = self._win32.client_geometry(identity.top_level_hwnd)
        return ResolvedCoordinateTarget(identity, (x, y), (geometry.left + x, geometry.top + y))


__all__ = [
    "PywinautoUiaSearch",
    "ResolvedCoordinateTarget",
    "ResolvedTarget",
    "ResolvedUiaTarget",
    "UiaSearchPort",
    "WindowsTargetResolver",
]
