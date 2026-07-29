from __future__ import annotations

import pytest
from pydantic import ValidationError

from universal_rpa.domain.values import RowBindingValue, SecretRefValue, VariableDefinition


def test_secret_reference_cannot_accept_plaintext() -> None:
    with pytest.raises(ValidationError):
        SecretRefValue.model_validate(
            {"mode": "secret_ref", "credential_ref": "erp/password", "value": "plain"}
        )


def test_row_binding_accepts_only_one_row_column() -> None:
    assert RowBindingValue(template="{{ row.factory }}").column_name == "factory"
    with pytest.raises(ValidationError):
        RowBindingValue(template="{{ row['factory'] }}")


def test_variable_sources_are_typed_and_cross_references_are_explicit() -> None:
    variable = VariableDefinition.model_validate(
        {
            "variable_id": "start_date",
            "label": "시작일",
            "value_type": "date",
            "source": {
                "source_type": "date_rule",
                "expression": {"operation": "run_date"},
            },
        }
    )

    assert variable.source.source_type == "date_rule"
    with pytest.raises(ValidationError):
        VariableDefinition.model_validate(
            {
                "variable_id": "password",
                "label": "비밀번호",
                "value_type": "secret",
                "source": {"source_type": "fixed_default", "value": "plain"},
            }
        )


@pytest.mark.parametrize(
    ("value_type", "source"),
    [
        ("integer", {"source_type": "fixed_default", "value": True}),
        ("decimal", {"source_type": "fixed_default", "value": float("inf")}),
        ("date", {"source_type": "fixed_default", "value": "2024/02/29"}),
        ("date", {"source_type": "fixed_default", "value": "20240229"}),
        ("choice", {"source_type": "inline_options", "options": ["A", "A"]}),
        ("text", {"source_type": "credential_ref", "credential_ref": "erp/password"}),
    ],
)
def test_variable_definition_rejects_invalid_source_type_combinations(
    value_type: str, source: dict[str, object]
) -> None:
    with pytest.raises(ValidationError):
        VariableDefinition.model_validate(
            {
                "variable_id": "value",
                "label": "Value",
                "value_type": value_type,
                "source": source,
            }
        )
