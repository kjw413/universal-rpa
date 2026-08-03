from __future__ import annotations

import struct
import threading
from collections import deque
from collections.abc import Callable, Iterable
from typing import Any, Protocol, cast

from universal_rpa.domain.recording import (
    EventFocusSnapshot,
    NativeInputEvent,
    RecordingEnvironmentSnapshot,
    RecordingTarget,
    TargetSnapshot,
    WindowContextSnapshot,
)
from universal_rpa.domain.targets import (
    CoordinateFallback,
    NormalizedRect,
    RelativePoint,
    TargetSpec,
    UiaSelector,
    WindowsTarget,
)
from universal_rpa.ports.automation import (
    CancellationToken,
    TargetCaptureRequest,
    TargetCaptureResult,
)
from universal_rpa.ports.context import CapturedEventContext

from .window_catalog import (
    ClientGeometry,
    Win32WindowCatalog,
    Win32WindowFacade,
)


class UiaFacade(Protocol):
    def element_from_runtime_id(self, runtime_id: tuple[int, ...]) -> object | None: ...

    def elements_from_point(self, screen_x: int, screen_y: int) -> Iterable[object]: ...

    def password_elements(self, top_level_hwnd: int) -> Iterable[object]: ...


class ScreenshotCapturePort(Protocol):
    def capture_client_png(self, hwnd: int, width: int, height: int) -> bytes: ...


class UiaFocusCache:
    """Thread-safe immutable focus history published outside input callbacks."""

    def __init__(self, initial: EventFocusSnapshot) -> None:
        self._current = initial
        self._history: deque[EventFocusSnapshot] = deque((initial,), maxlen=256)
        self._condition = threading.Condition()

    def snapshot(self) -> EventFocusSnapshot:
        with self._condition:
            return self._current

    def publish(self, snapshot: EventFocusSnapshot) -> None:
        with self._condition:
            if snapshot.cache_generation <= self._current.cache_generation:
                raise ValueError("focus cache generation must increase")
            self._current = snapshot
            self._history.append(snapshot)
            self._condition.notify_all()

    def next_generation(self) -> int:
        with self._condition:
            return self._current.cache_generation + 1

    def wait_and_confirm(
        self,
        captured: EventFocusSnapshot,
        *,
        input_hook_time_ms: int,
        settle_timeout_seconds: float,
    ) -> bool:
        with self._condition:
            if settle_timeout_seconds > 0:
                self._condition.wait(settle_timeout_seconds)
            if not captured.cache_confirmed:
                return False
            if captured.focus_event_time_ms > input_hook_time_ms:
                return False
            return not any(
                transition.cache_generation > captured.cache_generation
                and transition.focus_event_time_ms <= input_hook_time_ms
                for transition in self._history
            )


class ContextConfirmationBarrier:
    def __init__(
        self,
        cache: UiaFocusCache,
        *,
        settle_timeout_seconds: float = 0.01,
    ) -> None:
        if settle_timeout_seconds < 0:
            raise ValueError("settle timeout must be nonnegative")
        self._cache = cache
        self._settle_timeout_seconds = settle_timeout_seconds

    def confirm(self, event: NativeInputEvent) -> bool:
        return self._cache.wait_and_confirm(
            event.focus,
            input_hook_time_ms=event.hook_time_ms,
            settle_timeout_seconds=self._settle_timeout_seconds,
        )


class FocusContextWatcher:
    def __init__(
        self,
        cache: UiaFocusCache,
        *,
        source_start: Callable[[Callable[..., None]], None] | None = None,
        source_stop: Callable[[], None] | None = None,
    ) -> None:
        self._cache = cache
        self._source_start = source_start
        self._source_stop = source_stop

    def start(self) -> None:
        if self._source_start is not None:
            self._source_start(self.publish_focus)

    def stop(self) -> None:
        if self._source_stop is not None:
            self._source_stop()

    def publish_focus(
        self,
        *,
        foreground_hwnd: int,
        focused_hwnd: int | None,
        process_id: int,
        runtime_id: tuple[int, ...] | None,
        event_time_ms: int,
        confirmed: bool,
    ) -> None:
        self._cache.publish(
            EventFocusSnapshot(
                foreground_hwnd=foreground_hwnd,
                focused_hwnd=focused_hwnd,
                foreground_process_id=process_id,
                cached_uia_runtime_id=runtime_id,
                focus_event_time_ms=event_time_ms,
                cache_generation=self._cache.next_generation(),
                cache_confirmed=confirmed,
            )
        )


