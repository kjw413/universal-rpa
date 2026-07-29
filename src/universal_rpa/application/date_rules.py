from __future__ import annotations

import calendar
from datetime import date, timedelta
from typing import assert_never

from universal_rpa.domain.targets import DateContext
from universal_rpa.domain.values import DateExpression


def evaluate_date_expression(expression: DateExpression, context: DateContext) -> date:
    """Evaluate a validated deterministic date expression without ambient clock access."""

    if expression.operation == "today":
        return context.today
    if expression.operation == "run_date":
        return context.run_date

    operand = expression.operand
    if operand is None:
        raise ValueError(f"{expression.operation} requires an operand")
    operand_date = evaluate_date_expression(operand, context)

    if expression.operation == "add_days":
        if expression.days is None:
            raise ValueError("add_days requires days")
        return operand_date + timedelta(days=expression.days)
    if expression.operation == "month_start":
        return operand_date.replace(day=1)
    if expression.operation == "month_end":
        final_day = calendar.monthrange(operand_date.year, operand_date.month)[1]
        return operand_date.replace(day=final_day)
    assert_never(expression.operation)
