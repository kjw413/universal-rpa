"""``windows.element_exists`` must mean an element was actually found.

Target resolution is UIA-first with a guarded coordinate fallback, and the
fallback proves nothing about an element: it derives a screen point from the
recorded ratio without looking at what -- if anything -- is there.  A condition
that names an element therefore cannot be satisfied by one.

This matters most where the condition is used as a postcondition, since a
postcondition that cannot fail is a false safety signal rather than a check.
"""

from __future__ import annotations

from typing import Any

import pytest

from universal_rpa.adapters.windows.adapter import WindowsAutomationAdapter
from universal_rpa.adapters.windows.foreground import WindowIdentity
from universal_rpa.adapters.windows.target_resolver import (
    ResolvedCoordinateTarget,
    ResolvedUiaTarget,
)
from universal_rpa.domain.conditions import ConditionSpec
from universal_rpa.domain.errors import ErrorCode, RpaError
from universal_rpa.domain.targets import RuntimeEnvironment, TargetSpec
from universal_rpa.ports.automation import CancellationToken

IDENTITY = WindowIdentity(
    process_id=100,
    process_executable="harness.exe",
    top_level_hwnd=900,
    window_class="HarnessWindow",
)

RUNTIME = RuntimeEnvironment(
    interactive_desktop=True,
    process_id=100,
    process_executable="harness.exe",
    top_level_hwnd=900,
    window_title="Harness",
    window_class="HarnessWindow",
    foreground_hwnd=900,
    dpi_x=96,
    dpi_y=96,
    client_width=720,
    client_height=620,
    monitor_scale=1.0,
)


class _Element:
    def is_visible(self) -> bool:
        return True

    def is_enabled(self) -> bool:
        return True


class _StubResolver:
    """Returns one prepared resolution, or raises one prepared error."""

    def __init__(self, resolution: object | None = None, *, error: RpaError | None = None) -> None:
        self._resolution = resolution
        self._error = error

    def resolve(self, target: object, runtime: object, **kwargs: Any) -> Any:
        del target, runtime, kwargs
        if self._error is not None:
            raise self._error
        return self._resolution


class _StubProbe:
    def require_interactive_desktop(self) -> None:
        return None

    def foreground_hwnd(self) -> int:
        return 900

    def snapshot(self, hwnd: int) -> RuntimeEnvironment:
        del hwnd
        return RUNTIME


def _adapter(resolver: _StubResolver) -> WindowsAutomationAdapter:
    return WindowsAutomationAdapter(
        resolver,  # type: ignore[arg-type]
        None,  # type: ignore[arg-type]
        _StubProbe(),  # type: ignore[arg-type]
    )


def _condition(condition_type: str) -> ConditionSpec:
    return ConditionSpec.model_validate(
        {
            "condition_type": condition_type,
            "target": TargetSpec.model_validate(
                {
                    "adapter_id": "windows",
                    "payload": {
                        "selector": {"automation_id": "field", "control_type": "Edit"},
                        "coordinate_fallback": None,
                    },
                }
            ),
        }
    )


def _coordinate_target() -> ResolvedCoordinateTarget:
    return ResolvedCoordinateTarget(
        window=IDENTITY, client_point=(10, 20), screen_point=(150, 300)
    )


def test_element_exists_is_satisfied_by_a_resolved_element() -> None:
    adapter = _adapter(_StubResolver(ResolvedUiaTarget(IDENTITY, _Element())))

    observation = adapter.evaluate_condition(
        _condition("windows.element_exists"), None, CancellationToken()
    )

    assert observation.satisfied is True


def test_element_exists_is_not_satisfied_by_a_coordinate_fallback() -> None:
    """The fallback found a point, not an element -- claiming the element
    exists would let a postcondition pass over a control that is gone."""

    adapter = _adapter(_StubResolver(_coordinate_target()))

    observation = adapter.evaluate_condition(
        _condition("windows.element_exists"), None, CancellationToken()
    )

    assert observation.satisfied is False


def test_element_exists_is_not_satisfied_when_nothing_resolves() -> None:
    adapter = _adapter(
        _StubResolver(error=RpaError(ErrorCode.TARGET_NOT_FOUND, "UI 요소를 찾을 수 없습니다."))
    )

    observation = adapter.evaluate_condition(
        _condition("windows.element_exists"), None, CancellationToken()
    )

    assert observation.satisfied is False


def test_window_exists_is_still_satisfied_by_a_coordinate_fallback() -> None:
    """A coordinate resolution establishes the window identity it was guarded
    against, so the *window* genuinely does exist -- only the element claim
    was unfounded."""

    adapter = _adapter(_StubResolver(_coordinate_target()))

    observation = adapter.evaluate_condition(
        _condition("windows.window_exists"), None, CancellationToken()
    )

    assert observation.satisfied is True


@pytest.mark.parametrize(
    "condition_type",
    ["windows.element_visible", "windows.element_enabled"],
)
def test_element_state_conditions_already_refuse_a_coordinate_fallback(
    condition_type: str,
) -> None:
    """Pins the behaviour element_exists was missing: no element, no claim."""

    adapter = _adapter(_StubResolver(_coordinate_target()))

    observation = adapter.evaluate_condition(_condition(condition_type), None, CancellationToken())

    assert observation.satisfied is False
