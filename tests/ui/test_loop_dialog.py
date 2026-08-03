from universal_rpa.ui.loop_dialog import LoopDialog


def test_loop_dialog_shows_defaults_and_hard_limits(qtbot: object) -> None:
    dialog = LoopDialog()
    qtbot.addWidget(dialog)  # type: ignore[attr-defined]

    assert dialog.max_iterations.value() == 1_000
    assert dialog.max_iterations.maximum() == 10_000
    assert dialog.max_runtime_seconds.value() == 7_200
    assert dialog.max_runtime_seconds.maximum() == 86_400
