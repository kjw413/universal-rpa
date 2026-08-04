"""Offscreen coverage of the harness itself.

The interactive suite can only run on an unlocked desktop, so the harness would
otherwise be the one untested piece of the end-to-end story.  These tests are
deliberately *not* marked ``windows_e2e``: they build the real window offscreen
and drive it through Qt, which verifies the control identities, the deterministic
effects, and — most importantly — that the state file can never carry a password.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QLineEdit, QPushButton
from pytestqt.qtbot import QtBot

from samples.test_harness.app import HarnessConfig, create_harness_window, parse_config
from samples.test_harness.main_window import (
    CLICK_BUTTON_ID,
    COPY_TABLE_BUTTON_ID,
    DATE_TEXT_ID,
    DELAYED_CONTROL_ID,
    DOUBLE_CLICK_BUTTON_ID,
    DRAG_SURFACE_ID,
    DUPLICATE_BUTTON_ID,
    HOTKEY_INDICATOR_ID,
    KOREAN_TEXT_ID,
    NORMAL_TEXT_ID,
    OPEN_MODAL_BUTTON_ID,
    PASSWORD_TEXT_ID,
    SCROLL_SURFACE_ID,
    HarnessWindow,
)
from samples.test_harness.state import (
    SYNTHETIC_KOREAN,
    SYNTHETIC_TABLE_HEADERS,
    HarnessState,
    HarnessStateFile,
    synthetic_table_text,
)

REQUIRED_AUTOMATION_IDS = (
    NORMAL_TEXT_ID,
    DATE_TEXT_ID,
    KOREAN_TEXT_ID,
    PASSWORD_TEXT_ID,
    CLICK_BUTTON_ID,
    DOUBLE_CLICK_BUTTON_ID,
    DRAG_SURFACE_ID,
    SCROLL_SURFACE_ID,
    HOTKEY_INDICATOR_ID,
    DELAYED_CONTROL_ID,
    OPEN_MODAL_BUTTON_ID,
    COPY_TABLE_BUTTON_ID,
)


def _config(tmp_path: Path, **overrides: object) -> HarnessConfig:
    base: dict[str, object] = {
        "state_file": tmp_path / "state.json",
        "ready_file": tmp_path / "ready.json",
        "delayed_control_ms": 10,
    }
    base.update(overrides)
    return HarnessConfig(**base)  # type: ignore[arg-type]


@pytest.fixture
def window(qtbot: QtBot, tmp_path: Path) -> HarnessWindow:
    harness = create_harness_window(_config(tmp_path))
    qtbot.addWidget(harness)
    harness.show()
    return harness


def test_every_required_control_has_a_stable_automation_identity(
    window: HarnessWindow,
) -> None:
    for automation_id in REQUIRED_AUTOMATION_IDS:
        control = window.findChild(object, automation_id)
        assert control is not None, f"{automation_id} is missing"
        assert control.accessibleName()  # type: ignore[attr-defined]


def test_duplicate_selector_publishes_exactly_two_identical_controls(
    qtbot: QtBot, tmp_path: Path
) -> None:
    harness = create_harness_window(_config(tmp_path, duplicate_selector=True))
    qtbot.addWidget(harness)

    duplicates = harness.findChildren(QPushButton, DUPLICATE_BUTTON_ID)

    assert len(duplicates) == 2
    assert {button.accessibleName() for button in duplicates} == {"중복 대상"}


def test_default_configuration_publishes_no_duplicate_control(window: HarnessWindow) -> None:
    assert window.findChildren(QPushButton, DUPLICATE_BUTTON_ID) == []


def test_click_and_double_click_are_counted_separately(qtbot: QtBot, window: HarnessWindow) -> None:
    qtbot.mouseClick(window.click_button, Qt.MouseButton.LeftButton)
    qtbot.mouseDClick(window.double_click_button, Qt.MouseButton.LeftButton)

    state = HarnessStateFile.read(window.state_file.path)
    assert state.click_count == 1
    assert state.double_click_count == 1


def test_drag_requires_press_move_and_release(qtbot: QtBot, window: HarnessWindow) -> None:
    surface = window.drag_surface
    qtbot.mousePress(surface, Qt.MouseButton.LeftButton, pos=QPoint(10, 10))
    qtbot.mouseMove(surface, QPoint(120, 40))
    qtbot.mouseRelease(surface, Qt.MouseButton.LeftButton, pos=QPoint(120, 40))

    assert HarnessStateFile.read(window.state_file.path).drag_count == 1


def test_a_press_without_movement_is_not_a_drag(qtbot: QtBot, window: HarnessWindow) -> None:
    surface = window.drag_surface
    qtbot.mousePress(surface, Qt.MouseButton.LeftButton, pos=QPoint(10, 10))
    qtbot.mouseRelease(surface, Qt.MouseButton.LeftButton, pos=QPoint(10, 10))

    assert HarnessStateFile.read(window.state_file.path).drag_count == 0


def test_wheel_notches_are_counted(window: HarnessWindow) -> None:
    event = QWheelEvent(
        QPoint(10, 10),
        window.scroll_surface.mapToGlobal(QPoint(10, 10)),
        QPoint(0, 0),
        QPoint(0, -120),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.NoScrollPhase,
        False,
    )
    window.scroll_surface.wheelEvent(event)

    assert HarnessStateFile.read(window.state_file.path).scroll_count == 1


def test_the_delayed_control_appears_only_after_its_configured_delay(
    qtbot: QtBot, window: HarnessWindow
) -> None:
    assert not window.delayed_control.isVisible()

    window.start()
    qtbot.waitUntil(lambda: window.delayed_control.isVisible(), timeout=2_000)

    assert HarnessStateFile.read(window.state_file.path).delayed_control_visible is True


def test_intentional_timeout_never_reveals_the_delayed_control(
    qtbot: QtBot, tmp_path: Path
) -> None:
    harness = create_harness_window(_config(tmp_path, intentional_timeout=True))
    qtbot.addWidget(harness)
    harness.show()

    harness.start()
    qtbot.wait(200)

    assert not harness.delayed_control.isVisible()
    assert HarnessStateFile.read(harness.state_file.path).delayed_control_visible is False


def test_korean_text_is_preloaded_with_the_fixed_synthetic_value(
    window: HarnessWindow,
) -> None:
    assert window.korean_text.text() == SYNTHETIC_KOREAN
    assert HarnessStateFile.read(window.state_file.path).korean_text == SYNTHETIC_KOREAN


def test_the_state_file_records_presence_but_never_the_password(
    window: HarnessWindow,
) -> None:
    window.password_text.setText("actual-password")

    raw = window.state_file.path.read_text(encoding="utf-8")
    state = HarnessState.from_json(raw)
    assert state.password_present is True
    assert "actual-password" not in raw


def test_copying_the_table_publishes_the_fixed_synthetic_block(
    qtbot: QtBot, window: HarnessWindow
) -> None:
    qtbot.mouseClick(window.copy_table_button, Qt.MouseButton.LeftButton)

    text = synthetic_table_text()
    assert text.splitlines()[0].split("\t") == list(SYNTHETIC_TABLE_HEADERS)
    assert len(text.splitlines()) == 4
    assert HarnessStateFile.read(window.state_file.path).copy_table_count == 1


def test_ctrl_a_selects_all_in_the_focused_field_and_is_counted(
    qtbot: QtBot, window: HarnessWindow
) -> None:
    window.date_text.setText("2026-01-01")
    window.date_text.setFocus()
    qtbot.keyClick(window, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)

    assert HarnessStateFile.read(window.state_file.path).hotkey_count == 1
    assert "1회" in window.hotkey_indicator.text()


def test_return_in_a_text_field_is_counted_as_a_key_press(
    qtbot: QtBot, window: HarnessWindow
) -> None:
    window.date_text.setFocus()
    qtbot.keyClick(window.date_text, Qt.Key.Key_Return)

    assert HarnessStateFile.read(window.state_file.path).press_key_count == 1


def test_opening_the_modal_is_counted_and_it_is_owned_by_the_window(
    qtbot: QtBot, window: HarnessWindow
) -> None:
    qtbot.mouseClick(window.open_modal_button, Qt.MouseButton.LeftButton)

    modal = window.findChild(object, "ownedModal")
    assert modal is not None
    assert modal.parent() is window  # type: ignore[attr-defined]
    assert modal.isModal()  # type: ignore[attr-defined]
    assert HarnessStateFile.read(window.state_file.path).modal_open_count == 1


def test_the_state_file_is_written_atomically_from_the_first_moment(
    tmp_path: Path,
) -> None:
    state_file = HarnessStateFile(tmp_path / "state.json")

    assert state_file.path.is_file()
    assert HarnessStateFile.read(state_file.path) == HarnessState()
    assert not list(tmp_path.glob("*.tmp"))


def test_state_rejects_an_unexpected_field() -> None:
    with pytest.raises(ValueError, match="unexpected harness state fields"):
        HarnessState.from_json('{"password": "leaked"}')


def test_command_line_maps_onto_the_frozen_config(tmp_path: Path) -> None:
    config = parse_config(
        [
            "--state-file",
            str(tmp_path / "state.json"),
            "--ready-file",
            str(tmp_path / "ready.json"),
            "--delayed-control-ms",
            "1200",
            "--duplicate-selector",
            "--intentional-timeout",
            "--lock-output",
            "--lock-output-path",
            str(tmp_path / "locked.csv"),
        ]
    )

    assert config.delayed_control_ms == 1200
    assert config.duplicate_selector is True
    assert config.intentional_timeout is True
    assert config.lock_output is True
    assert config.lock_output_path == tmp_path / "locked.csv"


def test_password_field_never_echoes_its_content(window: HarnessWindow) -> None:
    assert window.password_text.echoMode() == QLineEdit.EchoMode.Password
