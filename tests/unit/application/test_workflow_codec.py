from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import UUID

import pytest

from universal_rpa.application.workflow_codec import (
    UnsupportedSchemaVersion,
    dump_workflow,
    load_workflow,
)
from universal_rpa.domain.workflow import Workflow


def valid_workflow_payload() -> dict[str, object]:
    now = datetime(2026, 7, 27, 1, 2, 3, tzinfo=UTC)
    return {
        "schema_version": "1",
        "workflow_id": UUID("00000000-0000-0000-0000-000000000001"),
        "name": "Codec workflow",
        "revision": 2,
        "target_apps": [
            {
                "app_id": "erp",
                "process_executable": "erp.exe",
                "window_class": "ERPMain",
            }
        ],
        "steps": [
            {
                "step_id": UUID("00000000-0000-0000-0000-000000000101"),
                "label": "Wait",
                "kind": "action",
                "action_type": "windows.wait",
                "wait": {
                    "condition": {"condition_type": "windows.element_exists"},
                    "timeout_ms": 500,
                },
            }
        ],
        "created_at": now,
        "updated_at": now,
    }


@pytest.mark.parametrize("version", ["2", 1, None])
def test_unsupported_schema_version_is_rejected_before_model_validation(
    version: object,
) -> None:
    payload = valid_workflow_payload()
    payload["schema_version"] = version
    payload["workflow_id"] = "not-a-uuid"

    with pytest.raises(UnsupportedSchemaVersion):
        load_workflow(payload)


def test_load_workflow_accepts_mapping_text_and_utf8_bytes() -> None:
    payload = valid_workflow_payload()
    serialized = json.dumps(payload, default=str)

    from_mapping = load_workflow(payload)
    from_text = load_workflow(serialized)
    from_bytes = load_workflow(serialized.encode("utf-8"))

    assert isinstance(from_mapping, Workflow)
    assert from_text == from_mapping
    assert from_bytes == from_mapping


def test_dump_workflow_is_stable_sorted_utf8_json_with_trailing_newline() -> None:
    workflow = load_workflow(valid_workflow_payload())

    dumped = dump_workflow(workflow)

    assert isinstance(dumped, bytes)
    assert dumped.endswith(b"\n")
    assert dumped == dump_workflow(load_workflow(dumped))
    decoded = dumped.decode("utf-8")
    assert decoded.index('"created_at"') < decoded.index('"name"')
    assert "\\u" not in decoded
