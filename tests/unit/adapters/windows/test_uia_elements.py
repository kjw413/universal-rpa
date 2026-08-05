from __future__ import annotations

from dataclasses import dataclass, field

from universal_rpa.adapters.windows.uia_elements import UiaElementView


@dataclass
class _FakeRect:
    left: int
    top: int
    right: int
    bottom: int


class _FakeValuePattern:
    def __init__(self, value: object) -> None:
        self._value = value

    def QueryInterface(self, iface: object) -> _FakeValuePattern:
        del iface
        return self

    @property
    def CurrentValue(self) -> object:
        return self._value


class _FakeComElement:
    def __init__(
        self,
        *,
        is_password: int = 0,
        value: object = None,
        raise_is_password: bool = False,
        raise_pattern: bool = False,
    ) -> None:
        self._is_password = is_password
        self._value = value
        self._raise_is_password = raise_is_password
        self._raise_pattern = raise_pattern

    @property
    def CurrentIsPassword(self) -> int:
        if self._raise_is_password:
            raise RuntimeError("uia unavailable")
        return self._is_password

    def GetCurrentPattern(self, pattern_id: int) -> _FakeValuePattern:
        del pattern_id
        if self._raise_pattern or self._value is None:
            raise ValueError("no value pattern")
        return _FakeValuePattern(self._value)


@dataclass
class _FakeElementInfo:
    automation_id: str | None = "field1"
    control_type: str | None = "Edit"
    name: str | None = "First name"
    class_name: str | None = "TextBox"
    runtime_id: object = field(default_factory=lambda: [42, 3, 9001])
    rectangle: object | None = field(default_factory=lambda: _FakeRect(10, 20, 210, 60))
    element: object | None = None


def test_translates_selector_fields_directly() -> None:
    view = UiaElementView(_FakeElementInfo())

    assert view.automation_id == "field1"
    assert view.control_type == "Edit"
    assert view.name == "First name"
    assert view.class_name == "TextBox"


def test_missing_selector_fields_fall_back_to_safe_defaults() -> None:
    view = UiaElementView(
        _FakeElementInfo(automation_id=None, control_type=None, name=None, class_name=None)
    )

    assert view.automation_id is None
    assert view.control_type == "Unknown"
    assert view.name is None
    assert view.class_name is None


def test_runtime_id_sequence_becomes_a_tuple_of_ints() -> None:
    view = UiaElementView(_FakeElementInfo(runtime_id=[42, 3, 9001]))

    assert view.runtime_id == (42, 3, 9001)


def test_runtime_id_zero_sentinel_from_a_com_error_becomes_none() -> None:
    # pywinauto's UIAElementInfo.runtime_id returns the int 0 (not None) when
    # the underlying COM call fails.
    view = UiaElementView(_FakeElementInfo(runtime_id=0))

    assert view.runtime_id is None


def test_is_password_reads_the_com_current_is_password_property() -> None:
    view = UiaElementView(_FakeElementInfo(element=_FakeComElement(is_password=1)))

    assert view.is_password is True


def test_is_password_false_when_com_reports_not_a_password_field() -> None:
    view = UiaElementView(_FakeElementInfo(element=_FakeComElement(is_password=0)))

    assert view.is_password is False


def test_is_password_defaults_to_true_when_element_is_missing() -> None:
    view = UiaElementView(_FakeElementInfo(element=None))

    assert view.is_password is True


def test_is_password_defaults_to_true_when_the_com_read_raises() -> None:
    view = UiaElementView(_FakeElementInfo(element=_FakeComElement(raise_is_password=True)))

    assert view.is_password is True


def test_get_value_reads_the_value_pattern_current_value() -> None:
    view = UiaElementView(_FakeElementInfo(element=_FakeComElement(value="hello")))

    assert view.get_value() == "hello"


def test_get_value_is_none_when_there_is_no_value_pattern() -> None:
    view = UiaElementView(_FakeElementInfo(element=_FakeComElement(value=None)))

    assert view.get_value() is None


def test_get_value_is_none_rather_than_raising_when_the_pattern_lookup_fails() -> None:
    view = UiaElementView(
        _FakeElementInfo(element=_FakeComElement(value="hello", raise_pattern=True))
    )

    assert view.get_value() is None


def test_get_value_is_none_when_there_is_no_element_at_all() -> None:
    view = UiaElementView(_FakeElementInfo(element=None))

    assert view.get_value() is None


def test_bounding_rectangle_proxies_the_pywinauto_rectangle() -> None:
    rect = _FakeRect(10, 20, 210, 60)
    view = UiaElementView(_FakeElementInfo(rectangle=rect))

    assert view.bounding_rectangle is rect


def test_bounding_rectangle_is_none_when_absent() -> None:
    view = UiaElementView(_FakeElementInfo(rectangle=None))

    assert view.bounding_rectangle is None


def test_view_satisfies_capture_target_snapshot() -> None:
    """End-to-end proof: the real capture_target_snapshot can read this view."""
    from universal_rpa.adapters.windows.context import capture_target_snapshot
    from universal_rpa.adapters.windows.window_catalog import ClientGeometry

    view = UiaElementView(
        _FakeElementInfo(
            element=_FakeComElement(is_password=0, value="Alice"),
        )
    )

    snapshot = capture_target_snapshot(view, client=ClientGeometry(0, 0, 1200, 800))

    assert snapshot.focused_runtime_id == (42, 3, 9001)
    assert snapshot.is_password is False
    assert snapshot.editable is True
    assert snapshot.observed_value == "Alice"
    assert snapshot.bounds is not None
    assert snapshot.selector_candidates[0].automation_id == "field1"


def test_view_satisfies_capture_target_snapshot_for_a_password_field() -> None:
    from universal_rpa.adapters.windows.context import capture_target_snapshot
    from universal_rpa.adapters.windows.window_catalog import ClientGeometry

    view = UiaElementView(
        _FakeElementInfo(
            control_type="Edit",
            element=_FakeComElement(is_password=1, value="hunter2"),
        )
    )

    snapshot = capture_target_snapshot(view, client=ClientGeometry(0, 0, 1200, 800))

    assert snapshot.is_password is True
    assert snapshot.observed_value is None
