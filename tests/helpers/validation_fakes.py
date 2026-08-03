from __future__ import annotations

from collections.abc import Iterator, Mapping
from datetime import UTC, datetime
from pathlib import Path

from universal_rpa.adapters.fake import FakeAutomationAdapter
from universal_rpa.adapters.registry import AdapterRegistry
from universal_rpa.domain.conditions import AssertionSpec
from universal_rpa.domain.errors import ErrorCode, ValidationIssue
from universal_rpa.domain.targets import RuntimeEnvironment, TargetSpec
from universal_rpa.domain.types import DataCell, FrozenMapping
from universal_rpa.domain.workflow import ActionStep, DataSourceSpec
from universal_rpa.ports.automation import (
    AdapterDescriptor,
    AssertionInputKind,
    TargetValidationMode,
    VerificationMode,
)
from universal_rpa.ports.credentials import SecretValue
from universal_rpa.ports.data_sources import DataPreview

NOW = datetime(2026, 7, 27, tzinfo=UTC)


def descriptor(
    *,
    action: str = "fake.read",
    verification: VerificationMode = "postcondition_or_assertion",
    idempotent: bool = True,
    compatible_assertions: frozenset[str] = frozenset({"fake.equals"}),
    assertion_kind: AssertionInputKind = "json",
) -> AdapterDescriptor:
    verification_items: tuple[tuple[str, VerificationMode], ...] = ((action, verification),)
    retry_items: tuple[tuple[str, frozenset[ErrorCode]], ...] = (
        ((action, frozenset({ErrorCode.ACTION_FAILED})),) if idempotent else ()
    )
    assertion_items: tuple[tuple[str, frozenset[str]], ...] = ((action, compatible_assertions),)
    kind_items: tuple[tuple[str, AssertionInputKind], ...] = (("fake.equals", assertion_kind),)
    return AdapterDescriptor(
        adapter_id="fake",
        implementation_version="1.0",
        supports_target_capture=True,
        actions=frozenset({action}),
        conditions=frozenset({"fake.ready", "fake.element_exists"}),
        assertions=frozenset({"fake.equals"}),
        verification_by_action=FrozenMapping(verification_items),
        idempotent_actions=frozenset({action}) if idempotent else frozenset(),
        retryable_errors_by_action=FrozenMapping(retry_items),
        assertions_by_action=FrozenMapping(assertion_items),
        assertion_input_kind=FrozenMapping(kind_items),
    )


class ValidationSpyAdapter(FakeAutomationAdapter):
    def __init__(self, custom_descriptor: AdapterDescriptor | None = None) -> None:
        super().__init__(descriptor=custom_descriptor or descriptor())
        self.validated_action_specs: list[ActionStep] = []
        self.validation_modes: list[TargetValidationMode] = []

    def validate_action_spec(self, step: ActionStep) -> tuple[ValidationIssue, ...]:
        self.validated_action_specs.append(step)
        return super().validate_action_spec(step)

    def validate_target(
        self,
        target: TargetSpec,
        runtime: RuntimeEnvironment,
        mode: TargetValidationMode,
    ) -> tuple[ValidationIssue, ...]:
        self.validation_modes.append(mode)
        return super().validate_target(target, runtime, mode)


def registry_with(adapter: ValidationSpyAdapter) -> AdapterRegistry:
    registry = AdapterRegistry()
    registry.register(adapter)
    return registry


def runtime_environment() -> RuntimeEnvironment:
    return RuntimeEnvironment(
        interactive_desktop=True,
        process_id=100,
        process_executable="fake.exe",
        top_level_hwnd=200,
        window_title="Fake",
        window_class="FakeWindow",
        foreground_hwnd=200,
        dpi_x=96,
        dpi_y=96,
        client_width=1280,
        client_height=720,
        monitor_scale=1.0,
    )


def fake_target(*, matches: int = 1) -> TargetSpec:
    return TargetSpec.model_validate({"adapter_id": "fake", "payload": {"match_count": matches}})


def successful_assertion() -> AssertionSpec:
    return AssertionSpec(assertion_type="fake.equals", expected=True)


class MemoryDataSources:
    def __init__(self, previews: Mapping[str, DataPreview]) -> None:
        self._previews = dict(previews)
        self.preview_calls: list[str] = []

    def preview(
        self,
        project_dir: Path,
        spec: DataSourceSpec,
        max_rows: int = 20,
    ) -> DataPreview:
        del project_dir, max_rows
        self.preview_calls.append(spec.data_source_id)
        return self._previews[spec.data_source_id]

    def iter_rows(
        self,
        project_dir: Path,
        spec: DataSourceSpec,
        required_columns: frozenset[str],
    ) -> Iterator[FrozenMapping[str, DataCell]]:
        del project_dir, spec, required_columns
        return iter(())


class MemorySecrets:
    def __init__(self, references: frozenset[str]) -> None:
        self._references = references

    def exists(self, reference: str) -> bool:
        return reference in self._references

    def read(self, reference: str) -> SecretValue:
        if not self.exists(reference):
            raise KeyError(reference)
        return SecretValue.from_text("test-only")


__all__ = [
    "MemoryDataSources",
    "MemorySecrets",
    "ValidationSpyAdapter",
    "descriptor",
    "fake_target",
    "registry_with",
    "runtime_environment",
    "successful_assertion",
]
