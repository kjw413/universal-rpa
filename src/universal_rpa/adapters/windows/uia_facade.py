"""Real UIA-backed implementation of the ``UiaFacade`` protocol.

All three lookups are read-only tree/point queries against UI Automation and
must never raise into the caller and never block it past a resolution
budget -- a slow or hung UIA call must not stall the recorder, which is why
every call runs on a single dedicated worker thread (COM is initialized
there exactly once) and is bounded by ``resolution_budget_seconds``. A call
that exceeds its budget is abandoned and reported as "nothing found" rather
than raising or blocking the caller.

The raw pywinauto/COM mechanics are isolated behind ``UiaTreeSource`` so this
module can be unit tested without a live window or a real UIA tree.
"""

from __future__ import annotations

from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Protocol, TypeVar, cast

from .uia_elements import UiaElementView

_DEFAULT_BUDGET_SECONDS = 0.25
_DEFAULT_MAX_ANCESTOR_DEPTH = 12

T = TypeVar("T")


class UiaTreeSource(Protocol):
    def focused_element(self) -> object | None: ...

    def element_from_point(self, screen_x: int, screen_y: int) -> object | None: ...

    def parent(self, element: object) -> object | None: ...

    def root_from_hwnd(self, top_level_hwnd: int) -> object | None: ...

    def descendants(self, root: object) -> Iterable[object]: ...


def _initialize_worker_com() -> None:
    import comtypes  # type: ignore[import-untyped]

    comtypes.CoInitialize()


class PywinautoUiaTreeSource:
    """Thin pass-through to pywinauto's UIA client, kept off the caller's thread."""

    def focused_element(self) -> object | None:
        from pywinauto.uia_defines import IUIA  # type: ignore[import-untyped]
        from pywinauto.uia_element_info import UIAElementInfo  # type: ignore[import-untyped]

        raw = IUIA().iuia.GetFocusedElement()
        return None if raw is None else cast(object, UIAElementInfo(raw))

    def element_from_point(self, screen_x: int, screen_y: int) -> object | None:
        from pywinauto.uia_element_info import UIAElementInfo

        return cast(object, UIAElementInfo.from_point(screen_x, screen_y))

    def parent(self, element: object) -> object | None:
        value = cast(Any, element).parent
        return None if value is None else cast(object, value)

    def root_from_hwnd(self, top_level_hwnd: int) -> object | None:
        from pywinauto.uia_element_info import UIAElementInfo

        return cast(object, UIAElementInfo(top_level_hwnd))

    def descendants(self, root: object) -> Iterable[object]:
        return cast(Iterable[object], cast(Any, root).iter_descendants())


class PywinautoUiaFacade:
    def __init__(
        self,
        *,
        source: UiaTreeSource | None = None,
        resolution_budget_seconds: float = _DEFAULT_BUDGET_SECONDS,
        max_ancestor_depth: int = _DEFAULT_MAX_ANCESTOR_DEPTH,
    ) -> None:
        self._source = source or PywinautoUiaTreeSource()
        self._budget = resolution_budget_seconds
        self._max_ancestor_depth = max_ancestor_depth
        self._executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="universal-rpa-uia-facade",
            initializer=_initialize_worker_com,
        )

    def close(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)

    def element_from_runtime_id(self, runtime_id: tuple[int, ...]) -> object | None:
        return cast(object, self._call(self._resolve_by_runtime_id, runtime_id))

    def elements_from_point(self, screen_x: int, screen_y: int) -> tuple[object, ...]:
        return self._call(self._resolve_from_point, screen_x, screen_y) or ()

    def password_elements(self, top_level_hwnd: int) -> tuple[object, ...]:
        return self._call(self._resolve_password_elements, top_level_hwnd) or ()

    def focused_runtime_id(self) -> tuple[int, ...] | None:
        """The runtime id of whatever is currently focused, for the focus
        poller to publish -- separate from ``element_from_runtime_id``, which
        resolves a previously-published id back to an element."""
        return cast("tuple[int, ...] | None", self._call(self._resolve_focused_runtime_id))

    def _resolve_focused_runtime_id(self) -> tuple[int, ...] | None:
        try:
            raw = self._source.focused_element()
        except Exception:
            return None
        if raw is None:
            return None
        return UiaElementView(raw).runtime_id

    def _call(self, fn: Any, *args: object) -> Any:
        try:
            future = self._executor.submit(fn, *args)
        except RuntimeError:
            return None
        try:
            return future.result(timeout=self._budget)
        except Exception:
            return None

    def _resolve_by_runtime_id(self, runtime_id: tuple[int, ...]) -> object | None:
        try:
            raw = self._source.focused_element()
        except Exception:
            return None
        if raw is None:
            return None
        view = UiaElementView(raw)
        if view.runtime_id != tuple(runtime_id):
            return None
        return view

    def _resolve_from_point(self, screen_x: int, screen_y: int) -> tuple[object, ...]:
        try:
            current = self._source.element_from_point(screen_x, screen_y)
        except Exception:
            return ()
        chain: list[object] = []
        depth = 0
        while current is not None and depth < self._max_ancestor_depth:
            chain.append(UiaElementView(current))
            try:
                current = self._source.parent(current)
            except Exception:
                break
            depth += 1
        return tuple(chain)

    def _resolve_password_elements(self, top_level_hwnd: int) -> tuple[object, ...]:
        try:
            root = self._source.root_from_hwnd(top_level_hwnd)
        except Exception:
            return ()
        if root is None:
            return ()
        elements: list[object] = []
        try:
            for raw in self._source.descendants(root):
                elements.append(UiaElementView(raw))
        except Exception:
            pass
        return tuple(elements)


__all__ = ["PywinautoUiaFacade", "PywinautoUiaTreeSource", "UiaTreeSource"]
