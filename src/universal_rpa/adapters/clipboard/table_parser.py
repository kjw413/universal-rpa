"""Strict TSV/CSV clipboard table parsing with no implicit data repair."""

from __future__ import annotations

import csv
from io import StringIO
from typing import Literal

from universal_rpa.domain.errors import ErrorCode, RpaError
from universal_rpa.domain.results import TableData


def parse_clipboard_table(
    text: str,
    *,
    delimiter: Literal["auto", "tab", "comma"] = "auto",
) -> TableData:
    if not isinstance(text, str) or not text:
        raise RpaError(ErrorCode.DATA_SOURCE_INVALID, "클립보드에 표 형식의 텍스트가 없습니다.")
    first = next((line for line in text.splitlines() if line), "")
    selected = "\t" if delimiter == "tab" or (delimiter == "auto" and "\t" in first) else ","
    try:
        rows = tuple(
            tuple(cell for cell in row) for row in csv.reader(StringIO(text), delimiter=selected)
        )
    except csv.Error:
        raise RpaError(
            ErrorCode.DATA_SOURCE_INVALID, "클립보드 표 형식을 해석할 수 없습니다."
        ) from None
    if len(rows) < 1:
        raise RpaError(ErrorCode.DATA_SOURCE_INVALID, "클립보드 표에 헤더가 없습니다.")
    headers = tuple(rows[0])
    if (
        not headers
        or any(not header.strip() for header in headers)
        or len(set(headers)) != len(headers)
    ):
        raise RpaError(ErrorCode.DATA_SOURCE_INVALID, "클립보드 표의 헤더가 올바르지 않습니다.")
    if any(len(row) != len(headers) for row in rows[1:]):
        raise RpaError(ErrorCode.DATA_SOURCE_INVALID, "클립보드 표의 열 수가 일치하지 않습니다.")
    try:
        return TableData(headers=headers, rows=rows[1:])
    except ValueError:
        raise RpaError(
            ErrorCode.DATA_SOURCE_INVALID, "클립보드 표의 값이 올바르지 않습니다."
        ) from None


__all__ = ["parse_clipboard_table"]
