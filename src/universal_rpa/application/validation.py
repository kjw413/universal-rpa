from __future__ import annotations

import os
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from math import isfinite
from pathlib import Path
from uuid import UUID, uuid4

from universal_rpa.adapters.registry import AdapterRegistry
from universal_rpa.domain.action_parameters import (
    BUILTIN_ACTION_PARAMETER_MODELS,
    validate_builtin_action_parameters,
)
from universal_rpa.domain.conditions import (
    AssertionSpec,
    ConditionSpec,
    TableAssertionSpec,
    WaitSpec,
)
from universal_rpa.domain.errors import ErrorCode, RpaError, ValidationIssue, ValidationReport
from universal_rpa.domain.targets import RuntimeEnvironment, TargetSpec
from universal_rpa.domain.types import DataCell, FrozenMapping
from universal_rpa.domain.values import (
    CredentialSource,
    DataColumnSource,
    RowBindingValue,
    RunInputSource,
    SecretRefValue,
)
from universal_rpa.domain.workflow import (
    ActionStep,
    LoopStep,
    OutputRelativePath,
    Step,
    TargetAppSpec,
    Workflow,
)
from universal_rpa.ports.automation import AutomationAdapter, TargetValidationMode
from universal_rpa.ports.credentials import SecretStorePort
from universal_rpa.ports.data_sources import DataPreview, DataSourcePort


def _freeze_values(value: Mapping[str, DataCell]) -> FrozenMapping[str, DataCell]:
    items: list[tuple[str, DataCell]] = []
    for key, item in value.items():
        if not isinstance(key, str) or not key:
            raise ValueError("variable value keys must be nonblank strings")
        if item is not None and not isinstance(item, (bool, int, float, str)):
            raise ValueError("variable values must be scalar")
        if isinstance(item, float) and not isfinite(item):
            raise ValueError("variable values must be finite")
        items.append((key, item))
    return FrozenMapping(tuple(items))


@dataclass(frozen=True, slots=True, init=False)
class ValidationContext:
    project_dir: Path
    runtime: RuntimeEnvironment | None
    variable_values: FrozenMapping[str, DataCell]
    output_root: Path

    def __init__(
        self,
        project_dir: Path,
        runtime: RuntimeEnvironment | None,
        variable_values: Mapping[str, DataCell],
        output_root: Path,
    ) -> None:
        object.__setattr__(self, "project_dir", Path(project_dir).absolute())
        object.__setattr__(self, "runtime", runtime)
        object.__setattr__(self, "variable_values", _freeze_values(dict(variable_values)))
        object.__setattr__(self, "output_root", Path(output_root).absolute())


