from __future__ import annotations

from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from universal_rpa.domain.types import FrozenJsonObject, FrozenMapping, thaw_json
from universal_rpa.infrastructure.redaction import sanitize_evidence


class ErrorCode(StrEnum):
    INVALID_SCHEMA = "invalid_schema"
    UNSUPPORTED_SCHEMA = "unsupported_schema"
    ADAPTER_MISSING = "adapter_missing"
    ACTION_UNSUPPORTED = "action_unsupported"
    TARGET_NOT_FOUND = "target_not_found"
    TARGET_AMBIGUOUS = "target_ambiguous"
    ENVIRONMENT_MISMATCH = "environment_mismatch"
    FOREGROUND_MISMATCH = "foreground_mismatch"
    CONDITION_TIMEOUT = "condition_timeout"
    ASSERTION_FAILED = "assertion_failed"
    DATA_SOURCE_INVALID = "data_source_invalid"
    SECRET_MISSING = "secret_missing"
    OUTPUT_UNAVAILABLE = "output_unavailable"
    ACTION_FAILED = "action_failed"
    CHECKPOINT_INVALID = "checkpoint_invalid"
    RESUME_MISMATCH = "resume_mismatch"
    RESUME_UNSAFE = "resume_unsafe"
    CANCELLED = "cancelled"
    INTERNAL_ERROR = "internal_error"


class RpaError(Exception):
    """A typed exception that retains no unsafe underlying exception text."""

    def __init__(
        self,
        code: ErrorCode,
        safe_message: str,
        evidence: FrozenJsonObject | None = None,
    ) -> None:
        super().__init__(safe_message)
        self.code = code
        self.safe_message = safe_message
        self.evidence = (
            FrozenMapping.empty() if evidence is None else sanitize_evidence(thaw_json(evidence))
        )


class ValidationIssue(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    code: ErrorCode
    path: str
    safe_message: str
    severity: Literal["error", "warning"] = "error"
    step_id: UUID | None = None


class ValidationReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    issues: tuple[ValidationIssue, ...] = ()

    @property
    def errors(self) -> tuple[ValidationIssue, ...]:
        return tuple(issue for issue in self.issues if issue.severity == "error")

    @property
    def warnings(self) -> tuple[ValidationIssue, ...]:
        return tuple(issue for issue in self.issues if issue.severity == "warning")

    @property
    def is_valid(self) -> bool:
        return not self.errors


__all__ = [
    "ErrorCode",
    "RpaError",
    "ValidationIssue",
    "ValidationReport",
]
