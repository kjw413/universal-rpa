"""Canonical, secret-safe fingerprints and output validation for resume."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID

from universal_rpa.adapters.registry import AdapterRegistry
from universal_rpa.application.loops import DataSourceSnapshot
from universal_rpa.application.variable_preparation import PreparedVariables
from universal_rpa.application.workflow_codec import dump_workflow
from universal_rpa.domain.errors import ErrorCode, RpaError
from universal_rpa.domain.results import LoopCursor, OutputCommit
from universal_rpa.domain.targets import RuntimeEnvironment
from universal_rpa.domain.types import FrozenMapping
from universal_rpa.domain.workflow import IfPresentStep, LoopStep, Step, Workflow
from universal_rpa.infrastructure.checkpoint_store import (
    AdapterFingerprint,
    DataSourceFingerprint,
    ResumeFingerprint,
)
from universal_rpa.ports.automation import AdapterDescriptor, PreparedValue
from universal_rpa.ports.credentials import SecretStorePort


def _hash_json(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _prepared_value(value: PreparedValue) -> dict[str, object]:
    if isinstance(value, Decimal):
        return {"type": "decimal", "value": str(value)}
    if isinstance(value, date):
        return {"type": "date", "value": value.isoformat()}
    if isinstance(value, Path):
        return {"type": "path", "value": value.as_posix()}
    return {"type": type(value).__name__, "value": value}


def _descriptor_payload(descriptor: AdapterDescriptor) -> dict[str, object]:
    return {
        "adapter_id": descriptor.adapter_id,
        "implementation_version": descriptor.implementation_version,
        "supports_target_capture": descriptor.supports_target_capture,
        "actions": sorted(descriptor.actions),
        "conditions": sorted(descriptor.conditions),
        "assertions": sorted(descriptor.assertions),
        "verification_by_action": list(descriptor.verification_by_action.items()),
        "idempotent_actions": sorted(descriptor.idempotent_actions),
        "retryable_errors_by_action": [
            [action, sorted(error.value for error in errors)]
            for action, errors in descriptor.retryable_errors_by_action.items()
        ],
        "assertions_by_action": [
            [action, sorted(assertions)]
            for action, assertions in descriptor.assertions_by_action.items()
        ],
        "assertion_input_kind": list(descriptor.assertion_input_kind.items()),
    }


def _uses_coordinate_fallback(steps: tuple[Step, ...]) -> bool:
    for step in steps:
        target = getattr(step, "target", None)
        if target is not None and target.adapter_id == "windows":
            if target.payload.get("coordinate_fallback") is not None:
                return True
        if isinstance(step, (LoopStep, IfPresentStep)) and _uses_coordinate_fallback(step.steps):
            return True
    return False


class ResumeFingerprintBuilder:
    def build(
        self,
        *,
        workflow: Workflow,
        output_root: Path,
        prepared: PreparedVariables,
        snapshots: FrozenMapping[str, DataSourceSnapshot],
        registry: AdapterRegistry,
        runtime: RuntimeEnvironment | None,
        secret_store: SecretStorePort,
    ) -> ResumeFingerprint:
        input_payload = {
            "values": [
                [key, _prepared_value(value)] for key, value in sorted(prepared.values.items())
            ],
            "credentials": [
                [variable_id, reference, secret_store.exists(reference)]
                for variable_id, reference in sorted(prepared.credential_refs.items())
            ],
        }
        data_sources = tuple(
            DataSourceFingerprint(
                data_source_id=snapshot.data_source_id,
                source_type=snapshot.source_type,
                row_count=len(snapshot.rows),
                content_sha256=snapshot.content_sha256,
            )
            for _, snapshot in sorted(snapshots.items())
        )
        adapters = tuple(
            AdapterFingerprint(
                adapter_id=descriptor.adapter_id,
                implementation_version=descriptor.implementation_version,
                descriptor_sha256=_hash_json(_descriptor_payload(descriptor)),
            )
            for descriptor in registry.descriptors()
        )
        environment: dict[str, object] = {"available": runtime is not None}
        if runtime is not None:
            environment.update(
                {
                    "process_executable": runtime.process_executable.casefold(),
                    "window_class": runtime.window_class,
                }
            )
            if _uses_coordinate_fallback(workflow.steps):
                environment.update(
                    {
                        "dpi_x": runtime.dpi_x,
                        "dpi_y": runtime.dpi_y,
                        "client_width": runtime.client_width,
                        "client_height": runtime.client_height,
                    }
                )
        return ResumeFingerprint(
            workflow_sha256=hashlib.sha256(dump_workflow(workflow)).hexdigest(),
            resolved_inputs_sha256=_hash_json(input_payload),
            output_root_sha256=_hash_json(str(output_root.resolve()).casefold()),
            data_sources=data_sources,
            adapters=adapters,
            environment_sha256=_hash_json(environment),
        )


@dataclass(frozen=True, slots=True)
class ResumeCompatibility:
    """Whether one stored checkpoint may be resumed, and why not when it may not.

    The three refusal codes are deliberately distinct: ``RESUME_UNSAFE`` is an
    interrupted non-idempotent iteration that a human must inspect,
    ``RESUME_MISMATCH`` is a changed workflow/input/data/adapter/environment or
    output, and ``CHECKPOINT_INVALID`` is unreadable state.
    """

    workflow_id: UUID
    run_id: UUID
    resumable: bool
    completed_cursor: tuple[LoopCursor, ...] = ()
    updated_at: datetime | None = None
    error_code: ErrorCode | None = None
    safe_message: str = ""
    mismatch_fields: tuple[str, ...] = ()


#: Public, stable field names reported for a fingerprint difference.
FINGERPRINT_FIELDS: tuple[tuple[str, str], ...] = (
    ("workflow", "workflow_sha256"),
    ("inputs", "resolved_inputs_sha256"),
    ("data", "data_sources"),
    ("adapter", "adapters"),
    ("environment", "environment_sha256"),
    ("output", "output_root_sha256"),
)


class ResumeValidator:
    def compare(self, stored: ResumeFingerprint, current: ResumeFingerprint) -> tuple[str, ...]:
        return tuple(
            name
            for name, attribute in FINGERPRINT_FIELDS
            if getattr(stored, attribute) != getattr(current, attribute)
        )

    def validate_fingerprint(self, stored: ResumeFingerprint, current: ResumeFingerprint) -> None:
        if stored != current:
            raise RpaError(
                ErrorCode.RESUME_MISMATCH,
                "업무 정의·실행 입력·데이터 또는 실행 환경이 바뀌어 재개할 수 없습니다.",
            )

    def validate_outputs(self, commits: tuple[OutputCommit, ...], output_root: Path) -> None:
        root = output_root.resolve()
        for commit in commits:
            destination = commit.destination.resolve()
            try:
                destination.relative_to(root)
            except ValueError:
                raise RpaError(
                    ErrorCode.RESUME_MISMATCH, "기존 산출물 위치가 출력 폴더 밖에 있습니다."
                ) from None
            if not commit.committed or not destination.is_file():
                raise RpaError(
                    ErrorCode.RESUME_MISMATCH, "재개에 필요한 기존 산출물을 찾을 수 없습니다."
                )
            digest = hashlib.sha256(destination.read_bytes()).hexdigest()
            if digest != commit.sha256:
                raise RpaError(
                    ErrorCode.RESUME_MISMATCH, "기존 산출물 내용이 실행 중 변경되었습니다."
                )


__all__ = [
    "FINGERPRINT_FIELDS",
    "ResumeCompatibility",
    "ResumeFingerprintBuilder",
    "ResumeValidator",
]
