from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest

import universal_rpa.adapters.registry as registry_module
from universal_rpa.adapters.fake import FakeAutomationAdapter
from universal_rpa.adapters.registry import AdapterRegistry
from universal_rpa.domain.errors import ErrorCode, RpaError
from universal_rpa.domain.types import FrozenMapping
from universal_rpa.ports.automation import AdapterDescriptor


def descriptor(
    adapter_id: str = "fake",
    *,
    implementation_version: str = "1.0",
    actions: frozenset[str] | None = None,
    conditions: frozenset[str] | None = None,
    assertions: frozenset[str] | None = None,
    verification_by_action: object | None = None,
    idempotent_actions: frozenset[str] | None = None,
    retryable_errors_by_action: object | None = None,
    assertions_by_action: object | None = None,
    assertion_input_kind: object | None = None,
) -> AdapterDescriptor:
    actions = frozenset({f"{adapter_id}.read"}) if actions is None else actions
    conditions = frozenset({f"{adapter_id}.ready"}) if conditions is None else conditions
    assertions = frozenset({f"{adapter_id}.equals"}) if assertions is None else assertions
    if verification_by_action is None:
        verification_by_action = {action: "postcondition_or_assertion" for action in actions}
    if idempotent_actions is None:
        idempotent_actions = actions
    if retryable_errors_by_action is None:
        retryable_errors_by_action = {action: {ErrorCode.ACTION_FAILED} for action in actions}
    if assertions_by_action is None:
        assertions_by_action = {action: assertions for action in actions}
    if assertion_input_kind is None:
        assertion_input_kind = {assertion: "json" for assertion in assertions}
    return AdapterDescriptor(
        adapter_id=adapter_id,
        implementation_version=implementation_version,
        supports_target_capture=True,
        actions=actions,
        conditions=conditions,
        assertions=assertions,
        verification_by_action=verification_by_action,  # type: ignore[arg-type]
        idempotent_actions=idempotent_actions,
        retryable_errors_by_action=retryable_errors_by_action,  # type: ignore[arg-type]
        assertions_by_action=assertions_by_action,  # type: ignore[arg-type]
        assertion_input_kind=assertion_input_kind,  # type: ignore[arg-type]
    )


def test_registry_rejects_retry_metadata_outside_declared_actions() -> None:
    adapter = FakeAutomationAdapter(
        descriptor=descriptor(
            idempotent_actions=frozenset({"fake.unknown"}),
            retryable_errors_by_action={"fake.read": frozenset({ErrorCode.ACTION_FAILED})},
        )
    )

    with pytest.raises(ValueError, match="idempotent action must be declared"):
        AdapterRegistry().register(adapter)


def test_registry_rejects_duplicate_adapter_id_without_replacing_first() -> None:
    registry = AdapterRegistry()
    first = FakeAutomationAdapter(adapter_id="fake")
    registry.register(first)

    with pytest.raises(ValueError, match="already registered"):
        registry.register(FakeAutomationAdapter(adapter_id="fake"))

    assert registry.require("fake").descriptor() == first.descriptor()


def test_descriptor_source_mutation_cannot_change_registered_capability_or_fingerprint() -> None:
    errors = {ErrorCode.ACTION_FAILED}
    source = {"fake.read": errors}
    source_assertions = {"fake.read": {"fake.equals"}}
    adapter_descriptor = descriptor(
        retryable_errors_by_action=source,
        assertions_by_action=source_assertions,
    )
    registry = AdapterRegistry()
    registry.register(FakeAutomationAdapter(descriptor=adapter_descriptor))
    before = registry.descriptor_fingerprint()
    errors.clear()
    errors.add(ErrorCode.TARGET_NOT_FOUND)
    source["fake.read"] = {ErrorCode.INTERNAL_ERROR}
    source_assertions["fake.read"].add("fake.other")

    registered = registry.require("fake").descriptor()

    assert registered.retryable_errors_by_action["fake.read"] == frozenset(
        {ErrorCode.ACTION_FAILED}
    )
    assert registered.assertions_by_action["fake.read"] == frozenset({"fake.equals"})
    assert registry.descriptor_fingerprint() == before
    assert isinstance(registered.retryable_errors_by_action, FrozenMapping)
    assert isinstance(registered.assertions_by_action, FrozenMapping)


@pytest.mark.parametrize(
    ("adapter_id", "expected_message"),
    (
        ("Fake", "invalid adapter id"),
        ("fake-id", "invalid adapter id"),
        ("1fake", "invalid adapter id"),
    ),
)
def test_registry_rejects_malformed_adapter_ids(adapter_id: str, expected_message: str) -> None:
    with pytest.raises(ValueError, match=expected_message):
        AdapterRegistry().register(FakeAutomationAdapter(adapter_id=adapter_id))


def test_registry_rejects_adapter_and_descriptor_id_mismatch() -> None:
    with pytest.raises(ValueError, match="adapter id must match descriptor"):
        AdapterRegistry().register(
            FakeAutomationAdapter(adapter_id="fake", descriptor=descriptor("other"))
        )