_MISSING = object()


def _member(value: object, name: str, default: object = _MISSING) -> object:
    result = getattr(value, name, default)
    if result is _MISSING:
        raise AttributeError(name)
    return result


def _optional_member(value: object, *names: str, default: object = None) -> object:
    for name in names:
        result = getattr(value, name, _MISSING)
        if result is not _MISSING:
            return result() if callable(result) else result
    return default


def _bool_member(value: object, *names: str, default: bool = False) -> bool:
    return bool(_optional_member(value, *names, default=default))


def _runtime_id(element: object) -> tuple[int, ...] | None:
    value = _optional_member(element, "runtime_id", "get_runtime_id")
    if value is None:
        return None
    return tuple(int(cast(Any, item)) for item in cast(Iterable[object], value))


def _selector_candidates(element: object) -> tuple[UiaSelector, ...]:
    supplied = _optional_member(element, "selector_candidates")
    if supplied is not None:
        return tuple(UiaSelector.model_validate(item) for item in cast(Iterable[object], supplied))
    automation_id = _optional_member(element, "automation_id")
    control_type = _optional_member(element, "control_type", default="Unknown")
    name = _optional_member(element, "name")
    class_name = _optional_member(element, "class_name")
    return (
        UiaSelector(
            automation_id=str(automation_id) if automation_id else None,
            control_type=str(control_type) if control_type else "Unknown",
            name=str(name) if name else None,
            class_name=str(class_name) if class_name else None,
        ),
    )


def _element_bounds(element: object) -> tuple[int, int, int, int] | None:
    value = _optional_member(element, "bounds", "bounding_rectangle")
    if value is None:
        return None
    if all(hasattr(value, name) for name in ("left", "top", "right", "bottom")):
        return (
            int(cast(Any, _member(value, "left"))),
            int(cast(Any, _member(value, "top"))),
            int(cast(Any, _member(value, "right"))),
            int(cast(Any, _member(value, "bottom"))),
        )
    items = tuple(int(cast(Any, item)) for item in cast(Iterable[object], value))
    return items if len(items) == 4 else None


def _normalized_rect(
    bounds: tuple[int, int, int, int] | None,
    client: ClientGeometry | None,
) -> NormalizedRect | None:
    if bounds is None or client is None:
        return None
    left, top, right, bottom = bounds
    clipped_left = max(left, client.left)
    clipped_top = max(top, client.top)
    clipped_right = min(right, client.left + client.width)
    clipped_bottom = min(bottom, client.top + client.height)
    if clipped_right <= clipped_left or clipped_bottom <= clipped_top:
        return None
    return NormalizedRect(
        x=(clipped_left - client.left) / client.width,
        y=(clipped_top - client.top) / client.height,
        width=(clipped_right - clipped_left) / client.width,
        height=(clipped_bottom - clipped_top) / client.height,
    )


def _observed_value(element: object, *, editable: bool, is_password: bool) -> str | None:
    if is_password or not editable:
        return None
    direct = getattr(element, "get_value", _MISSING)
    if direct is not _MISSING:
        value = cast(Callable[[], object], direct)()
        return None if value is None else str(value)
    pattern = getattr(element, "value_pattern", _MISSING)
    if pattern is not _MISSING and pattern is not None:
        getter = getattr(pattern, "get_value", _MISSING)
        if getter is not _MISSING:
            value = cast(Callable[[], object], getter)()
            return None if value is None else str(value)
    value = getattr(element, "value", None)
    return None if value is None else str(value)


