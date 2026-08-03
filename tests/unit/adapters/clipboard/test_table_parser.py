from __future__ import annotations

import pytest

from universal_rpa.adapters.clipboard.table_parser import parse_clipboard_table
from universal_rpa.domain.errors import ErrorCode, RpaError


def test_parser_supports_quoted_csv_without_repairing_cells() -> None:
    table = parse_clipboard_table('name,note\nA,"x,y"')

    assert table.headers == ("name", "note")
    assert table.rows == (("A", "x,y"),)


def test_parser_rejects_row_width_drift() -> None:
    with pytest.raises(RpaError) as caught:
        parse_clipboard_table("a\tb\n1")

    assert caught.value.code is ErrorCode.DATA_SOURCE_INVALID
