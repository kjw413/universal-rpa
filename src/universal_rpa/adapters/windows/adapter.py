"""Windows UIA-first automation adapter with guarded native input."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, cast

from universal_rpa.domain.action_parameters import validate_builtin_action_parameters
from universal_rpa.domain.conditions import AssertionSpec, ConditionSpec, TableAssertionSpec
from universal_rpa.domain.errors import ErrorCode, RpaError, ValidationIssue
from universal_rpa.domain.targets import RuntimeEnvironment, TargetSpec
from universal_rpa.domain.types import FrozenMapping
from universal_rpa.domain.workflow import ActionStep
from universal_rpa.ports.automation import (
    ActionRequest,
    AdapterActionResult,
    AdapterDescriptor,
    AssertionObservation,
    CancellationToken,
    ConditionObservation,
    ExecutionContext,
    TargetCapturePort,
    TargetCaptureRequest,
    TargetCaptureResult,
    TargetValidationMode,
)
from universal_rpa.ports.credentials import SecretValue

from .environment import WindowsEnvironmentProbe
from .input_driver import WindowsInputDriver
from .target_resolver import ResolvedUiaTarget, WindowsTargetResolver
from .text_input import TextInputStrategy

WINDOWS_ADAPTER_VERSION = "1.0.0"


def _issue(code: ErrorCode, path: str, message: str) -> tuple[ValidationIssue, ...]:
    return (ValidationIssue(code=code, path=path, safe_message=message),)


class WindowsAutomationAdapter:
    """Concrete adapter for the bounded Windows action set declared in M1."""

    def __init__(
        self,
        resolver: WindowsTargetResolver,
        driver: WindowsInputDriver,
        probe: WindowsEnvironmentProbe,
        *,
        target_capture: TargetCapturePort | None = None,
        text_input: TextInputStrategy | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._resolver = resolver
        self._driver = driver
        self._probe = probe
        self._target_capture = target_capture
        self._text_input = text_input or TextInputStrategy(driver)
        self._clock = clock

    @property
    def adapter_id(self) -> str:
        return "windows"

    def descriptor(self) -> AdapterDescriptor:
        actions = frozenset(
            {
                "windows.activate_window",
                "windows.click",
                "windows.double_click",
                "windows.drag",
                "windows.scroll",
                "windows.set_text",
                "windows.press_key",
                "windows.hotkey",
                "windows.wait",
            }
        )
        assertions = frozenset({"windows.value_equals", "windows.value_contains"})
        return AdapterDescriptor(
            adapter_id=self.adapter_id,
            implementation_version=WINDOWS_ADAPTER_VERSION,
            supports_target_capture=True,
            actions=actions,
            conditions=frozenset(
                {
                    "windows.element_exists",
                    "windows.element_visible",
                    "windows.element_enabled",
                    "windows.window_exists",
                    "windows.value_equals",
                    "windows.value_contains",
                    "windows.fixed_delay",
                }
            ),
            assertions=assertions,
            verification_by_action=FrozenMapping(
                tuple(
                    (
                        action,
                        "intrinsic" if action == "windows.wait" else "postcondition_or_assertion",
                    )
                    for action in sorted(actions)
                )
            ),
            idempotent_actions=frozenset({"windows.activate_window", "windows.set_text"}),
            retryable_errors_by_action=FrozenMapping(
                (("windows.set_text", frozenset({ErrorCode.ACTION_FAILED})),)
            ),
            assertions_by_action=FrozenMapping(
                tuple(
                    (action, assertions) for action in sorted(actions) if action != "windows.wait"
                )
            ),
            assertion_input_kind=FrozenMapping(
                (("windows.value_equals", "json"), ("windows.value_contains", "json"))
            ),
        )

    def capture_target(
        self,
        request: TargetCaptureRequest,
        cancellation: CancellationToken,
    ) -> TargetCaptureResult:
        if self._target_capture is not None:
            return self._target_capture.capture_target(request, cancellation)
        return TargetCaptureResult(
            target=None,
            candidates=(),
            preview_png=None,
            issues=_issue(
                ErrorCode.ACTION_UNSUPPORTED, "target", "대상 캡처 구성 요소를 사용할 수 없습니다."
            ),
        )

    def validate_action_spec(self, step: ActionStep) -> tuple[ValidationIssue, ...]:
        if step.action_type not in self.descriptor().actions:
            return _issue(
                ErrorCode.ACTION_UNSUPPORTED, "action_type", "지원하지 않는 Windows 작업입니다."
            )
        try:
            validate_builtin_action_parameters(step.action_type, step.parameters)
        except (TypeError, ValueError):
            return _issue(
                ErrorCode.INVALID_SCHEMA, "parameters", "Windows 작업 매개변수가 올바르지 않습니다."
            )
        if step.action_type != "windows.wait" and step.target is None:
            return _issue(
                ErrorCode.INVALID_SCHEMA, "target", "이 작업에는 대상 창 또는 UI 요소가 필요합니다."
            )
        if step.target is not None and step.target.adapter_id != self.adapter_id:
            return _issue(
                ErrorCode.INVALID_SCHEMA,
                "target.adapter_id",
                "대상 어댑터가 작업과 일치하지 않습니다.",
            )
        return ()

    def validate_condition_spec(self, condition: ConditionSpec) -> tuple[ValidationIssue, ...]:
        if condition.condition_type not in self.descriptor().conditions:
            return _issue(
                ErrorCode.ACTION_UNSUPPORTED, "condition_type", "지원하지 않는 Windows 조건입니다."
            )
        if condition.condition_type != "windows.fixed_delay" and condition.target is None:
            return _issue(ErrorCode.INVALID_SCHEMA, "target", "이 조건에는 대상 정보가 필요합니다.")
        return ()

    def validate_assertion_spec(
        self, assertion: AssertionSpec | TableAssertionSpec
    ) -> tuple[ValidationIssue, ...]:
        if not isinstance(assertion, AssertionSpec):
            return _issue(
                ErrorCode.INVALID_SCHEMA, "assertion", "Windows 값 검증 형식이 올바르지 않습니다."
            )
        if assertion.assertion_type not in self.descriptor().assertions:
            return _issue(
                ErrorCode.ACTION_UNSUPPORTED, "assertion_type", "지원하지 않는 Windows 검증입니다."
            )
        return ()

    def validate_target(
        self,
        target: TargetSpec,
        runtime: RuntimeEnvironment,
        mode: TargetValidationMode,
    ) -> tuple[ValidationIssue, ...]:
        del runtime, mode
        if target.adapter_id != self.adapter_id:
            return _issue(
                ErrorCode.INVALID_SCHEMA, "adapter_id", "대상 어댑터가 일치하지 않습니다."
            )
        try:
            from universal_rpa.domain.targets import WindowsTarget

            WindowsTarget.model_validate(target.payload)
        except Exception:
            return _issue(
                ErrorCode.INVALID_SCHEMA, "payload", "Windows 대상 정보가 올바르지 않습니다."
            )
        return ()

    def _foreground_runtime(self) -> RuntimeEnvironment:
        # A global input action is legal only in the currently observed interactive
        # foreground window. Resolver/driver independently repeat their guards.
        self._probe.require_interactive_desktop()
        hwnd = self._probe.foreground_hwnd()
        return self._probe.snapshot(hwnd)

    def execute(
        self,
        request: ActionRequest,
        context: ExecutionContext,
        cancellation: CancellationToken,
    ) -> AdapterActionResult:
        observed_runtime: RuntimeEnvironment | None = None
        try:
            cancellation.raise_if_cancelled()
            if request.action_type == "windows.wait":
                return AdapterActionResult(output=None, evidence=FrozenMapping.empty())
            if request.target is None:
                raise RpaError(ErrorCode.INVALID_SCHEMA, "Windows 작업에 대상 정보가 없습니다.")
            resolved = self._resolver.resolve(
                request.target,
                self._foreground_runtime(),
                has_postcondition_or_assertion=request.has_postcondition_or_assertion,
            )
            observed_runtime = self._probe.snapshot(resolved.window.top_level_hwnd)
            cancellation.raise_if_cancelled()
            parameters = request.parameters
            if request.action_type == "windows.activate_window":
                self._driver.activate(resolved)
            elif request.action_type == "windows.click":
                self._driver.click(resolved, str(parameters.get("button", "left")))
            elif request.action_type == "windows.double_click":
                self._driver.click(resolved, str(parameters.get("button", "left")), double=True)
            elif request.action_type == "windows.drag":
                point = parameters.get("end_point")
                if not isinstance(point, FrozenMapping):
                    raise RpaError(
                        ErrorCode.INVALID_SCHEMA, "드래그 종료 좌표가 올바르지 않습니다."
                    )
                runtime = self._foreground_runtime()
                end = (
                    round(self._number(point.get("x"), "end_point.x") * (runtime.client_width - 1)),
                    round(
                        self._number(point.get("y"), "end_point.y") * (runtime.client_height - 1)
                    ),
                )
                geometry = self._resolver._win32.client_geometry(runtime.top_level_hwnd)
                self._driver.drag(
                    resolved,
                    (geometry.left + end[0], geometry.top + end[1]),
                    str(parameters.get("button", "left")),
                )
            elif request.action_type == "windows.scroll":
                self._driver.scroll(
                    resolved,
                    round(self._number(parameters.get("horizontal_delta"), "horizontal_delta")),
                    round(self._number(parameters.get("vertical_delta"), "vertical_delta")),
                )
            elif request.action_type == "windows.set_text":
                if not isinstance(request.value, (str, SecretValue)):
                    raise RpaError(ErrorCode.INVALID_SCHEMA, "텍스트 입력값이 없습니다.")
                result = self._text_input.set_text(resolved, request.value, verify=True)
                return AdapterActionResult(
                    output=None, evidence=result.evidence, runtime=observed_runtime
                )
            elif request.action_type == "windows.press_key":
                self._driver.press_key(resolved, str(parameters["key"]))
            elif request.action_type == "windows.hotkey":
                modifiers_value = parameters.get("modifiers", ())
                if not isinstance(modifiers_value, tuple):
                    raise RpaError(ErrorCode.INVALID_SCHEMA, "보조 키 목록이 올바르지 않습니다.")
                modifiers = tuple(str(item) for item in modifiers_value)
                self._driver.press_key(resolved, str(parameters["key"]), modifiers=modifiers)
            else:
                raise RpaError(ErrorCode.ACTION_UNSUPPORTED, "지원하지 않는 Windows 작업입니다.")
            return AdapterActionResult(
                output=None, evidence=FrozenMapping.empty(), runtime=observed_runtime
            )
        except RpaError as error:
            return AdapterActionResult(
                output=None,
                evidence=error.evidence,
                error_code=error.code,
                safe_message=error.safe_message,
                runtime=observed_runtime,
            )
        except Exception:
            return AdapterActionResult(
                output=None,
                evidence=FrozenMapping.empty(),
                error_code=ErrorCode.INTERNAL_ERROR,
                safe_message="Windows 작업을 완료하지 못했습니다.",
                runtime=observed_runtime,
            )

    def evaluate_condition(
        self,
        condition: ConditionSpec,
        context: ExecutionContext,
        cancellation: CancellationToken,
    ) -> ConditionObservation:
        del context
        cancellation.raise_if_cancelled()
        if condition.condition_type == "windows.fixed_delay":
            return ConditionObservation(
                satisfied=True, observed=None, evidence=FrozenMapping.empty()
            )
        if condition.target is None:
            raise RpaError(ErrorCode.INVALID_SCHEMA, "조건 대상 정보가 없습니다.")
        try:
            resolved = self._resolver.resolve(
                condition.target, self._foreground_runtime(), has_postcondition_or_assertion=True
            )
        except RpaError as error:
            if error.code is ErrorCode.TARGET_NOT_FOUND and condition.condition_type in {
                "windows.element_exists",
                "windows.window_exists",
            }:
                return ConditionObservation(False, None, FrozenMapping.empty())
            raise
        element = cast(Any, resolved.element) if isinstance(resolved, ResolvedUiaTarget) else None
        if (
            condition.condition_type == "windows.element_exists"
            or condition.condition_type == "windows.window_exists"
        ):
            return ConditionObservation(True, True, FrozenMapping.empty())
        if element is None:
            return ConditionObservation(False, None, FrozenMapping.empty())
        if condition.condition_type == "windows.element_visible":
            return ConditionObservation(bool(element.is_visible()), None, FrozenMapping.empty())
        if condition.condition_type == "windows.element_enabled":
            return ConditionObservation(bool(element.is_enabled()), None, FrozenMapping.empty())
        actual = self._element_value(element)
        expected = condition.expected
        if condition.condition_type == "windows.value_equals":
            return ConditionObservation(actual == expected, actual, FrozenMapping.empty())
        if condition.condition_type == "windows.value_contains":
            return ConditionObservation(str(expected) in str(actual), actual, FrozenMapping.empty())
        raise RpaError(ErrorCode.ACTION_UNSUPPORTED, "지원하지 않는 Windows 조건입니다.")

    def evaluate_assertion(
        self,
        assertion: AssertionSpec | TableAssertionSpec,
        subject: object,
        target: TargetSpec | None,
        context: ExecutionContext,
        cancellation: CancellationToken,
    ) -> AssertionObservation:
        del subject, context
        cancellation.raise_if_cancelled()
        if target is None:
            raise RpaError(ErrorCode.INVALID_SCHEMA, "값 검증에는 대상 정보가 필요합니다.")
        resolved = self._resolver.resolve(
            target, self._foreground_runtime(), has_postcondition_or_assertion=True
        )
        if not isinstance(resolved, ResolvedUiaTarget):
            raise RpaError(ErrorCode.ACTION_FAILED, "좌표 대상의 값을 검증할 수 없습니다.")
        if not isinstance(assertion, AssertionSpec):
            raise RpaError(ErrorCode.INVALID_SCHEMA, "Windows 검증 형식이 올바르지 않습니다.")
        actual = self._element_value(resolved.element)
        if assertion.assertion_type == "windows.value_equals":
            passed = actual == assertion.expected
        elif assertion.assertion_type == "windows.value_contains":
            passed = str(assertion.expected) in str(actual)
        else:
            raise RpaError(ErrorCode.ACTION_UNSUPPORTED, "지원하지 않는 Windows 검증입니다.")
        return AssertionObservation(passed=passed, evidence=FrozenMapping.empty())

    @staticmethod
    def _number(value: object, field: str) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float, str)):
            raise RpaError(ErrorCode.INVALID_SCHEMA, f"{field} 숫자 값이 올바르지 않습니다.")
        try:
            return float(value)
        except ValueError:
            raise RpaError(
                ErrorCode.INVALID_SCHEMA, f"{field} 숫자 값이 올바르지 않습니다."
            ) from None

    @staticmethod
    def _element_value(element: object) -> str:
        dynamic = cast(Any, element)
        getters: tuple[Callable[[], object], ...] = (
            lambda: dynamic.iface_value.CurrentValue,
            lambda: dynamic.get_value(),
            lambda: dynamic.window_text(),
        )
        for getter in getters:
            try:
                return str(getter())
            except Exception:
                continue
        raise RpaError(ErrorCode.ACTION_FAILED, "UI 요소의 현재 값을 읽을 수 없습니다.")


__all__ = ["WINDOWS_ADAPTER_VERSION", "WindowsAutomationAdapter"]
