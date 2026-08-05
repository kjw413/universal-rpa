from __future__ import annotations

import pytest

from tests.helpers.validation_fakes import runtime_environment
from universal_rpa.adapters.windows.foreground import ForegroundGuard
from universal_rpa.adapters.windows.target_resolver import (
    ResolvedCoordinateTarget,
    ResolvedUiaTarget,
    WindowsTargetResolver,
)
from universal_rpa.adapters.windows.window_catalog import ClientGeometry
from universal_rpa.domain.errors import ErrorCode, RpaError
from universal_rpa.domain.targets import RuntimeEnvironment, TargetSpec, UiaSelector


class _Probe:
    def __init__(self, runtime: RuntimeEnvironment) -> None:
        self.runtime = runtime

    def snapshot(self, hwnd: int) -> RuntimeEnvironment:
        del hwnd
        return self.runtime


class _Win32:
    def client_geometry(self, hwnd: int) -> ClientGeometry:
        del hwnd
        return ClientGeometry(left=10, top=20, width=1280, height=720)


class _Uia:
    def __init__(self, matches: tuple[object, ...] = ()) -> None:
        self.matches = matches

    def find(self, hwnd: int, selector: object) -> tuple[object, ...]:
        del hwnd, selector
        return self.matches


def _runtime(**updates: object) -> RuntimeEnvironment:
    return runtime_environment().model_copy(update=updates)


def _target() -> TargetSpec:
    return TargetSpec.model_validate(
        {
            "adapter_id": "windows",
            "payload": {
                "selector": {"automation_id": "missing"},
                "coordinate_fallback": {
                    "recorded_process_executable": "fake.exe",
                    "recorded_window_class": "FakeWindow",
                    "point": {"x": 0.5, "y": 0.5},
                    "recorded_dpi_x": 96,
                    "recorded_dpi_y": 96,
                    "recorded_client_width": 1280,
                    "recorded_client_height": 720,
                },
            },
        }
    )


@pytest.mark.parametrize(
    ("updates", "verified", "code"),
    [
        ({"process_executable": "other.exe"}, True, ErrorCode.ENVIRONMENT_MISMATCH),
        ({"window_class": "Other"}, True, ErrorCode.ENVIRONMENT_MISMATCH),
        ({"dpi_x": 120}, True, ErrorCode.ENVIRONMENT_MISMATCH),
        ({"client_width": 1310}, True, ErrorCode.ENVIRONMENT_MISMATCH),
        ({"foreground_hwnd": 999}, True, ErrorCode.FOREGROUND_MISMATCH),
        ({}, False, ErrorCode.ENVIRONMENT_MISMATCH),
    ],
)
def test_each_coordinate_guard_blocks_resolution(
    updates: dict[str, object], verified: bool, code: ErrorCode
) -> None:
    probe = _Probe(_runtime(**updates))
    resolver = WindowsTargetResolver(
        probe,  # type: ignore[arg-type]
        ForegroundGuard(probe),  # type: ignore[arg-type]
        win32=_Win32(),  # type: ignore[arg-type]
        uia=_Uia(),
    )

    with pytest.raises(RpaError) as caught:
        resolver.resolve(_target(), runtime_environment(), has_postcondition_or_assertion=verified)

    assert caught.value.code is code


def test_unique_uia_match_wins_without_using_coordinate_fallback() -> None:
    element = object()
    probe = _Probe(_runtime(client_width=1600, client_height=900))
    resolver = WindowsTargetResolver(
        probe,  # type: ignore[arg-type]
        ForegroundGuard(probe),  # type: ignore[arg-type]
        win32=_Win32(),  # type: ignore[arg-type]
        uia=_Uia((element,)),
    )

    resolved = resolver.resolve(
        _target(), runtime_environment(), has_postcondition_or_assertion=True
    )

    assert isinstance(resolved, ResolvedUiaTarget)
    assert resolved.element is element


def test_matching_coordinate_target_uses_relative_client_arithmetic() -> None:
    probe = _Probe(runtime_environment())
    resolver = WindowsTargetResolver(
        probe,  # type: ignore[arg-type]
        ForegroundGuard(probe),  # type: ignore[arg-type]
        win32=_Win32(),  # type: ignore[arg-type]
        uia=_Uia(),
    )

    resolved = resolver.resolve(
        _target(), runtime_environment(), has_postcondition_or_assertion=True
    )

    assert isinstance(resolved, ResolvedCoordinateTarget)
    assert resolved.client_point == (640, 360)
    assert resolved.screen_point == (650, 380)


# --------------------------------------------------------------------------- #
# The real pywinauto query surface
# --------------------------------------------------------------------------- #


class _FakeElementInfo:
    def __init__(self, automation_id: str | None) -> None:
        self.automation_id = automation_id


class _FakeElement:
    def __init__(self, automation_id: str | None) -> None:
        self.element_info = _FakeElementInfo(automation_id)


class _FakeWindow:
    """Rejects keywords the real ``IUIA.build_condition`` does not accept."""

    SUPPORTED = frozenset({"process", "class_name", "title", "control_type", "content_only"})

    def __init__(self, elements: tuple[_FakeElement, ...]) -> None:
        self.elements = elements
        self.calls: list[dict[str, object]] = []

    def descendants(self, **criteria: object) -> tuple[_FakeElement, ...]:
        unexpected = set(criteria) - self.SUPPORTED
        if unexpected:
            raise TypeError(
                f"IUIA.build_condition() got an unexpected keyword argument "
                f"{sorted(unexpected)[0]!r}"
            )
        self.calls.append(dict(criteria))
        return self.elements


def test_automation_id_is_matched_without_being_passed_to_pywinauto(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AutomationId is not a pywinauto query keyword; passing it raises TypeError."""

    from universal_rpa.adapters.windows import target_resolver as module

    window = _FakeWindow(
        (_FakeElement("clickButton"), _FakeElement("otherButton"), _FakeElement(None))
    )
    monkeypatch.setitem(
        __import__("sys").modules,
        "pywinauto",
        type(
            "_M",
            (),
            {
                "Desktop": lambda backend: type(
                    "_D", (), {"window": staticmethod(lambda handle: window)}
                )()
            },
        ),
    )

    found = tuple(
        module.PywinautoUiaSearch().find(
            42, UiaSelector(automation_id="clickButton", control_type="Button")
        )
    )

    assert window.calls == [{"control_type": "Button"}]
    assert len(found) == 1
    assert found[0].element_info.automation_id == "clickButton"  # type: ignore[attr-defined]


def test_a_selector_without_automation_id_returns_every_match(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from universal_rpa.adapters.windows import target_resolver as module

    window = _FakeWindow((_FakeElement("a"), _FakeElement("b")))
    monkeypatch.setitem(
        __import__("sys").modules,
        "pywinauto",
        type(
            "_M",
            (),
            {
                "Desktop": lambda backend: type(
                    "_D", (), {"window": staticmethod(lambda handle: window)}
                )()
            },
        ),
    )

    found = tuple(module.PywinautoUiaSearch().find(42, UiaSelector(control_type="Button")))

    assert len(found) == 2
