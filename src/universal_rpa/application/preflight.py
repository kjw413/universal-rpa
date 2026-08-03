"""Fail-closed execution readiness validation.

This service deliberately owns no native input capability.  Its only outcome is
the canonical M3 ``ValidationReport`` that must be valid before a runner is
allowed to resolve a target or call an adapter action.
"""

from __future__ import annotations

from collections.abc import Callable

from universal_rpa.application.validation import ValidationContext, ValidationService
from universal_rpa.domain.errors import ValidationReport
from universal_rpa.domain.execution import RunRequest
from universal_rpa.domain.targets import RuntimeEnvironment

RuntimeProvider = Callable[[RunRequest], RuntimeEnvironment | None]


class PreflightService:
    def __init__(
        self,
        validation_service: ValidationService,
        runtime_provider: RuntimeProvider,
    ) -> None:
        self._validation_service = validation_service
        self._runtime_provider = runtime_provider

    def check(self, request: RunRequest) -> ValidationReport:
        """Validate without performing any target resolution or native input."""

        return self.inspect(request)[0]

    def inspect(self, request: RunRequest) -> tuple[ValidationReport, RuntimeEnvironment | None]:
        """Return validation and the exact environment snapshot used by it."""

        static = self._validation_service.validate_static(request.workflow)
        if not static.is_valid:
            return static, None
        runtime = self._runtime_provider(request)
        context = ValidationContext(
            project_dir=request.project_dir,
            runtime=runtime,
            variable_values=request.inputs.variable_values,
            output_root=request.inputs.output_directory,
        )
        return self._validation_service.validate_environment(request.workflow, context), runtime


__all__ = ["PreflightService", "RuntimeProvider"]
