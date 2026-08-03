from __future__ import annotations

from collections.abc import Mapping

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QLabel,
    QSpinBox,
    QWidget,
)

from universal_rpa.domain.action_parameters import validate_builtin_action_parameters
from universal_rpa.domain.types import FrozenJsonObject, JsonValue

_KEYS = (
    *(chr(code) for code in range(ord("a"), ord("z") + 1)),
    *(str(number) for number in range(10)),
    "enter",
    "tab",
    "esc",
    "space",
    "backspace",
    "delete",
    "insert",
    "home",
    "end",
    "page_up",
    "page_down",
    "left",
    "right",
    "up",
    "down",
    *(f"f{number}" for number in range(1, 25)),
)


class ActionParameterEditor(QWidget):
    parameters_changed = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._action_type = "windows.activate_window"
        self.button_combo = QComboBox()
        self.button_combo.addItems(("left", "right", "middle"))
        self.end_x = QDoubleSpinBox()
        self.end_y = QDoubleSpinBox()
        for double_control in (self.end_x, self.end_y):
            double_control.setRange(0.0, 1.0)
            double_control.setSingleStep(0.05)
            double_control.setDecimals(3)
        self.horizontal_delta = QSpinBox()
        self.vertical_delta = QSpinBox()
        for spin_control in (self.horizontal_delta, self.vertical_delta):
            spin_control.setRange(-120_000, 120_000)
            spin_control.setSingleStep(120)
        self.key_combo = QComboBox()
        self.key_combo.addItems(_KEYS)
        self.ctrl = QCheckBox("Ctrl")
        self.alt = QCheckBox("Alt")
        self.shift = QCheckBox("Shift")
        self.win = QCheckBox("Win")
        self.error_label = QLabel()
        self.error_label.setWordWrap(True)

        form = QFormLayout(self)
        form.addRow("마우스 버튼", self.button_combo)
        form.addRow("끝 X (0~1)", self.end_x)
        form.addRow("끝 Y (0~1)", self.end_y)
        form.addRow("가로 스크롤", self.horizontal_delta)
        form.addRow("세로 스크롤", self.vertical_delta)
        form.addRow("기본 키", self.key_combo)
        form.addRow("조합 키", self.ctrl)
        form.addRow("", self.alt)
        form.addRow("", self.shift)
        form.addRow("", self.win)
        form.addRow("", self.error_label)

        self.button_combo.currentIndexChanged.connect(self._changed)
        self.end_x.valueChanged.connect(self._changed)
        self.end_y.valueChanged.connect(self._changed)
        self.horizontal_delta.valueChanged.connect(self._changed)
        self.vertical_delta.valueChanged.connect(self._changed)
        self.key_combo.currentIndexChanged.connect(self._changed)
        for checkbox in (self.ctrl, self.alt, self.shift, self.win):
            checkbox.toggled.connect(self._changed)
        self._update_visibility()

    @property
    def error_text(self) -> str:
        return self.error_label.text()

    def set_action(self, action_type: str, parameters: FrozenJsonObject) -> None:
        self._action_type = action_type
        self.set_draft(dict(parameters))
        self._update_visibility()

    def set_draft(self, draft: Mapping[str, JsonValue | object]) -> None:
        for widget in (
            self.button_combo,
            self.end_x,
            self.end_y,
            self.horizontal_delta,
            self.vertical_delta,
            self.key_combo,
            self.ctrl,
            self.alt,
            self.shift,
            self.win,
        ):
            widget.blockSignals(True)
        button = draft.get("button", "left")
        self.button_combo.setCurrentText(str(button))
        end = draft.get("end_point")
        if isinstance(end, Mapping):
            self.end_x.setValue(float(end.get("x", 0.0)))
            self.end_y.setValue(float(end.get("y", 0.0)))
        horizontal = draft.get("horizontal_delta", 0)
        vertical = draft.get("vertical_delta", 0)
        self.horizontal_delta.setValue(horizontal if isinstance(horizontal, int) else 0)
        self.vertical_delta.setValue(vertical if isinstance(vertical, int) else 0)
        self.key_combo.setCurrentText(str(draft.get("key", "enter")))
        modifiers = draft.get("modifiers", ())
        selected = set(modifiers) if isinstance(modifiers, (tuple, list, set, frozenset)) else set()
        for name, checkbox in (
            ("ctrl", self.ctrl),
            ("alt", self.alt),
            ("shift", self.shift),
            ("win", self.win),
        ):
            checkbox.setChecked(name in selected)
        for widget in (
            self.button_combo,
            self.end_x,
            self.end_y,
            self.horizontal_delta,
            self.vertical_delta,
            self.key_combo,
            self.ctrl,
            self.alt,
            self.shift,
            self.win,
        ):
            widget.blockSignals(False)
        self._changed()

    def pending_parameters(self) -> FrozenJsonObject | None:
        draft: dict[str, JsonValue]
        if self._action_type in {"windows.click", "windows.double_click"}:
            draft = {"button": self.button_combo.currentText()}
        elif self._action_type == "windows.drag":
            draft = {
                "button": self.button_combo.currentText(),
                "end_point": {"x": self.end_x.value(), "y": self.end_y.value()},
            }
        elif self._action_type == "windows.scroll":
            draft = {
                "horizontal_delta": self.horizontal_delta.value(),
                "vertical_delta": self.vertical_delta.value(),
            }
        elif self._action_type == "windows.press_key":
            draft = {"key": self.key_combo.currentText()}
        elif self._action_type == "windows.hotkey":
            modifiers = tuple(
                name
                for name, checkbox in (
                    ("ctrl", self.ctrl),
                    ("alt", self.alt),
                    ("shift", self.shift),
                    ("win", self.win),
                )
                if checkbox.isChecked()
            )
            draft = {"key": self.key_combo.currentText(), "modifiers": list(modifiers)}
        else:
            draft = {}
        try:
            return validate_builtin_action_parameters(self._action_type, draft)
        except (TypeError, ValueError):
            return None

    def _changed(self) -> None:
        parameters = self.pending_parameters()
        self.error_label.setText("" if parameters is not None else "입력값을 확인하세요.")
        self.parameters_changed.emit(parameters)

    def _update_visibility(self) -> None:
        mouse = self._action_type in {"windows.click", "windows.double_click", "windows.drag"}
        drag = self._action_type == "windows.drag"
        scroll = self._action_type == "windows.scroll"
        key = self._action_type in {"windows.press_key", "windows.hotkey"}
        hotkey = self._action_type == "windows.hotkey"
        self.button_combo.setVisible(mouse)
        self.end_x.setVisible(drag)
        self.end_y.setVisible(drag)
        self.horizontal_delta.setVisible(scroll)
        self.vertical_delta.setVisible(scroll)
        self.key_combo.setVisible(key)
        for checkbox in (self.ctrl, self.alt, self.shift, self.win):
            checkbox.setVisible(hotkey)


__all__ = ["ActionParameterEditor"]
