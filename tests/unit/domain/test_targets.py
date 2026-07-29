from __future__ import annotations

from typing import get_type_hints

import pytest
from pydantic import ValidationError

from universal_rpa.domain.targets import (
    CoordinateFallback,
    RelativePoint,
    TargetSpec,
    UiaSelector,
    WindowsTarget,
)
from universal_rpa.domain.types import FrozenJsonObject, FrozenMapping


def test_coordinate_must_be_normalized() -> None:
    with pytest.raises(ValidationError):
        RelativePoint(x=1.01, y=0.5)


def test_coordinate_fallback_requires_recorded_window_identity() -> None:
    with pytest.raises(ValidationError):
        CoordinateFallback.model_validate(
            {
                "point": {"x": 0.5, "y": 0.5},
                "recorded_dpi_x": 96,
                "recorded_dpi_y": 96,
                "recorded_client_width": 800,
                "recorded_client_height": 600,
            }
        )


def test_windows_target_requires_selector_or_fallback() -> None:
    with pytest.raises(ValidationError):
        WindowsTarget(selector=None, coordinate_fallback=None)


def test_uia_selector_requires_at_least_one_identity_attribute() -> None:
    with pytest.raises(ValidationError):
        UiaSelector()


def test_target_models_reject_unknown_fields_and_cannot_be_mutated() -> None:
    selector = UiaSelector(automation_id="submit")

    with pytest.raises(ValidationError):
        UiaSelector(automation_id="submit", unsupported=True)  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        selector.automation_id = "cancel"


def test_target_payload_has_an_immutable_public_type_and_json_dump() -> None:
    target = TargetSpec(adapter_id="fake", payload={"nested": ["safe"]})

    assert get_type_hints(TargetSpec)["payload"] == FrozenJsonObject
    assert isinstance(target.payload, FrozenMapping)
    assert target.model_dump(mode="json") == {
        "adapter_id": "fake",
        "payload": {"nested": ["safe"]},
    }
    assert TargetSpec.model_json_schema()["properties"]["payload"] == {
        "additionalProperties": True,
        "type": "object",
    }


def test_target_payload_rejects_non_json_leaves() -> None:
    with pytest.raises(ValidationError):
        TargetSpec(adapter_id="fake", payload={"unsupported": object()})
