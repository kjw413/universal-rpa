from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

from universal_rpa.application.date_rules import evaluate_date_expression
from universal_rpa.domain.targets import DateContext
from universal_rpa.domain.values import DateExpression


def test_month_end_handles_leap_year() -> None:
    expression = DateExpression(operation="month_end", operand=DateExpression(operation="run_date"))

    assert evaluate_date_expression(
        expression,
        DateContext(today=date(2024, 1, 1), run_date=date(2024, 2, 10)),
    ) == date(2024, 2, 29)


def test_add_days_uses_the_explicit_operand() -> None:
    expression = DateExpression(
        operation="add_days", operand=DateExpression(operation="today"), days=-2
    )

    assert evaluate_date_expression(
        expression,
        DateContext(today=date(2024, 3, 1), run_date=date(2024, 2, 10)),
    ) == date(2024, 2, 28)


@pytest.mark.parametrize(
    "expression",
    [
        {"operation": "today", "operand": {"operation": "run_date"}},
        {"operation": "month_start"},
        {"operation": "add_days", "operand": {"operation": "today"}},
        {"operation": "run_date", "days": 1},
        {"operation": "not_an_operation"},
    ],
)
def test_date_expression_rejects_invalid_operation_shapes(expression: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        DateExpression.model_validate(expression)
