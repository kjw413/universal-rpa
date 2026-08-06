from __future__ import annotations

from universal_rpa.domain.targets import TargetSpec
from universal_rpa.ports.automation import AdapterDescriptor
from universal_rpa.ui.wait_assertion_editor import WaitAssertionEditor


def descriptor() -> AdapterDescriptor:
    return AdapterDescriptor(
        adapter_id="windows",
        implementation_version="1",
        supports_target_capture=True,
        actions=frozenset({"windows.click"}),
        conditions=frozenset({"windows.element_exists", "windows.value_equals"}),
        assertions=frozenset({"windows.value_equals"}),
        verification_by_action={"windows.click": "assertion"},
        idempotent_actions=frozenset(),
        retryable_errors_by_action={},
        assertions_by_action={"windows.click": frozenset({"windows.value_equals"})},
        assertion_input_kind={"windows.value_equals": "text"},
    )


def target() -> TargetSpec:
    return TargetSpec.model_validate(
        {
            "adapter_id": "windows",
            "payload": {"selector": {"automation_id": "grid"}, "coordinate_fallback": None},
        }
    )


def test_no_condition_means_no_wait(qtbot: object) -> None:
    editor = WaitAssertionEditor()
    qtbot.addWidget(editor)  # type: ignore[attr-defined]
    editor.set_action(descriptor(), "windows.click")

    assert editor.pending_wait(target()) is None


def test_a_chosen_condition_becomes_a_wait_with_its_timeout(qtbot: object) -> None:
    """A step that waits for the screen is the whole point of this editor."""

    editor = WaitAssertionEditor()
    qtbot.addWidget(editor)  # type: ignore[attr-defined]
    editor.set_action(descriptor(), "windows.click")
    editor.condition_combo.setCurrentText("windows.element_exists")
    editor.timeout_ms.setValue(5_000)

    wait = editor.pending_wait(target())

    assert wait is not None
    assert wait.condition.condition_type == "windows.element_exists"
    assert wait.timeout_ms == 5_000
    assert wait.condition.target == target()


def test_an_expected_value_travels_with_the_condition(qtbot: object) -> None:
    editor = WaitAssertionEditor()
    qtbot.addWidget(editor)  # type: ignore[attr-defined]
    editor.set_action(descriptor(), "windows.click")
    editor.condition_combo.setCurrentText("windows.value_equals")
    editor.expected_input.setText("완료")

    wait = editor.pending_wait(target())

    assert wait is not None
    assert wait.condition.expected == "완료"


def test_a_chosen_assertion_is_offered_for_the_step(qtbot: object) -> None:
    editor = WaitAssertionEditor()
    qtbot.addWidget(editor)  # type: ignore[attr-defined]
    editor.set_action(descriptor(), "windows.click")
    editor.assertion_combo.setCurrentIndex(editor.assertion_combo.findData("windows.value_equals"))
    editor.assertion_expected.setText("저장됨")

    assertions = editor.pending_assertions()

    assert [item.assertion_type for item in assertions] == ["windows.value_equals"]
    assert assertions[0].expected == "저장됨"


def test_clearing_the_assertion_removes_it(qtbot: object) -> None:
    editor = WaitAssertionEditor()
    qtbot.addWidget(editor)  # type: ignore[attr-defined]
    editor.set_action(descriptor(), "windows.click")

    assert editor.pending_assertions() == ()
