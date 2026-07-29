from __future__ import annotations

import pytest

from universal_rpa.domain.types import FrozenMapping, thaw_json
from universal_rpa.infrastructure.redaction import sanitize_evidence


@pytest.mark.parametrize(
    "unsafe_key",
    [
        "text",
        "RAW_TEXT",
        "Clipboard",
        "clipboard_text",
        "secret",
        "PASSWORD",
        "Token",
        "VaLuE",
    ],
)
def test_sanitize_evidence_rejects_unsafe_keys_case_insensitively(
    unsafe_key: str,
) -> None:
    with pytest.raises(ValueError):
        sanitize_evidence({"context": {"items": [{"safe": {unsafe_key: "private"}}]}})


def test_sanitize_evidence_defensively_copies_and_freezes_nested_json() -> None:
    source = {"adapter": "fake", "details": [{"row_count": 2}]}
    evidence = sanitize_evidence(source)

    source["details"][0]["row_count"] = 99

    assert isinstance(evidence, FrozenMapping)
    assert thaw_json(evidence) == {
        "adapter": "fake",
        "details": [{"row_count": 2}],
    }
    with pytest.raises(TypeError):
        evidence["details"][0]["row_count"] = 3  # type: ignore[index]


@pytest.mark.parametrize("value", [["not", "an", "object"], "message", object()])
def test_sanitize_evidence_requires_an_ordinary_json_object(value: object) -> None:
    with pytest.raises(ValueError):
        sanitize_evidence(value)
