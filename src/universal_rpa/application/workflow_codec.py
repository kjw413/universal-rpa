from __future__ import annotations

import json
from collections.abc import Mapping

from universal_rpa.domain.workflow import Workflow


class UnsupportedSchemaVersion(ValueError):
    """Raised before model parsing when no schema-v1 migration is available."""


def _reject_non_finite_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON number is not allowed: {value}")


def _decode_payload(
    payload: str | bytes | bytearray | Mapping[str, object],
) -> Mapping[str, object]:
    if isinstance(payload, Mapping):
        return payload
    decoded = json.loads(payload, parse_constant=_reject_non_finite_constant)
    if not isinstance(decoded, dict):
        raise UnsupportedSchemaVersion("workflow schema_version must be string '1'")
    return decoded


def load_workflow(
    payload: str | bytes | bytearray | Mapping[str, object],
) -> Workflow:
    decoded = _decode_payload(payload)
    if decoded.get("schema_version") != "1":
        raise UnsupportedSchemaVersion("unsupported workflow schema version")
    return Workflow.model_validate(decoded)


def dump_workflow(workflow: Workflow) -> bytes:
    serialized = json.dumps(
        workflow.model_dump(mode="json"),
        ensure_ascii=False,
        allow_nan=False,
        indent=2,
        sort_keys=True,
        separators=(",", ": "),
    )
    return f"{serialized}\n".encode()


def export_workflow_schema() -> bytes:
    serialized = json.dumps(
        Workflow.model_json_schema(),
        ensure_ascii=False,
        allow_nan=False,
        indent=2,
        sort_keys=True,
        separators=(",", ": "),
    )
    return f"{serialized}\n".encode()


__all__ = [
    "UnsupportedSchemaVersion",
    "dump_workflow",
    "export_workflow_schema",
    "load_workflow",
]
