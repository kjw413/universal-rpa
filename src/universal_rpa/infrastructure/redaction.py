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


def _strip_unsafe_json(value: object) -> JsonValue:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Mapping):
        stripped: dict[str, JsonValue] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError("report keys must be strings")
            if key.casefold() in _UNSAFE_EVIDENCE_KEYS:
                continue
            stripped[key] = _strip_unsafe_json(item)
        return stripped
    if isinstance(value, (list, tuple)):
        return [_strip_unsafe_json(item) for item in value]
    raise ValueError("report values must be JSON values")


def redact_evidence(value: object) -> FrozenJsonObject:
    """Drop unsafe field names and return a defensive, deeply immutable copy.

    :func:`sanitize_evidence` stays fail-closed so an adapter that leaks a raw
    field is caught at the boundary.  Report projection instead runs over data
    that has already crossed that boundary and must always produce a document,
    so unsafe names are removed rather than raised on.
    """

    stripped = _strip_unsafe_json(value)
    if not isinstance(stripped, dict):
        raise ValueError("report sections must be JSON objects")
    frozen: FrozenJsonValue = deep_freeze_json(stripped)
    if not isinstance(frozen, FrozenMapping):
        raise ValueError("report sections must be JSON objects")
    return frozen


__all__ = ["redact_evidence", "sanitize_evidence"]
