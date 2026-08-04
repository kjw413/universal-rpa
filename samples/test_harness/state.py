"""The harness's observable state: counters and fixed synthetic values only.

The state file is the single channel through which a test observes what the
runner did.  It deliberately holds no captured input, no clipboard body, and no
value the operator typed — only counts and the harness's own fixed strings — so a
state file left behind by a failed run can never leak anything.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, replace
from pathlib import Path

#: Fixed synthetic values the harness writes; never user- or customer-derived.
SYNTHETIC_DATE = "2026-07-27"
SYNTHETIC_KOREAN = "가나다라"
SYNTHETIC_TABLE_HEADERS = ("factory", "period", "quantity")
SYNTHETIC_TABLE_ROWS = (
    ("F-001", "2026-07", "120"),
    ("F-002", "2026-07", "240"),
    ("F-003", "2026-07", "360"),
)


@dataclass(frozen=True, slots=True)
class HarnessState:
    click_count: int = 0
    double_click_count: int = 0
    drag_count: int = 0
    scroll_count: int = 0
    hotkey_count: int = 0
    set_text_count: int = 0
    press_key_count: int = 0
    modal_open_count: int = 0
    modal_close_count: int = 0
    copy_table_count: int = 0
    delayed_control_visible: bool = False
    normal_text: str = ""
    date_text: str = ""
    korean_text: str = ""
    #: Always empty. The password field's content is never recorded anywhere.
    password_present: bool = False

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, indent=2, sort_keys=True)

    @classmethod
    def from_json(cls, payload: str) -> HarnessState:
        raw = json.loads(payload)
        if not isinstance(raw, dict):
            raise ValueError("harness state must be a JSON object")
        allowed = {field for field in cls.__dataclass_fields__}
        unexpected = set(raw) - allowed
        if unexpected:
            raise ValueError(f"unexpected harness state fields: {sorted(unexpected)}")
        return cls(**raw)

    def bumped(self, field: str) -> HarnessState:
        current = getattr(self, field)
        if not isinstance(current, int) or isinstance(current, bool):
            raise ValueError(f"{field} is not a counter")
        return replace(self, **{field: current + 1})


class HarnessStateFile:
    """Atomically publishes state so a reader never observes a partial write."""

    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._state = HarnessState()
        self.publish(self._state)

    @property
    def path(self) -> Path:
        return self._path

    @property
    def state(self) -> HarnessState:
        return self._state

    def publish(self, state: HarnessState) -> None:
        self._state = state
        temporary = self._path.with_suffix(self._path.suffix + ".tmp")
        with temporary.open("w", encoding="utf-8", newline="\n") as stream:
            stream.write(state.to_json())
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, self._path)

    def update(self, **changes: object) -> HarnessState:
        self.publish(replace(self._state, **changes))  # type: ignore[arg-type]
        return self._state

    def bump(self, field: str) -> HarnessState:
        self.publish(self._state.bumped(field))
        return self._state

    @classmethod
    def read(cls, path: Path) -> HarnessState:
        return HarnessState.from_json(Path(path).read_text(encoding="utf-8"))


def synthetic_table_text() -> str:
    """The exact TSV block the clipboard-table button publishes."""

    lines = ["\t".join(SYNTHETIC_TABLE_HEADERS)]
    lines.extend("\t".join(row) for row in SYNTHETIC_TABLE_ROWS)
    return "\r\n".join(lines)


__all__ = [
    "SYNTHETIC_DATE",
    "SYNTHETIC_KOREAN",
    "SYNTHETIC_TABLE_HEADERS",
    "SYNTHETIC_TABLE_ROWS",
    "HarnessState",
    "HarnessStateFile",
    "synthetic_table_text",
]
