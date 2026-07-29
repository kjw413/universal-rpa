from __future__ import annotations

import pytest
from pydantic import BaseModel

from universal_rpa.domain.targets import TargetSpec
from universal_rpa.domain.types import FrozenMapping, deep_freeze_json, thaw_json


def test_nested_json_is_defensively_copied_and_deeply_immutable() -> None:
    source = {"nested": {"items": ["safe"]}}
    target = TargetSpec(adapter_id="fake", payload=source)

    source["nested"]["items"][0] = "mutated"

    assert thaw_json(target.payload) == {"nested": {"items": ["safe"]}}
    with pytest.raises(TypeError):
        target.payload["nested"]["items"][0] = "mutated"  # type: ignore[index]


def test_deep_freeze_and_thaw_preserve_json_data_without_aliasing() -> None:
    value = {"rows": [{"id": 1}, {"id": 2}], "enabled": True}
    frozen = deep_freeze_json(value)

    assert isinstance(frozen, FrozenMapping)
    assert thaw_json(frozen) == {"rows": [{"id": 1}, {"id": 2}], "enabled": True}
    assert thaw_json(frozen) is not value


def test_frozen_mapping_factory_recursively_copies_json_values() -> None:
    source = {"nested": {"items": ["safe"]}}
    frozen = FrozenMapping.from_mapping(source)

    source["nested"]["items"][0] = "mutated"

    assert thaw_json(frozen) == {"nested": {"items": ["safe"]}}
    with pytest.raises(TypeError):
        frozen["nested"]["items"][0] = "mutated"  # type: ignore[index]


class ExampleParameters(BaseModel):
    pass


def test_frozen_mapping_factory_retains_non_json_class_leaves() -> None:
    models: FrozenMapping[str, type[BaseModel]] = FrozenMapping.from_mapping(
        {"example.action": ExampleParameters}
    )

    assert models["example.action"] is ExampleParameters


def test_frozen_mapping_factory_freezes_containers_beside_non_json_leaves() -> None:
    source = {"model": ExampleParameters, "nested": {"items": ["safe"]}}
    frozen = FrozenMapping.from_mapping(source)

    source["nested"]["items"][0] = "mutated"

    assert frozen["model"] is ExampleParameters
    assert isinstance(frozen["nested"], FrozenMapping)
    assert frozen["nested"]["items"] == ("safe",)  # type: ignore[index]