@pytest.mark.parametrize(
    ("change", "message"),
    (
        ({"implementation_version": "  "}, "implementation version"),
        ({"actions": frozenset({"other.read"})}, "action must use adapter namespace"),
        (
            {"conditions": frozenset({"other.ready"})},
            "condition must use adapter namespace",
        ),
        (
            {"assertions": frozenset({"other.equals"})},
            "assertion must use adapter namespace",
        ),
        (
            {"verification_by_action": {}},
            "every action must have exactly one verification mode",
        ),
        (
            {"verification_by_action": {"fake.read": "sometimes"}},
            "invalid verification mode",
        ),
        (
            {"retryable_errors_by_action": {"fake.unknown": {ErrorCode.ACTION_FAILED}}},
            "retry metadata action must be declared",
        ),
        (
            {"idempotent_actions": frozenset()},
            "retryable action must be idempotent",
        ),
        (
            {"assertions_by_action": {"fake.unknown": {"fake.equals"}}},
            "assertion metadata action must be declared",
        ),
        (
            {"assertions_by_action": {"fake.read": {"fake.unknown"}}},
            "compatible assertion must be declared",
        ),
        (
            {"assertion_input_kind": {}},
            "every assertion must have exactly one input kind",
        ),
        (
            {"assertion_input_kind": {"fake.equals": "bytes"}},
            "invalid assertion input kind",
        ),
    ),
)
def test_registry_rejects_invalid_descriptor_metadata(change: dict[str, Any], message: str) -> None:
    invalid = replace(descriptor(), **change)

    with pytest.raises(ValueError, match=message):
        AdapterRegistry().register(FakeAutomationAdapter(descriptor=invalid))


def test_failed_registration_does_not_partially_register_adapter() -> None:
    registry = AdapterRegistry()
    invalid = replace(descriptor(), implementation_version="")

    with pytest.raises(ValueError):
        registry.register(FakeAutomationAdapter(descriptor=invalid))
    with pytest.raises(RpaError) as missing:
        registry.require("fake")

    assert missing.value.code is ErrorCode.ADAPTER_MISSING
    assert registry.descriptor_fingerprint() == registry_module.EMPTY_REGISTRY_FINGERPRINT


def test_require_unknown_adapter_raises_safe_typed_error() -> None:
    with pytest.raises(RpaError) as missing:
        AdapterRegistry().require("missing")

    assert missing.value.code is ErrorCode.ADAPTER_MISSING
    assert missing.value.safe_message == "필요한 자동화 어댑터가 없습니다"


def test_fingerprint_is_canonical_and_covers_complete_descriptor() -> None:
    first = AdapterRegistry()
    first.register(FakeAutomationAdapter(adapter_id="web"))
    first.register(FakeAutomationAdapter(adapter_id="http"))
    second = AdapterRegistry()
    second.register(FakeAutomationAdapter(adapter_id="http"))
    second.register(FakeAutomationAdapter(adapter_id="web"))
    changed = AdapterRegistry()
    changed.register(
        FakeAutomationAdapter(
            adapter_id="http",
            descriptor=replace(
                FakeAutomationAdapter(adapter_id="http").descriptor(),
                implementation_version="2",
            ),
        )
    )
    changed.register(FakeAutomationAdapter(adapter_id="web"))

    assert first.descriptor_fingerprint() == second.descriptor_fingerprint()
    assert first.descriptor_fingerprint() != changed.descriptor_fingerprint()


def test_reserved_future_adapter_ids_register_only_deterministic_fakes() -> None:
    registry = AdapterRegistry()

    for adapter_id in ("web", "http", "mail", "fileops"):
        registry.register(FakeAutomationAdapter(adapter_id=adapter_id))

    assert tuple(
        registry.require(adapter_id).adapter_id for adapter_id in ("web", "http", "mail", "fileops")
    ) == ("web", "http", "mail", "fileops")


class FakeEntryPoint:
    def __init__(
        self,
        name: str,
        adapter_id: str,
        factory_order: list[str],
    ) -> None:
        self.name = name
        self.adapter_id = adapter_id
        self.factory_order = factory_order
        self.load_count = 0
        self.factory_count = 0

    def load(self) -> object:
        self.load_count += 1

        def factory() -> FakeAutomationAdapter:
            self.factory_count += 1
            self.factory_order.append(self.name)
            return FakeAutomationAdapter(adapter_id=self.adapter_id)

        return factory


def test_load_entry_points_sorts_by_name_and_invokes_each_factory_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory_order: list[str] = []
    entries = (
        FakeEntryPoint("zeta", "mail", factory_order),
        FakeEntryPoint("alpha", "web", factory_order),
        FakeEntryPoint("middle", "http", factory_order),
    )
    selected_groups: list[str] = []

    def installed_entry_points(*, group: str) -> tuple[FakeEntryPoint, ...]:
        selected_groups.append(group)
        return entries

    monkeypatch.setattr(registry_module, "entry_points", installed_entry_points)
    registry = AdapterRegistry()

    loaded = registry.load_entry_points()

    assert loaded == ("web", "http", "mail")
    assert factory_order == ["alpha", "middle", "zeta"]
    assert selected_groups == ["universal_rpa.adapters"]
    assert [entry.load_count for entry in entries] == [1, 1, 1]
    assert [entry.factory_count for entry in entries] == [1, 1, 1]


def test_registry_routes_namespaced_assertion_to_its_owner() -> None:
    registry = AdapterRegistry()
    registry.register(FakeAutomationAdapter(adapter_id="web"))
    registry.register(FakeAutomationAdapter(adapter_id="http"))

    owner = registry.require("web")

    assert "web.equals" in owner.descriptor().assertions
    assert "http.equals" not in owner.descriptor().assertions
