from __future__ import annotations

from types import SimpleNamespace

from universal_rpa.adapters.windows.foreground import WindowIdentity
from universal_rpa.adapters.windows.target_resolver import ResolvedUiaTarget
from universal_rpa.adapters.windows.text_input import TextInputStrategy


class _Element:
    def __init__(self) -> None:
        self.iface_value = SimpleNamespace(CurrentValue="")

    def set_edit_text(self, text: str) -> None:
        del text
        raise RuntimeError


class _Driver:
    def __init__(self, element: _Element) -> None:
        self.element = element
        self.calls: list[str] = []

    def verify_target(self, target: object) -> None:
        del target
        self.calls.append("guard")

    def paste_text(self, target: object, text: str) -> None:
        del target
        self.calls.append("paste")
        self.element.iface_value.CurrentValue = text

    def direct_text(self, target: object, text: str) -> None:
        del target, text
        self.calls.append("keys")


def test_text_strategy_guards_each_attempt_and_stops_after_verified_paste() -> None:
    element = _Element()
    driver = _Driver(element)
    target = ResolvedUiaTarget(WindowIdentity(1, "fake.exe", 2, "FakeWindow"), element)

    result = TextInputStrategy(driver).set_text(  # type: ignore[arg-type]
        target, "생산실적", verify=True
    )

    assert result.strategy == "paste"
    assert result.verified is True
    assert driver.calls == ["guard", "guard", "guard", "paste"]
