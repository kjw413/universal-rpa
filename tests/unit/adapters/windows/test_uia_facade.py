from __future__ import annotations

import time
from collections.abc import Iterable
from dataclasses import dataclass

from universal_rpa.adapters.windows.uia_facade import PywinautoUiaFacade


@dataclass
class _ComPasswordStub:
    is_password: bool

    @property
    def CurrentIsPassword(self) -> bool:
        return self.is_password


@dataclass
class _RawElement:
    runtime_id: tuple[int, ...] | None = None
    automation_id: str | None = None
    control_type: str | None = "Edit"
    name: str | None = None
    class_name: str | None = None
    element: object | None = None
    rectangle: object | None = None


class _FakeSource:
    """Stands in for real pywinauto/COM tree access in unit tests."""

    def __init__(
        self,
        *,
        focused: object | None = None,
        point_chain: tuple[object, ...] = (),
        password_root: object | None = None,
        password_descendants: tuple[object, ...] = (),
        focused_delay_seconds: float = 0.0,
        raise_on_focused: bool = False,
        raise_on_point: bool = False,
        raise_on_root: bool = False,
    ) -> None:
        self._focused = focused
        self._point_chain = point_chain
        self._password_root = password_root
        self._password_descendants = password_descendants
        self._focused_delay = focused_delay_seconds
        self._raise_on_focused = raise_on_focused
        self._raise_on_point = raise_on_point
        self._raise_on_root = raise_on_root

    def focused_element(self) -> object | None:
        if self._focused_delay:
            time.sleep(self._focused_delay)
        if self._raise_on_focused:
            raise RuntimeError("uia focus lookup failed")
        return self._focused

    def element_from_point(self, screen_x: int, screen_y: int) -> object | None:
        del screen_x, screen_y
        if self._raise_on_point:
            raise RuntimeError("uia point lookup failed")
        return self._point_chain[0] if self._point_chain else None

    def parent(self, element: object) -> object | None:
        try:
            index = self._point_chain.index(element)
        except ValueError:
            return None
        following = index + 1
        return self._point_chain[following] if following < len(self._point_chain) else None

    def root_from_hwnd(self, top_level_hwnd: int) -> object | None:
        del top_level_hwnd
        if self._raise_on_root:
            raise RuntimeError("uia root lookup failed")
        return self._password_root

    def descendants(self, root: object) -> Iterable[object]:
        del root
        return self._password_descendants


def _facade(source: _FakeSource, **kwargs: object) -> PywinautoUiaFacade:
    return PywinautoUiaFacade(source=source, **kwargs)  # type: ignore[arg-type]


def test_a_matching_runtime_id_resolves_to_the_focused_element() -> None:
    element = _RawElement(runtime_id=(7, 2, 900), automation_id="field1")
    facade = _facade(_FakeSource(focused=element))
    try:
        resolved = facade.element_from_runtime_id((7, 2, 900))
        assert resolved is not None
        assert resolved.automation_id == "field1"  # type: ignore[attr-defined]
    finally:
        facade.close()


def test_a_reused_runtime_id_resolves_to_nothing() -> None:
    """The focused element's id no longer matches what the caller cached --
    focus moved on, so the cached id must not resolve to today's element."""
    element = _RawElement(runtime_id=(7, 2, 900))
    facade = _facade(_FakeSource(focused=element))
    try:
        assert facade.element_from_runtime_id((1, 1, 1)) is None
    finally:
        facade.close()


def test_a_uia_error_yields_no_element_rather_than_raising() -> None:
    facade = _facade(_FakeSource(raise_on_focused=True))
    try:
        assert facade.element_from_runtime_id((1, 2, 3)) is None
    finally:
        facade.close()


def test_password_elements_are_reported_for_masking() -> None:
    root = _RawElement(automation_id="root")
    secret_field = _RawElement(automation_id="secret", element=_ComPasswordStub(True))
    facade = _facade(_FakeSource(password_root=root, password_descendants=(secret_field,)))
    try:
        elements = tuple(facade.password_elements(4242))
        assert len(elements) == 1
        assert elements[0].is_password is True  # type: ignore[attr-defined]
    finally:
        facade.close()


def test_password_elements_yields_nothing_rather_than_raising() -> None:
    facade = _facade(_FakeSource(raise_on_root=True))
    try:
        assert tuple(facade.password_elements(99)) == ()
    finally:
        facade.close()


def test_elements_from_point_returns_the_hit_chain_innermost_first() -> None:
    leaf = _RawElement(automation_id="leaf")
    container = _RawElement(automation_id="container")
    facade = _facade(_FakeSource(point_chain=(leaf, container)))
    try:
        views = tuple(facade.elements_from_point(500, 400))
        assert [view.automation_id for view in views] == ["leaf", "container"]  # type: ignore[attr-defined]
    finally:
        facade.close()


def test_elements_from_point_yields_nothing_rather_than_raising() -> None:
    facade = _facade(_FakeSource(raise_on_point=True))
    try:
        assert tuple(facade.elements_from_point(1, 1)) == ()
    finally:
        facade.close()


def test_resolution_gives_up_within_its_budget() -> None:
    element = _RawElement(runtime_id=(1, 1, 1))
    facade = _facade(
        _FakeSource(focused=element, focused_delay_seconds=0.3),
        resolution_budget_seconds=0.05,
    )
    try:
        started = time.monotonic()
        resolved = facade.element_from_runtime_id((1, 1, 1))
        elapsed = time.monotonic() - started

        assert resolved is None
        assert elapsed < 0.25
    finally:
        facade.close()
