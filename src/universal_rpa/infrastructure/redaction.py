from __future__ import annotations

from collections.abc import Mapping

from universal_rpa.domain.types import (
    FrozenJsonObject,
    FrozenJsonValue,
    FrozenMapping,
    JsonValue,
    deep_freeze_json,
)

_UNSAFE_EVIDENCE_KEYS = frozenset(
    {
        "text",
        "raw_text",
        "clipboard",
        "clipboard_text",
        "secret",
        "password",
        "token",
        "value",
    }
)


def _validate_safe_json(value: object) -> JsonValue:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Mapping):
        normalized: dict[str, JsonValue] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError("evidence keys must be strings")
            if key.casefold() in _UNSAFE_EVIDENCE_KEYS:
                raise ValueError(f"unsafe evidence key: {key}")
            normalized[key] = _validate_safe_json(item)
        return normalized
    if isinstance(value, (list, tuple)):
        return [_validate_safe_json(item) for item in value]
    raise ValueError("evidence must contain only JSON values")


def sanitize_evidence(value: object) -> FrozenJsonObject:
    """Reject unsafe field names and return a defensive, deeply immutable copy."""

    normalized = _validate_safe_json(value)
    if not isinstance(normalized, dict):
        raise ValueError("evidence must be a JSON object")
    frozen: FrozenJsonValue = deep_freeze_json(normalized)
    if not isinstance(frozen, FrozenMapping):
        raise ValueError("evidence must be a JSON object")
    return frozen


__all__ = ["sanitize_evidence"]
