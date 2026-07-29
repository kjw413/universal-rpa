from __future__ import annotations

from typing import get_type_hints

import pytest
from pydantic import ValidationError

from universal_rpa.domain.conditions import (
    AssertionSpec,
    ConditionSpec,
    TableAssertionSpec,
    WaitSpec,
)
from universal_rpa.domain.types import FrozenJsonValue, FrozenMapping


def test_every_wait_has_a_finite_timeout() -> None:
    with pytest.raises(ValidationError):
        WaitSpec.model_validate(
            {"condition": {"condition_type": "windows.element_exists"}, "timeout_ms": 0}
        )


def test_wait_rejects_timeout_above_one_day() -> None:
    with pytest.raises(ValidationError):
        WaitSpec(
            condition=ConditionSpec(condition_type="windows.element_exists"),
            timeout_ms=86_400_001,
        )


def test_wait_rejects_poll_interval_longer_than_timeout() -> None:
    with pytest.raises(ValidationError):
        WaitSpec(
            condition=ConditionSpec(condition_type="windows.element_exists"),
            timeout_ms=99,
            poll_interval_ms=100,
        )


def test_table_assertion_rejects_inverted_row_range() -> None:
    with pytest.raises(ValidationError):
        TableAssertionSpec(min_rows=10, max_rows=9)


@pytest.mark.parametrize("condition_type", ["Windows.exists", "windows", "windows.exists-now"])
def test_condition_type_uses_adapter_qualified_identifier(condition_type: str) -> None:
    with pytest.raises(ValidationError):
        ConditionSpec(condition_type=condition_type)


def test_condition_expected_value_is_copied_frozen_and_serialized_as_json() -> None:
    source = {"rows": [{"id": 1}]}
    condition = ConditionSpec(condition_type="windows.value_equals", expected=source)

    source["rows"][0]["id"] = 99

    assert get_type_hints(ConditionSpec)["expected"] == FrozenJsonValue
    assert isinstance(condition.expected, FrozenMapping)
    assert condition.model_dump(mode="json") == {
        "condition_type": "windows.value_equals",
        "target": None,
        "expected": {"rows": [{"id": 1}]},
    }


def test_assertion_expected_value_does_not_alias_caller_containers() -> None:
    source = ["ready", {"count": 2}]
    assertion = AssertionSpec(assertion_type="windows.value_equals", expected=source)

    source[1]["count"] = 9

    assert assertion.model_dump(mode="json")["expected"] == ["ready", {"count": 2}]


def test_condition_models_are_frozen_and_reject_unknown_fields() -> None:
    condition = ConditionSpec(condition_type="windows.element_exists")

    with pytest.raises(ValidationError):
        condition.condition_type = "windows.element_absent"
    with pytest.raises(ValidationError):
        WaitSpec.model_validate(
            {
                "condition": {"condition_type": "windows.element_exists"},
                "timeout_ms": 100,
                "unexpected": True,
            }
        )
