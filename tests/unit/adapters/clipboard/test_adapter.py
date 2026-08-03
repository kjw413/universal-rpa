from __future__ import annotations

from collections import deque

from tests.unit.application.test_conditions import _context
from universal_rpa.adapters.clipboard.adapter import ClipboardAutomationAdapter
from universal_rpa.domain.errors import ErrorCode
from universal_rpa.domain.results import TableData
from universal_rpa.domain.types import FrozenMapping
from universal_rpa.ports.automation import ActionRequest, CancellationToken


class _Clipboard:
    def __init__(self, text: str, sequences: tuple[int, ...]) -> None:
        self._text = text
        self._sequences = deque(sequences)

    def sequence_number(self) -> int:
        return self._sequences.popleft() if len(self._sequences) > 1 else self._sequences[0]

    def text(self) -> str:
        return self._text

    def formats(self) -> tuple[str, ...]:
        return ("CF_UNICODETEXT",)


def _request() -> ActionRequest:
    return ActionRequest(
        action_type="clipboard.extract_table",
        target=None,
        parameters=FrozenMapping.empty(),
        value=None,
        has_postcondition_or_assertion=True,
    )


def test_extract_table_keeps_body_out_of_evidence(tmp_path) -> None:
    adapter = ClipboardAutomationAdapter(_Clipboard("factory\tcount\nF-001\t3", (10, 10)))

    result = adapter.execute(_request(), _context(tmp_path), CancellationToken())

    assert result.error_code is None
    assert isinstance(result.output, TableData)
    assert result.output.rows == (("F-001", "3"),)
    assert "F-001" not in str(result.evidence)
    assert result.evidence["row_count"] == 1


def test_clipboard_change_during_read_fails_without_returning_stale_table(tmp_path) -> None:
    adapter = ClipboardAutomationAdapter(_Clipboard("a\tb\n1\t2", (10, 11)))

    result = adapter.execute(_request(), _context(tmp_path), CancellationToken())

    assert result.error_code is ErrorCode.ACTION_FAILED
    assert result.output is None