def capture_target_snapshot(
    element: object,
    *,
    client: ClientGeometry | None = None,
) -> TargetSnapshot:
    is_password = _bool_member(element, "is_password", "get_is_password")
    control_type = str(_optional_member(element, "control_type", default=""))
    editable = _bool_member(
        element,
        "editable",
        "is_editable",
        default=control_type.casefold() in {"edit", "document"},
    )
    return TargetSnapshot(
        selector_candidates=_selector_candidates(element),
        focused_runtime_id=_runtime_id(element),
        editable=editable,
        is_password=is_password,
        observed_value=_observed_value(
            element,
            editable=editable,
            is_password=is_password,
        ),
        bounds=_normalized_rect(_element_bounds(element), client),
    )


def _png_size(payload: bytes) -> tuple[int, int]:
    if len(payload) < 24 or not payload.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("capture did not return a PNG")
    return struct.unpack(">II", payload[16:24])


class WindowsWindowContext:
    def __init__(
        self,
        *,
        win32: Win32WindowFacade,
        uia: UiaFacade,
        focus_cache: UiaFocusCache | None = None,
        screenshot: ScreenshotCapturePort | None = None,
        catalog: Win32WindowCatalog | None = None,
        settle_timeout_seconds: float = 0.01,
    ) -> None:
        self._win32 = win32
        self._uia = uia
        self._screenshot = screenshot
        self._catalog = catalog or Win32WindowCatalog(win32)
        self._barrier = (
            ContextConfirmationBarrier(
                focus_cache,
                settle_timeout_seconds=settle_timeout_seconds,
            )
            if focus_cache is not None
            else None
        )

    def list_recordable_windows(self) -> tuple[RecordingTarget, ...]:
        return self._catalog.list_recordable_windows()

    def capture_context(
        self,
        event: NativeInputEvent,
        selected: RecordingTarget,
    ) -> CapturedEventContext:
        client = self._win32.client_geometry(selected.top_level_hwnd)
        dpi_x, dpi_y = self._win32.window_dpi(selected.top_level_hwnd)
        top_level = self._win32.top_level_window(event.focus.foreground_hwnd)
        owned = top_level == selected.top_level_hwnd or self._win32.is_owned_by(
            top_level,
            selected.top_level_hwnd,
        )
        process_id = self._win32.window_process_id(top_level)
        cache_confirmed = (
            self._barrier.confirm(event)
            if self._barrier is not None
            else event.focus.cache_confirmed
        )
        identity_matches = process_id == event.focus.foreground_process_id
        runtime_id = event.focus.cached_uia_runtime_id
        element: object | None = None
        if cache_confirmed and identity_matches and runtime_id is not None:
            element = self._uia.element_from_runtime_id(runtime_id)
            if element is not None and _runtime_id(element) != runtime_id:
                element = None
        confident = cache_confirmed and identity_matches and element is not None
        target_snapshot = (
            capture_target_snapshot(element, client=client) if element is not None else None
        )

        window_context = WindowContextSnapshot(
            foreground_hwnd=event.focus.foreground_hwnd,
            focused_hwnd=event.focus.focused_hwnd,
            process_id=process_id,
            process_executable=self._win32.process_executable(process_id),
            top_level_hwnd=top_level,
            window_title=self._win32.window_text(top_level),
            window_class=self._win32.window_class(top_level),
            focused_runtime_id=runtime_id,
            selected_top_level_hwnd=selected.top_level_hwnd,
            owned_by_selected_window=owned,
            context_confident=confident,
        )
        environment = RecordingEnvironmentSnapshot(
            client_left=client.left,
            client_top=client.top,
            client_width=client.width,
            client_height=client.height,
            dpi_x=dpi_x,
            dpi_y=dpi_y,
            monitor_scale=dpi_x / 96.0,
            monitor_id=self._win32.monitor_id(selected.top_level_hwnd),
            double_click_time_ms=self._optional_win32_int("double_click_time_ms", 500),
            drag_width_px=self._optional_win32_int("drag_width_px", 4),
            drag_height_px=self._optional_win32_int("drag_height_px", 4),
        )
        return CapturedEventContext(
            window_context=window_context,
            target_snapshot=target_snapshot,
            environment_snapshot=environment,
            in_scope=owned,
        )

    def capture_target(
        self,
        request: TargetCaptureRequest,
        cancellation: CancellationToken,
    ) -> TargetCaptureResult:
        cancellation.raise_if_cancelled()
        runtime = request.runtime
        client = self._win32.client_geometry(runtime.top_level_hwnd)
        if (client.width, client.height) != (runtime.client_width, runtime.client_height):
            raise ValueError("live client dimensions differ from the capture request")
        elements = tuple(self._uia.elements_from_point(request.screen_x, request.screen_y))
        mandatory_regions = self._password_regions(runtime.top_level_hwnd, client)
        candidates: list[TargetSpec] = []
        for element in elements:
            cancellation.raise_if_cancelled()
            snapshot = capture_target_snapshot(element, client=client)
            region = snapshot.bounds
            point = RelativePoint(
                x=min(max((request.screen_x - client.left) / client.width, 0.0), 1.0),
                y=min(max((request.screen_y - client.top) / client.height, 0.0), 1.0),
            )
            own_mandatory = mandatory_regions
            if region is not None and (snapshot.is_password or snapshot.editable):
                own_mandatory = tuple(dict.fromkeys((*own_mandatory, region)))
            target = WindowsTarget(
                selector=(
                    snapshot.selector_candidates[0] if snapshot.selector_candidates else None
                ),
                coordinate_fallback=CoordinateFallback(
                    recorded_process_executable=runtime.process_executable,
                    recorded_window_class=runtime.window_class,
                    point=point,
                    recorded_dpi_x=runtime.dpi_x,
                    recorded_dpi_y=runtime.dpi_y,
                    recorded_client_width=runtime.client_width,
                    recorded_client_height=runtime.client_height,
                ),
                target_region=region,
                mandatory_sensitive_regions=own_mandatory,
                user_sensitive_regions=(),
                diagnostic_absolute_x=request.screen_x,
                diagnostic_absolute_y=request.screen_y,
            )
            candidates.append(
                TargetSpec.model_validate(
                    {"adapter_id": "windows", "payload": target.model_dump(mode="json")}
                )
            )

        preview_png: bytes | None = None
        if self._screenshot is not None:
            preview_png = bytes(
                self._screenshot.capture_client_png(
                    runtime.top_level_hwnd,
                    runtime.client_width,
                    runtime.client_height,
                )
            )
            if _png_size(preview_png) != (runtime.client_width, runtime.client_height):
                raise ValueError("capture dimensions do not match the requested client")
        frozen_candidates = tuple(candidates)
        return TargetCaptureResult(
            target=frozen_candidates[0] if frozen_candidates else None,
            candidates=frozen_candidates,
            preview_png=preview_png,
        )

    def _password_regions(
        self,
        top_level_hwnd: int,
        client: ClientGeometry,
    ) -> tuple[NormalizedRect, ...]:
        regions: list[NormalizedRect] = []
        for element in self._uia.password_elements(top_level_hwnd):
            if not _bool_member(element, "is_password", "get_is_password"):
                continue
            region = _normalized_rect(_element_bounds(element), client)
            if region is not None and region not in regions:
                regions.append(region)
        return tuple(regions)

    def _optional_win32_int(self, name: str, default: int) -> int:
        member = getattr(self._win32, name, None)
        return int(member() if callable(member) else default)


__all__ = [
    "ContextConfirmationBarrier",
    "FocusContextWatcher",
    "ScreenshotCapturePort",
    "UiaFacade",
    "UiaFocusCache",
    "WindowsWindowContext",
    "capture_target_snapshot",
]
