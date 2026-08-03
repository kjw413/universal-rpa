from universal_rpa.ui.variable_dialog import VariableDialog


def test_variable_dialog_enforces_source_type_matrix(qtbot: object) -> None:
    dialog = VariableDialog()
    qtbot.addWidget(dialog)  # type: ignore[attr-defined]
    dialog.variable_id.setText("query_date")
    dialog.label_input.setText("조회일")
    dialog.value_type.setCurrentText("date")
    dialog.source_type.setCurrentText("run_input")

    variable = dialog.variable_definition()

    assert variable is not None
    assert variable.variable_id == "query_date"
    assert variable.source.source_type == "run_input"


def test_invalid_secret_fixed_default_is_rejected(qtbot: object) -> None:
    dialog = VariableDialog()
    qtbot.addWidget(dialog)  # type: ignore[attr-defined]
    dialog.variable_id.setText("password")
    dialog.label_input.setText("비밀번호")
    dialog.value_type.setCurrentText("secret")
    dialog.source_type.setCurrentText("fixed_default")
    dialog.source_value.setText("plaintext")

    assert dialog.variable_definition() is None
