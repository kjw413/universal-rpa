from __future__ import annotations

import hashlib
import json
import re
from dataclasses import replace
from importlib.metadata import entry_points
from typing import Any

from universal_rpa.domain.errors import ErrorCode, RpaError
from universal_rpa.ports.automation import AdapterDescriptor, AutomationAdapter

_ADAPTER_ID = re.compile(r"^[a-z][a-z0-9_]*$")
_VERIFICATION_MODES = {"postcondition_or_assertion", "intrinsic", "none"}
_ASSERTION_INPUT_KINDS = {"json", "table", "output_commit"}


def _canonical_descriptor(descriptor: AdapterDescriptor) -> dict[str, object]:
    return {
        "adapter_id": descriptor.adapter_id,
        "implementation_version": descriptor.implementation_version,
        "supports_target_capture": descriptor.supports_target_capture,
        "actions": sorted(descriptor.actions),
        "conditions": sorted(descriptor.conditions),
        "assertions": sorted(descriptor.assertions),
        "verification_by_action": [
            [key, value] for key, value in descriptor.verification_by_action.items()
        ],
        "idempotent_actions": sorted(descriptor.idempotent_actions),
        "retryable_errors_by_action": [
            [key, sorted(error.value for error in errors)]
            for key, errors in descriptor.retryable_errors_by_action.items()
        ],
        "assertions_by_action": [
            [key, sorted(assertions)] for key, assertions in descriptor.assertions_by_action.items()
        ],
        "assertion_input_kind": [
            [key, value] for key, value in descriptor.assertion_input_kind.items()
        ],
    }


def _fingerprint(descriptors: list[AdapterDescriptor]) -> str:
    canonical = [
        _canonical_descriptor(descriptor)
        for descriptor in sorted(descriptors, key=lambda item: item.adapter_id)
    ]
    payload = json.dumps(
        canonical,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


EMPTY_REGISTRY_FINGERPRINT = _fingerprint([])


def _snapshot(descriptor: AdapterDescriptor) -> AdapterDescriptor:
    return replace(descriptor)


def _validate_names(names: frozenset[str], adapter_id: str, label: str) -> None:
    prefix = f"{adapter_id}."
    if any(not name.startswith(prefix) for name in names):
        raise ValueError(f"{label} must use adapter namespace")


def _validate_descriptor(adapter: AutomationAdapter, descriptor: AdapterDescriptor) -> None:
    if not _ADAPTER_ID.fullmatch(adapter.adapter_id):
        raise ValueError("invalid adapter id")
    if adapter.adapter_id != descriptor.adapter_id:
        raise ValueError("adapter id must match descriptor")
    if not descriptor.implementation_version.strip():
        raise ValueError("implementation version must be nonblank")
    _validate_names(descriptor.actions, descriptor.adapter_id, "action")
    _validate_names(descriptor.conditions, descriptor.adapter_id, "condition")
    _validate_names(descriptor.assertions, descriptor.adapter_id, "assertion")

    verification_keys = set(descriptor.verification_by_action)
    if verification_keys != set(descriptor.actions):
        raise ValueError("every action must have exactly one verification mode")
    if any(mode not in _VERIFICATION_MODES for mode in descriptor.verification_by_action.values()):
        raise ValueError("invalid verification mode")
    if not descriptor.idempotent_actions <= descriptor.actions:
        raise ValueError("idempotent action must be declared")

    retry_keys = set(descriptor.retryable_errors_by_action)
    if not retry_keys <= descriptor.actions:
        raise ValueError("retry metadata action must be declared")
    if not retry_keys <= descriptor.idempotent_actions:
        raise ValueError("retryable action must be idempotent")
    if any(
        not isinstance(error, ErrorCode)
        for errors in descriptor.retryable_errors_by_action.values()
        for error in errors
    ):
        raise ValueError("retry metadata must use common error codes")

    assertion_action_keys = set(descriptor.assertions_by_action)
    if not assertion_action_keys <= descriptor.actions:
        raise ValueError("assertion metadata action must be declared")
    if any(
        not compatible <= descriptor.assertions
        for compatible in descriptor.assertions_by_action.values()
    ):
        raise ValueError("compatible assertion must be declared")

    input_keys = set(descriptor.assertion_input_kind)
    if input_keys != set(descriptor.assertions):
        raise ValueError("every assertion must have exactly one input kind")
    if any(kind not in _ASSERTION_INPUT_KINDS for kind in descriptor.assertion_input_kind.values()):
        raise ValueError("invalid assertion input kind")
    if not callable(getattr(adapter, "evaluate_assertion", None)):
        raise ValueError("declared assertions require an evaluation route")


class _RegisteredAdapter:
    """Delegates behavior while keeping the registered descriptor immutable."""

    def __init__(self, adapter: AutomationAdapter, descriptor: AdapterDescriptor) -> None:
        self._adapter = adapter
        self._descriptor = descriptor

    @property
    def adapter_id(self) -> str:
        return self._descriptor.adapter_id

    def descriptor(self) -> AdapterDescriptor:
        return self._descriptor

    def __getattr__(self, name: str) -> Any:
        return getattr(self._adapter, name)


class AdapterRegistry:
    def __init__(self) -> None:
        self._adapters: dict[str, AutomationAdapter] = {}
        self._descriptors: dict[str, AdapterDescriptor] = {}

    def register(self, adapter: AutomationAdapter) -> None:
        descriptor = _snapshot(adapter.descriptor())
        _validate_descriptor(adapter, descriptor)
        if adapter.adapter_id in self._adapters:
            raise ValueError(f"adapter id already registered: {adapter.adapter_id}")
        registered = _RegisteredAdapter(adapter, descriptor)
        self._adapters[adapter.adapter_id] = registered
        self._descriptors[adapter.adapter_id] = descriptor

    def require(self, adapter_id: str) -> AutomationAdapter:
        try:
            return self._adapters[adapter_id]
        except KeyError:
            raise RpaError(
                ErrorCode.ADAPTER_MISSING,
                "필요한 자동화 어댑터가 없습니다",
            ) from None

    def load_entry_points(
        self,
        group: str = "universal_rpa.adapters",
    ) -> tuple[str, ...]:
        selected = sorted(entry_points(group=group), key=lambda item: item.name)
        registered: list[str] = []
        for item in selected:
            factory = item.load()
            if not callable(factory):
                raise TypeError("adapter entry point must load a factory")
            adapter = factory()
            self.register(adapter)
            registered.append(adapter.adapter_id)
        return tuple(registered)

    def adapter_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._descriptors))

    def descriptors(self) -> tuple[AdapterDescriptor, ...]:
        return tuple(self._descriptors[adapter_id] for adapter_id in sorted(self._descriptors))

    def descriptor_fingerprint(self) -> str:
        return _fingerprint(list(self._descriptors.values()))


__all__ = ["EMPTY_REGISTRY_FINGERPRINT", "AdapterRegistry"]
