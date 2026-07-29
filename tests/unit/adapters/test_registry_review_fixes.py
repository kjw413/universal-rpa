from __future__ import annotations

from dataclasses import replace

import pytest

from tests.unit.adapters.test_registry import descriptor
from universal_rpa.adapters.fake import FakeAutomationAdapter
from universal_rpa.adapters.registry import AdapterRegistry
from universal_rpa.domain.errors import ErrorCode, RpaError
from universal_rpa.domain.types import FrozenMapping


@pytest.mark.parametrize(
    ("field_name", "duplicate_mapping"),
    (
        (
            "verification_by_action",
            FrozenMapping(
                (
                    ("fake.read", "intrinsic"),
                    ("fake.read", "none"),
                )
            ),
        ),
        (
            "retryable_errors_by_action",
            FrozenMapping(
                (
                    ("fake.read", frozenset({ErrorCode.ACTION_FAILED})),
                    ("fake.read", frozenset({ErrorCode.INTERNAL_ERROR})),
                )
            ),
        ),
        (
            "assertions_by_action",
            FrozenMapping(
                (
                    ("fake.read", frozenset({"fake.equals"})),
                    ("fake.read", frozenset()),
                )
            ),
        ),
        (
            "assertion_input_kind",
            FrozenMapping(
                (
                    ("fake.equals", "json"),
                    ("fake.equals", "table"),
                )
            ),
        ),
    ),
)
def test_descriptor_rejects_duplicate_mapping_keys(
    field_name: str,
    duplicate_mapping: FrozenMapping[str, object],
) -> None:
    with pytest.raises(ValueError, match=rf"{field_name} contains duplicate key"):
        replace(descriptor(), **{field_name: duplicate_mapping})


def test_descriptor_rejects_unhashable_mapping_key_with_field_specific_error() -> None:
    unhashable = FrozenMapping(
        ((["fake.read"], "intrinsic"),)  # type: ignore[list-item]
    )

    with pytest.raises(
        ValueError,
        match="verification_by_action keys must be hashable strings",
    ):
        replace(descriptor(), verification_by_action=unhashable)


def test_duplicate_descriptor_cannot_partially_register_or_change_fingerprint() -> None:
    registry = AdapterRegistry()
    registry.register(FakeAutomationAdapter(adapter_id="web"))
    before = registry.descriptor_fingerprint()
    duplicate = FrozenMapping(
        (
            ("fake.read", "intrinsic"),
            ("fake.read", "none"),
        )
    )

    with pytest.raises(
        ValueError,
        match="verification_by_action contains duplicate key",
    ):
        registry.register(
            FakeAutomationAdapter(
                descriptor=replace(
                    descriptor(),
                    verification_by_action=duplicate,
                )
            )
        )

    assert registry.descriptor_fingerprint() == before
    assert registry.require("web").adapter_id == "web"
    with pytest.raises(RpaError) as missing:
        registry.require("fake")
    assert missing.value.code is ErrorCode.ADAPTER_MISSING