class ValidationService:
    """Fail-closed validation that never invokes an adapter action."""

    def __init__(
        self,
        registry: AdapterRegistry | None = None,
        data_sources: DataSourcePort | None = None,
        secret_store: SecretStorePort | None = None,
    ) -> None:
        self._registry = registry or AdapterRegistry()
        self._data_sources = data_sources
        self._secret_store = secret_store

    def validate_static(self, workflow: Workflow) -> ValidationReport:
        issues: list[ValidationIssue] = []
        self._validate_steps_static(workflow.steps, "steps", issues, loop_depth=0)
        return self._report(issues)

    def validate_environment(
        self,
        workflow: Workflow,
        context: ValidationContext,
    ) -> ValidationReport:
        issues = list(self.validate_static(workflow).issues)
        runtime = context.runtime
        if runtime is None:
            issues.append(
                self._issue(
                    ErrorCode.ENVIRONMENT_MISMATCH,
                    "runtime",
                    "현재 Windows 실행 환경을 확인할 수 없습니다.",
                )
            )
        else:
            if not runtime.interactive_desktop:
                issues.append(
                    self._issue(
                        ErrorCode.ENVIRONMENT_MISMATCH,
                        "runtime.interactive_desktop",
                        "대화형 Windows 데스크톱이 필요합니다.",
                    )
                )
            if not any(self._runtime_matches_app(runtime, app) for app in workflow.target_apps):
                issues.append(
                    self._issue(
                        ErrorCode.ENVIRONMENT_MISMATCH,
                        "target_apps",
                        "현재 창이 프로젝트 대상 프로그램과 일치하지 않습니다.",
                    )
                )
            self._validate_steps_environment(
                workflow.steps,
                "steps",
                runtime,
                issues,
                optional_parent=False,
            )

        previews = self._validate_data_sources(workflow, context, issues)
        self._validate_variables(workflow, context, previews, issues)
        self._validate_secrets(workflow, issues)
        self._validate_outputs(workflow, context, issues)
        return self._report(issues)

    def _validate_steps_static(
        self,
        steps: Sequence[Step],
        path: str,
        issues: list[ValidationIssue],
        *,
        loop_depth: int,
    ) -> None:
        for index, step in enumerate(steps):
            step_path = f"{path}[{index}]"
            if isinstance(step, ActionStep):
                self._validate_action_static(step, step_path, issues, loop_depth=loop_depth)
                continue
            if isinstance(step, LoopStep):
                self._validate_steps_static(
                    step.steps,
                    f"{step_path}.steps",
                    issues,
                    loop_depth=loop_depth + 1,
                )
                continue
            condition = ConditionSpec(
                condition_type=step.condition.condition_type,
                target=step.condition.target,
                expected=True,
            )
            self._validate_condition_static(
                condition,
                f"{step_path}.condition",
                step.step_id,
                issues,
            )
            self._validate_steps_static(
                step.steps,
                f"{step_path}.steps",
                issues,
                loop_depth=loop_depth,
            )

    def _validate_action_static(
        self,
        step: ActionStep,
        path: str,
        issues: list[ValidationIssue],
        *,
        loop_depth: int,
    ) -> None:
        adapter = self._namespace_adapter(
            step.action_type, f"{path}.action_type", step.step_id, issues
        )
        if adapter is not None:
            descriptor = adapter.descriptor()
            if step.action_type not in descriptor.actions:
                issues.append(
                    self._issue(
                        ErrorCode.ACTION_UNSUPPORTED,
                        f"{path}.action_type",
                        "설치된 어댑터가 이 작업을 지원하지 않습니다.",
                        step.step_id,
                    )
                )
            else:
                self._validate_builtin_parameters(step, path, issues)
                self._call_static_validator(
                    lambda: adapter.validate_action_spec(step),
                    path,
                    step.step_id,
                    issues,
                )
                mode = descriptor.verification_by_action[step.action_type]
                if mode == "postcondition_or_assertion" and not (
                    step.postcondition is not None or step.assertions
                ):
                    issues.append(
                        self._issue(
                            ErrorCode.INVALID_SCHEMA,
                            f"{path}.postcondition",
                            "이 작업에는 완료 조건 또는 결과 검증이 필요합니다.",
                            step.step_id,
                        )
                    )
                if (
                    step.failure_policy.mode == "retry"
                    and step.action_type not in descriptor.idempotent_actions
                ):
                    issues.append(
                        self._issue(
                            ErrorCode.INVALID_SCHEMA,
                            f"{path}.failure_policy",
                            "이 작업은 어댑터가 안전한 재시도를 허용하지 않습니다.",
                            step.step_id,
                        )
                    )
                self._validate_assertions(step, path, adapter, issues)

        if isinstance(step.value, RowBindingValue) and loop_depth == 0:
            issues.append(
                self._issue(
                    ErrorCode.INVALID_SCHEMA,
                    f"{path}.value",
                    "행 데이터 값은 반복 단계 안에서만 사용할 수 있습니다.",
                    step.step_id,
                )
            )
        if step.target is not None:
            self._target_adapter(step.target, f"{path}.target", step.step_id, issues)
        for name, wait in (
            ("precondition", step.precondition),
            ("postcondition", step.postcondition),
            ("wait", step.wait),
        ):
            if wait is not None:
                self._validate_wait_static(wait, f"{path}.{name}", step.step_id, issues)

    def _validate_builtin_parameters(
        self,
        step: ActionStep,
        path: str,
        issues: list[ValidationIssue],
    ) -> None:
        if step.action_type not in BUILTIN_ACTION_PARAMETER_MODELS:
            return
        try:
            canonical = validate_builtin_action_parameters(step.action_type, step.parameters)
        except (TypeError, ValueError):
            issues.append(
                self._issue(
                    ErrorCode.INVALID_SCHEMA,
                    f"{path}.parameters",
                    "Windows 작업 매개변수가 올바르지 않습니다.",
                    step.step_id,
                )
            )
            return
        if canonical != step.parameters:
            issues.append(
                self._issue(
                    ErrorCode.INVALID_SCHEMA,
                    f"{path}.parameters",
                    "Windows 작업 매개변수가 표준 형식이 아닙니다.",
                    step.step_id,
                )
            )

    def _validate_assertions(
        self,
        step: ActionStep,
        path: str,
        adapter: AutomationAdapter,
        issues: list[ValidationIssue],
    ) -> None:
        descriptor = adapter.descriptor()
        compatible = descriptor.assertions_by_action.get(step.action_type, frozenset())
        subject_kind = self._action_subject_kind(step.action_type)
        for index, assertion in enumerate(step.assertions):
            assertion_path = f"{path}.assertions[{index}]"
            if assertion.assertion_type not in compatible:
                issues.append(
                    self._issue(
                        ErrorCode.INVALID_SCHEMA,
                        f"{assertion_path}.assertion_type",
                        "이 결과 검증은 작업 결과 형식과 호환되지 않습니다.",
                        step.step_id,
                    )
                )
                continue
            if descriptor.assertion_input_kind[assertion.assertion_type] != subject_kind:
                issues.append(
                    self._issue(
                        ErrorCode.INVALID_SCHEMA,
                        assertion_path,
                        "결과 검증이 요구하는 입력 형식과 작업 결과가 다릅니다.",
                        step.step_id,
                    )
                )
                continue

            def validate_assertion(
                current: AssertionSpec | TableAssertionSpec = assertion,
            ) -> tuple[ValidationIssue, ...]:
                return adapter.validate_assertion_spec(current)

            self._call_static_validator(
                validate_assertion,
                assertion_path,
                step.step_id,
                issues,
            )

    def _validate_wait_static(
        self,
        wait: WaitSpec,
        path: str,
        step_id: UUID,
        issues: list[ValidationIssue],
    ) -> None:
        self._validate_condition_static(wait.condition, f"{path}.condition", step_id, issues)

    def _validate_condition_static(
        self,
        condition: ConditionSpec,
        path: str,
        step_id: UUID,
        issues: list[ValidationIssue],
    ) -> None:
        adapter = self._namespace_adapter(
            condition.condition_type,
            f"{path}.condition_type",
            step_id,
            issues,
        )
        if adapter is not None:
            if condition.condition_type not in adapter.descriptor().conditions:
                issues.append(
                    self._issue(
                        ErrorCode.ACTION_UNSUPPORTED,
                        f"{path}.condition_type",
                        "설치된 어댑터가 이 조건을 지원하지 않습니다.",
                        step_id,
                    )
                )
            else:
                self._call_static_validator(
                    lambda: adapter.validate_condition_spec(condition),
                    path,
                    step_id,
                    issues,
                )
        if condition.target is not None:
            self._target_adapter(condition.target, f"{path}.target", step_id, issues)

    def _validate_steps_environment(
        self,
        steps: Sequence[Step],
        path: str,
        runtime: RuntimeEnvironment,
        issues: list[ValidationIssue],
        *,
        optional_parent: bool,
    ) -> None:
        for index, step in enumerate(steps):
            if not step.enabled:
                continue
            step_path = f"{path}[{index}]"
            if isinstance(step, ActionStep):
                action_mode: TargetValidationMode = (
                    "deferred" if optional_parent else "must_exist_now"
                )
                if step.target is not None:
                    self._validate_target_environment(
                        step.target,
                        f"{step_path}.target",
                        step.step_id,
                        runtime,
                        action_mode,
                        issues,
                    )
                for name, wait in (
                    ("precondition", step.precondition),
                    ("postcondition", step.postcondition),
                    ("wait", step.wait),
                ):
                    if wait is not None and wait.condition.target is not None:
                        self._validate_target_environment(
                            wait.condition.target,
                            f"{step_path}.{name}.condition.target",
                            step.step_id,
                            runtime,
                            "deferred",
                            issues,
                        )
                continue
            if isinstance(step, LoopStep):
                self._validate_steps_environment(
                    step.steps,
                    f"{step_path}.steps",
                    runtime,
                    issues,
                    optional_parent=optional_parent,
                )
                continue
            self._validate_target_environment(
                step.condition.target,
                f"{step_path}.condition.target",
                step.step_id,
                runtime,
                "may_be_absent_now",
                issues,
            )
            self._validate_steps_environment(
                step.steps,
                f"{step_path}.steps",
                runtime,
                issues,
                optional_parent=True,
            )

    def _validate_target_environment(
        self,
        target: TargetSpec,
        path: str,
        step_id: UUID,
        runtime: RuntimeEnvironment,
        mode: TargetValidationMode,
        issues: list[ValidationIssue],
    ) -> None:
        adapter = self._target_adapter(target, path, step_id, issues)
        if adapter is None:
            return
        try:
            found = adapter.validate_target(target, runtime, mode)
            self._extend_adapter_issues(found, path, step_id, issues)
        except RpaError as error:
            issues.append(self._issue(error.code, path, error.safe_message, step_id))
        except Exception:
            issues.append(
                self._issue(
                    ErrorCode.INTERNAL_ERROR,
                    path,
                    "대상 환경을 확인하는 중 오류가 발생했습니다.",
                    step_id,
                )
            )

    def _validate_data_sources(
        self,
        workflow: Workflow,
        context: ValidationContext,
        issues: list[ValidationIssue],
    ) -> dict[str, DataPreview]:
        previews: dict[str, DataPreview] = {}
        if not workflow.data_sources:
            return previews
        if self._data_sources is None:
            issues.append(
                self._issue(
                    ErrorCode.DATA_SOURCE_INVALID,
                    "data_sources",
                    "데이터 파일을 확인할 수 있는 서비스가 없습니다.",
                )
            )
            return previews
        for index, source in enumerate(workflow.data_sources):
            try:
                previews[source.data_source_id] = self._data_sources.preview(
                    context.project_dir,
                    source,
                    max_rows=20,
                )
            except RpaError as error:
                issues.append(
                    self._issue(
                        error.code,
                        f"data_sources[{index}]",
                        error.safe_message,
                    )
                )
            except Exception:
                issues.append(
                    self._issue(
                        ErrorCode.DATA_SOURCE_INVALID,
                        f"data_sources[{index}]",
                        "데이터 소스를 읽거나 미리 볼 수 없습니다.",
                    )
                )
        return previews

    def _validate_variables(
        self,
        workflow: Workflow,
        context: ValidationContext,
        previews: Mapping[str, DataPreview],
        issues: list[ValidationIssue],
    ) -> None:
        for index, variable in enumerate(workflow.variables):
            path = f"variables[{index}].source"
            source = variable.source
            if (
                isinstance(source, RunInputSource)
                and source.required
                and variable.variable_id not in context.variable_values
            ):
                issues.append(
                    self._issue(
                        ErrorCode.INVALID_SCHEMA,
                        path,
                        "필수 실행 변수를 입력하세요.",
                    )
                )
            if isinstance(source, DataColumnSource):
                preview = previews.get(source.data_source_id)
                if preview is not None and source.column_name not in preview.headers:
                    issues.append(
                        self._issue(
                            ErrorCode.DATA_SOURCE_INVALID,
                            path,
                            "데이터 소스에 지정한 열이 없습니다.",
                        )
                    )

    def _validate_secrets(
        self,
        workflow: Workflow,
        issues: list[ValidationIssue],
    ) -> None:
        references: list[tuple[str, str, UUID | None]] = []
        for index, variable in enumerate(workflow.variables):
            if isinstance(variable.source, CredentialSource):
                references.append(
                    (
                        variable.source.credential_ref,
                        f"variables[{index}].source.credential_ref",
                        None,
                    )
                )
        self._collect_step_secrets(workflow.steps, "steps", references)
        for reference, path, step_id in references:
            if self._secret_store is None:
                issues.append(
                    self._issue(
                        ErrorCode.SECRET_MISSING,
                        path,
                        "자격 증명 저장소를 사용할 수 없습니다.",
                        step_id,
                    )
                )
                continue
            try:
                exists = self._secret_store.exists(reference)
            except Exception:
                exists = False
            if not exists:
                issues.append(
                    self._issue(
                        ErrorCode.SECRET_MISSING,
                        path,
                        "선택한 자격 증명을 찾을 수 없습니다.",
                        step_id,
                    )
                )

    def _collect_step_secrets(
        self,
        steps: Sequence[Step],
        path: str,
        references: list[tuple[str, str, UUID | None]],
    ) -> None:
        for index, step in enumerate(steps):
            step_path = f"{path}[{index}]"
            if isinstance(step, ActionStep):
                if isinstance(step.value, SecretRefValue):
                    references.append(
                        (step.value.credential_ref, f"{step_path}.value", step.step_id)
                    )
            else:
                self._collect_step_secrets(step.steps, f"{step_path}.steps", references)

    def _validate_outputs(
        self,
        workflow: Workflow,
        context: ValidationContext,
        issues: list[ValidationIssue],
    ) -> None:
        root = context.output_root
        if not root.is_dir() or self._is_link_like(root) or not os.access(root, os.W_OK):
            issues.append(
                self._issue(
                    ErrorCode.OUTPUT_UNAVAILABLE,
                    "output_root",
                    "출력 폴더를 사용할 수 없습니다.",
                )
            )
            return
        probe = root / f".universal-rpa-write-{uuid4().hex}.tmp"
        try:
            with probe.open("xb") as stream:
                stream.write(b"")
                stream.flush()
                os.fsync(stream.fileno())
        except OSError:
            issues.append(
                self._issue(
                    ErrorCode.OUTPUT_UNAVAILABLE,
                    "output_root",
                    "출력 폴더에 파일을 만들 수 없습니다.",
                )
            )
            return
        finally:
            try:
                probe.unlink(missing_ok=True)
            except OSError:
                pass

        for path, value, step_id in self._output_values(workflow.steps, "steps"):
            try:
                relative = OutputRelativePath(value)
                resolved = relative.resolve_under(root)
            except (OSError, ValueError):
                issues.append(
                    self._issue(
                        ErrorCode.OUTPUT_UNAVAILABLE,
                        path,
                        "출력 경로가 출력 폴더의 안전한 상대 경로가 아닙니다.",
                        step_id,
                    )
                )
                continue
            if resolved.exists():
                try:
                    descriptor = os.open(resolved, os.O_WRONLY | os.O_APPEND)
                    os.close(descriptor)
                except OSError:
                    issues.append(
                        self._issue(
                            ErrorCode.OUTPUT_UNAVAILABLE,
                            path,
                            "출력 파일이 잠겨 있거나 쓸 수 없습니다.",
                            step_id,
                        )
                    )

    def _output_values(
        self,
        steps: Sequence[Step],
        path: str,
    ) -> tuple[tuple[str, str, UUID], ...]:
        found: list[tuple[str, str, UUID]] = []
        for index, step in enumerate(steps):
            step_path = f"{path}[{index}]"
            if isinstance(step, ActionStep):
                if step.action_type == "tabular.save_table":
                    for key in ("output_path", "path"):
                        value = step.parameters.get(key)
                        if isinstance(value, str):
                            found.append((f"{step_path}.parameters.{key}", value, step.step_id))
            else:
                found.extend(self._output_values(step.steps, f"{step_path}.steps"))
        return tuple(found)

    def _namespace_adapter(
        self,
        namespaced_type: str,
        path: str,
        step_id: UUID,
        issues: list[ValidationIssue],
    ) -> AutomationAdapter | None:
        adapter_id, separator, _ = namespaced_type.partition(".")
        if not separator:
            issues.append(
                self._issue(
                    ErrorCode.INVALID_SCHEMA,
                    path,
                    "자동화 유형의 이름 공간이 올바르지 않습니다.",
                    step_id,
                )
            )
            return None
        try:
            return self._registry.require(adapter_id)
        except RpaError as error:
            issues.append(self._issue(error.code, path, error.safe_message, step_id))
            return None

    def _target_adapter(
        self,
        target: TargetSpec,
        path: str,
        step_id: UUID,
        issues: list[ValidationIssue],
    ) -> AutomationAdapter | None:
        try:
            return self._registry.require(target.adapter_id)
        except RpaError as error:
            issues.append(self._issue(error.code, path, error.safe_message, step_id))
            return None

    def _call_static_validator(
        self,
        operation: Callable[[], Sequence[ValidationIssue]],
        path: str,
        step_id: UUID,
        issues: list[ValidationIssue],
    ) -> None:
        try:
            found = operation()
            self._extend_adapter_issues(found, path, step_id, issues)
        except RpaError as error:
            issues.append(self._issue(error.code, path, error.safe_message, step_id))
        except Exception:
            issues.append(
                self._issue(
                    ErrorCode.INTERNAL_ERROR,
                    path,
                    "자동화 사양을 확인하는 중 오류가 발생했습니다.",
                    step_id,
                )
            )

    @staticmethod
    def _extend_adapter_issues(
        found: Sequence[ValidationIssue],
        path: str,
        step_id: UUID,
        issues: list[ValidationIssue],
    ) -> None:
        for issue in found:
            suffix = issue.path.strip(".")
            issues.append(
                issue.model_copy(
                    update={
                        "path": f"{path}.{suffix}" if suffix else path,
                        "step_id": issue.step_id or step_id,
                    }
                )
            )

    @staticmethod
    def _action_subject_kind(action_type: str) -> str:
        if action_type == "clipboard.extract_table":
            return "table"
        if action_type == "tabular.save_table":
            return "output_commit"
        return "json"

    @staticmethod
    def _runtime_matches_app(runtime: RuntimeEnvironment, app: TargetAppSpec) -> bool:
        executable = app.process_executable
        window_class = app.window_class
        window_title = app.window_title
        same_process = (
            Path(runtime.process_executable).name.casefold() == Path(executable).name.casefold()
        )
        same_class = runtime.window_class == window_class
        title_matches = window_title is None or runtime.window_title == window_title
        return same_process and same_class and title_matches

    @staticmethod
    def _is_link_like(path: Path) -> bool:
        if path.is_symlink():
            return True
        is_junction = getattr(path, "is_junction", None)
        return bool(is_junction is not None and is_junction())

    @staticmethod
    def _issue(
        code: ErrorCode,
        path: str,
        message: str,
        step_id: UUID | None = None,
    ) -> ValidationIssue:
        return ValidationIssue(code=code, path=path, safe_message=message, step_id=step_id)

    @staticmethod
    def _report(issues: Sequence[ValidationIssue]) -> ValidationReport:
        ordered = tuple(
            sorted(
                issues,
                key=lambda issue: (
                    issue.path,
                    issue.code.value,
                    issue.safe_message,
                    str(issue.step_id or ""),
                ),
            )
        )
        return ValidationReport(issues=ordered)


__all__ = ["ValidationContext", "ValidationService"]
