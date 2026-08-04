"""Fail-closed workflow execution lifecycle for M4."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Protocol
from uuid import UUID, uuid4

from universal_rpa.adapters.registry import AdapterRegistry
from universal_rpa.application.conditions import (
    AdapterActionCapability,
    AssertionEvaluator,
    ConditionPoller,
    RetryExecutor,
)
from universal_rpa.application.loops import DataSourceSnapshot, LoopPlanner
from universal_rpa.application.preflight import PreflightService
from universal_rpa.application.resume import (
    ResumeCompatibility,
    ResumeFingerprintBuilder,
    ResumeValidator,
)
from universal_rpa.application.run_control import RunControl
from universal_rpa.application.value_resolution import ValueResolver
from universal_rpa.application.variable_preparation import (
    PreparedVariables,
    VariablePreparationService,
)
from universal_rpa.domain.conditions import ConditionSpec, WaitSpec
from universal_rpa.domain.errors import ErrorCode, RpaError, ValidationReport
from universal_rpa.domain.execution import RunRequest
from universal_rpa.domain.results import (
    ActionResult,
    LoopCursor,
    OutputCommit,
    RunReport,
    TableData,
    aggregate_run_status,
)
from universal_rpa.domain.targets import DateContext, RuntimeEnvironment, TargetSpec
from universal_rpa.domain.types import DataCell, FrozenJsonValue, FrozenMapping
from universal_rpa.domain.values import RowBindingValue
from universal_rpa.domain.workflow import ActionStep, IfPresentStep, LoopStep, Step
from universal_rpa.infrastructure.checkpoint_store import (
    Checkpoint,
    JsonCheckpointStore,
    ResumeFingerprint,
    TerminalRunRecord,
)
from universal_rpa.infrastructure.execution_journal import (
    InProgressAction,
    InProgressIterationJournal,
    JsonExecutionJournalStore,
)
from universal_rpa.ports.automation import ActionRequest, AdapterActionResult, ExecutionContext
from universal_rpa.ports.credentials import SecretStorePort


@dataclass(frozen=True, slots=True)
class RunStarted:
    run_id: UUID
    workflow_id: UUID
    workflow_name: str
    workflow_revision: int
    step_labels: FrozenMapping[UUID, str]
    started_at: datetime
    runtime: RuntimeEnvironment


@dataclass(frozen=True, slots=True)
class RunActionObserved:
    result: ActionResult
    target: TargetSpec | None
    runtime: RuntimeEnvironment


class RunObserver(Protocol):
    def on_run_started(self, event: RunStarted) -> None: ...

    def on_action_result(self, event: RunActionObserved) -> None: ...

    def on_run_finished(self, report: RunReport) -> None: ...


@dataclass(frozen=True, slots=True)
class StepTestRequest:
    run_request: RunRequest
    step_id: UUID
    cursor: tuple[LoopCursor, ...] = ()
    date_context: DateContext | None = None


@dataclass(frozen=True, slots=True)
class StepTestEligibility:
    enabled: bool
    reason_code: str | None = None
    safe_message: str = ""


@dataclass(slots=True)
class _ResumeTraversal:
    target: tuple[LoopCursor, ...]
    replay_target: bool
    active: bool = False
    matched: bool = False

    def decide(self, cursor: tuple[LoopCursor, ...]) -> str:
        if self.active:
            return "execute"
        if cursor == self.target:
            self.matched = True
            self.active = True
            return "execute" if self.replay_target else "skip"
        if len(cursor) < len(self.target) and cursor == self.target[: len(cursor)]:
            return "seek"
        return "skip"


class ExecutionService:
    def __init__(
        self,
        *,
        preflight: PreflightService,
        registry: AdapterRegistry,
        loop_planner: LoopPlanner,
        variable_preparation: VariablePreparationService,
        value_resolver: ValueResolver,
        secret_store: SecretStorePort,
        checkpoints: JsonCheckpointStore,
        journals: JsonExecutionJournalStore,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._preflight = preflight
        self._registry = registry
        self._loop_planner = loop_planner
        self._variable_preparation = variable_preparation
        self._value_resolver = value_resolver
        self._secret_store = secret_store
        self._checkpoints = checkpoints
        self._journals = journals
        self._now = now
        self._poller = ConditionPoller(registry)
        self._assertions = AssertionEvaluator(registry)
        self._retry = RetryExecutor()
        self._fingerprint_builder = ResumeFingerprintBuilder()
        self._resume_validator = ResumeValidator()

    def preflight(self, request: RunRequest) -> ValidationReport:
        return self._preflight.check(request)

    def run(
        self,
        request: RunRequest,
        control: RunControl,
        observers: tuple[RunObserver, ...] = (),
    ) -> RunReport:
        report = self._run(request, control, observers)
        for observer in observers:
            try:
                observer.on_run_finished(report)
            except Exception:
                continue
        return report

    def _run(
        self,
        request: RunRequest,
        control: RunControl,
        observers: tuple[RunObserver, ...],
    ) -> RunReport:
        started = self._utc_now()
        run_id = request.resume.run_id if request.resume is not None else uuid4()
        validation, runtime = self._preflight.inspect(request)
        if not validation.is_valid:
            return self._failed_report(request, run_id, started, validation.errors[0])
        if request.validation_only:
            return self._report(request, run_id, started, (), 0, None, ())
        control.configure_deadline(request.workflow.run_policy.max_runtime_seconds)

        try:
            snapshots = self._loop_planner.materialize_snapshots(
                request.project_dir, request.workflow
            )
            self._loop_planner.validate_iteration_bound(request.workflow, snapshots)
            date_context, resume_cursor, replay_cursor = self._prepare_resume(
                request, run_id, snapshots
            )
            resume_state = _ResumeTraversal(resume_cursor, replay_cursor) if resume_cursor else None
            prepared = self._variable_preparation.prepare(
                request.workflow,
                request.inputs,
                request.project_dir,
                date_context,
                snapshots,
                self._secret_store,
            )
            fingerprint = self._fingerprint(request, prepared, snapshots, runtime)
            if request.resume is not None:
                checkpoint = self._checkpoints.load_active(request.workflow.workflow_id, run_id)
                self._resume_validator.validate_fingerprint(checkpoint.fingerprint, fingerprint)
                self._resume_validator.validate_outputs(
                    checkpoint.output_commits, request.inputs.output_directory
                )
            else:
                self._checkpoints.save_active(
                    Checkpoint(
                        workflow_id=request.workflow.workflow_id,
                        run_id=run_id,
                        date_context_today=date_context.today.isoformat(),
                        date_context_run_date=date_context.run_date.isoformat(),
                        fingerprint=fingerprint,
                        updated_at=started,
                    )
                )
            if observers:
                if runtime is None:
                    raise RpaError(
                        ErrorCode.ENVIRONMENT_MISMATCH,
                        "실행 환경을 확인할 수 없어 실행 관찰을 시작할 수 없습니다.",
                    )
                self._notify_started(observers, request, run_id, started, runtime)
            results: list[ActionResult] = []
            commits: list[OutputCommit] = []
            completed = self._execute_steps(
                request,
                request.workflow.steps,
                run_id,
                date_context,
                prepared,
                snapshots,
                fingerprint,
                runtime,
                observers,
                control,
                results,
                commits,
                (),
                (),
                FrozenMapping.empty(),
                resume_state,
            )
            if resume_state is not None and not resume_state.matched:
                raise RpaError(
                    ErrorCode.RESUME_MISMATCH,
                    "저장된 반복 위치를 현재 업무 정의에서 찾을 수 없습니다.",
                )
            report = self._report(
                request,
                run_id,
                started,
                tuple(results),
                completed,
                self._last_cursor(results),
                tuple(commits),
            )
            if report.status in {"success", "partial"}:
                self._checkpoints.mark_terminal(
                    TerminalRunRecord(
                        workflow_id=request.workflow.workflow_id,
                        run_id=run_id,
                        status="success" if report.status == "success" else "partial",
                        finished_at=self._utc_now(),
                    )
                )
                self._journals.clear(request.workflow.workflow_id, run_id)
            return report
        except RpaError as error:
            return self._failed_report(request, run_id, started, error)
        except Exception:
            return self._failed_report(
                request,
                run_id,
                started,
                RpaError(ErrorCode.INTERNAL_ERROR, "실행 중 내부 오류가 발생했습니다."),
            )

    def discover_resumable(self, request: RunRequest) -> tuple[ResumeCompatibility, ...]:
        """Classify every stored checkpoint for this workflow, newest first.

        Discovery is read-only: it rebuilds the current fingerprint with the same
        builder the runner uses, but writes no checkpoint, journal, or output.
        """

        try:
            checkpoints = self._checkpoints.discover_active(request.workflow.workflow_id)
        except RpaError:
            return ()
        if not checkpoints:
            return ()
        try:
            snapshots = self._loop_planner.materialize_snapshots(
                request.project_dir, request.workflow
            )
            _, runtime = self._preflight.inspect(request)
        except RpaError as error:
            return tuple(
                self._refused(checkpoint, error.code, error.safe_message)
                for checkpoint in checkpoints
            )
        return tuple(
            self._classify(request, checkpoint, snapshots, runtime) for checkpoint in checkpoints
        )

    def _classify(
        self,
        request: RunRequest,
        checkpoint: Checkpoint,
        snapshots: FrozenMapping[str, DataSourceSnapshot],
        runtime: RuntimeEnvironment | None,
    ) -> ResumeCompatibility:
        try:
            date_context = DateContext(
                today=date.fromisoformat(checkpoint.date_context_today),
                run_date=date.fromisoformat(checkpoint.date_context_run_date),
            )
        except ValueError:
            return self._refused(
                checkpoint,
                ErrorCode.CHECKPOINT_INVALID,
                "재개 날짜 정보가 올바르지 않습니다.",
            )
        try:
            journal = self._journals.load(request.workflow.workflow_id, checkpoint.run_id)
        except RpaError as error:
            return self._refused(checkpoint, error.code, error.safe_message)
        if journal is not None and any(not action.idempotent for action in journal.actions):
            return self._refused(
                checkpoint,
                ErrorCode.RESUME_UNSAFE,
                "중단된 비멱등 작업이 있어 수동 확인이 필요합니다.",
            )
        try:
            prepared = self._variable_preparation.prepare(
                request.workflow,
                request.inputs,
                request.project_dir,
                date_context,
                snapshots,
                self._secret_store,
            )
            current = self._fingerprint(request, prepared, snapshots, runtime)
        except RpaError as error:
            return self._refused(checkpoint, error.code, error.safe_message)
        mismatch = self._resume_validator.compare(checkpoint.fingerprint, current)
        if mismatch:
            return self._refused(
                checkpoint,
                ErrorCode.RESUME_MISMATCH,
                "업무 정의·실행 입력·데이터 또는 실행 환경이 바뀌어 재개할 수 없습니다.",
                mismatch,
            )
        try:
            self._resume_validator.validate_outputs(
                checkpoint.output_commits, request.inputs.output_directory
            )
        except RpaError as error:
            return self._refused(checkpoint, error.code, error.safe_message, ("output",))
        return ResumeCompatibility(
            workflow_id=checkpoint.workflow_id,
            run_id=checkpoint.run_id,
            resumable=True,
            completed_cursor=checkpoint.completed_cursor,
            updated_at=checkpoint.updated_at,
        )

    @staticmethod
    def _refused(
        checkpoint: Checkpoint,
        code: ErrorCode,
        safe_message: str,
        mismatch_fields: tuple[str, ...] = (),
    ) -> ResumeCompatibility:
        return ResumeCompatibility(
            workflow_id=checkpoint.workflow_id,
            run_id=checkpoint.run_id,
            resumable=False,
            completed_cursor=checkpoint.completed_cursor,
            updated_at=checkpoint.updated_at,
            error_code=code,
            safe_message=safe_message,
            mismatch_fields=mismatch_fields,
        )

    def step_test_eligibility(self, request: StepTestRequest) -> StepTestEligibility:
        located = self._find_action_path(request.run_request.workflow.steps, request.step_id)
        if located is None:
            return StepTestEligibility(False, "unknown_step", "단계를 찾을 수 없습니다.")
        found, loop_path = located
        if not found.enabled:
            return StepTestEligibility(
                False, "disabled", "사용 중지된 단계는 테스트할 수 없습니다."
            )
        if found.input_step_id is not None:
            return StepTestEligibility(
                False, "requires_prior_action_output", "이전 추출 결과가 필요한 단계입니다."
            )
        if isinstance(found.value, RowBindingValue):
            expected_ids = tuple(loop.step_id for loop in loop_path)
            actual_ids = tuple(item.loop_step_id for item in request.cursor)
            if not request.cursor:
                return StepTestEligibility(
                    False, "row_cursor_required", "반복 행을 선택한 뒤 테스트하세요."
                )
            if actual_ids != expected_ids:
                return StepTestEligibility(
                    False, "invalid_cursor", "선택한 반복 행이 이 단계와 일치하지 않습니다."
                )
        return StepTestEligibility(True)

    def test_step(self, request: StepTestRequest, control: RunControl) -> ActionResult:
        eligibility = self.step_test_eligibility(request)
        if not eligibility.enabled:
            raise RpaError(ErrorCode.INVALID_SCHEMA, eligibility.safe_message)
        validation = self.preflight(request.run_request)
        if not validation.is_valid:
            issue = validation.errors[0]
            raise RpaError(issue.code, issue.safe_message)
        snapshots = self._loop_planner.materialize_snapshots(
            request.run_request.project_dir, request.run_request.workflow
        )
        today = self._utc_now().date()
        date_context = request.date_context or DateContext(today=today, run_date=today)
        prepared = self._variable_preparation.prepare(
            request.run_request.workflow,
            request.run_request.inputs,
            request.run_request.project_dir,
            date_context,
            snapshots,
            self._secret_store,
        )
        found = self._find_action_path(request.run_request.workflow.steps, request.step_id)
        if found is None:
            raise RpaError(ErrorCode.INVALID_SCHEMA, "단계를 찾을 수 없습니다.")
        step, loop_path = found
        row_stack = self._reconstruct_row_stack(loop_path, request.cursor, snapshots)
        run_id = uuid4()
        result, _, _ = self._execute_action(
            request.run_request,
            step,
            run_id,
            date_context,
            prepared,
            control,
            request.cursor,
            tuple(item.row_index for item in request.cursor),
            row_stack,
            FrozenMapping.empty(),
            persist_journal=False,
        )
        return result

    def _prepare_resume(
        self,
        request: RunRequest,
        run_id: UUID,
        snapshots: FrozenMapping[str, DataSourceSnapshot],
    ) -> tuple[DateContext, tuple[LoopCursor, ...] | None, bool]:
        del snapshots
        if request.resume is None:
            today = self._utc_now().date()
            return DateContext(today=today, run_date=today), None, False
        checkpoint = self._checkpoints.load_active(request.workflow.workflow_id, run_id)
        journal = self._journals.load(request.workflow.workflow_id, run_id)
        if journal is not None and any(not action.idempotent for action in journal.actions):
            raise RpaError(
                ErrorCode.RESUME_UNSAFE, "중단된 비멱등 작업이 있어 수동 확인이 필요합니다."
            )
        try:
            context = DateContext(
                today=date.fromisoformat(checkpoint.date_context_today),
                run_date=date.fromisoformat(checkpoint.date_context_run_date),
            )
        except ValueError:
            raise RpaError(
                ErrorCode.CHECKPOINT_INVALID, "재개 날짜 정보가 올바르지 않습니다."
            ) from None
        return (
            context,
            journal.cursor if journal is not None else checkpoint.completed_cursor or None,
            journal is not None,
        )

    def _execute_steps(
        self,
        request: RunRequest,
        steps: Sequence[Step],
        run_id: UUID,
        date_context: DateContext,
        prepared: PreparedVariables,
        snapshots: FrozenMapping[str, DataSourceSnapshot],
        fingerprint: ResumeFingerprint,
        runtime: RuntimeEnvironment | None,
        observers: tuple[RunObserver, ...],
        control: RunControl,
        results: list[ActionResult],
        commits: list[OutputCommit],
        cursor: tuple[LoopCursor, ...],
        row_stack: tuple[FrozenMapping[str, DataCell], ...],
        action_outputs: FrozenMapping[UUID, FrozenJsonValue | TableData],
        resume_state: _ResumeTraversal | None,
    ) -> int:
        completed = 0
        for step in steps:
            control.wait_if_paused()
            if not step.enabled:
                continue
            iteration_path = tuple(item.row_index for item in cursor)
            if isinstance(step, ActionStep):
                if resume_state is not None and not resume_state.active:
                    continue
                result, output, action_runtime = self._execute_action(
                    request,
                    step,
                    run_id,
                    date_context,
                    prepared,
                    control,
                    cursor,
                    iteration_path,
                    row_stack,
                    action_outputs,
                )
                results.append(result)
                if result.status in {"failed", "cancelled"}:
                    if step.failure_policy.mode == "skip_iteration" and cursor:
                        results[-1] = result.model_copy(
                            update={
                                "status": "skipped",
                                "error_code": None,
                                "safe_message": "",
                                "skip_reason": "skip_iteration",
                            }
                        )
                    self._notify_action(
                        observers, results[-1], step.target, action_runtime or runtime
                    )
                    return completed
                if output is not None:
                    action_outputs = FrozenMapping((*action_outputs._items, (step.step_id, output)))
                if result.output_commit is not None:
                    commits.append(result.output_commit)
                self._notify_action(observers, result, step.target, action_runtime or runtime)
                continue
            if isinstance(step, IfPresentStep):
                if resume_state is not None and not resume_state.active:
                    continue
                condition = ConditionSpec(
                    condition_type=step.condition.condition_type,
                    target=step.condition.target,
                    expected=True,
                )
                try:
                    self._poller.wait(
                        WaitSpec(
                            condition=condition,
                            timeout_ms=step.condition.timeout_ms,
                            poll_interval_ms=step.condition.poll_interval_ms,
                        ),
                        self._context(
                            run_id,
                            step.step_id,
                            iteration_path,
                            prepared,
                            date_context,
                            request.inputs.output_directory,
                            row_stack,
                            action_outputs,
                        ),
                        control,
                    )
                except RpaError as error:
                    if error.code is ErrorCode.CONDITION_TIMEOUT:
                        results.append(
                            ActionResult(
                                run_id=run_id,
                                step_id=step.step_id,
                                iteration_path=iteration_path,
                                iteration_cursor=cursor,
                                status="skipped",
                                started_at=self._utc_now(),
                                skip_reason="if_present_absent",
                            )
                        )
                        self._notify_action(observers, results[-1], step.condition.target, runtime)
                        continue
                    results.append(
                        self._failure_result(run_id, step.step_id, iteration_path, cursor, error)
                    )
                    self._notify_action(observers, results[-1], step.condition.target, runtime)
                    return completed
                completed += self._execute_steps(
                    request,
                    step.steps,
                    run_id,
                    date_context,
                    prepared,
                    snapshots,
                    fingerprint,
                    runtime,
                    observers,
                    control,
                    results,
                    commits,
                    cursor,
                    row_stack,
                    action_outputs,
                    resume_state,
                )
                continue
            try:
                snapshot = snapshots[step.data_source_id]
            except KeyError:
                raise RpaError(
                    ErrorCode.DATA_SOURCE_INVALID, "반복 데이터 소스를 찾을 수 없습니다."
                ) from None
            for row_index in range(len(snapshot.rows)):
                next_cursor = (*cursor, LoopCursor(loop_step_id=step.step_id, row_index=row_index))
                decision = (
                    resume_state.decide(next_cursor) if resume_state is not None else "execute"
                )
                if decision == "skip":
                    continue
                next_rows = (*row_stack, snapshot.row_mapping(row_index))
                before = len(results)
                completed += self._execute_steps(
                    request,
                    step.steps,
                    run_id,
                    date_context,
                    prepared,
                    snapshots,
                    fingerprint,
                    runtime,
                    observers,
                    control,
                    results,
                    commits,
                    next_cursor,
                    next_rows,
                    FrozenMapping.empty(),
                    resume_state,
                )
                if any(result.status in {"failed", "cancelled"} for result in results[before:]):
                    return completed
                self._checkpoints.save_active(
                    Checkpoint(
                        workflow_id=request.workflow.workflow_id,
                        run_id=run_id,
                        date_context_today=date_context.today.isoformat(),
                        date_context_run_date=date_context.run_date.isoformat(),
                        fingerprint=fingerprint,
                        completed_cursor=next_cursor,
                        output_commits=tuple(
                            commit for commit in commits if hasattr(commit, "destination")
                        ),
                        updated_at=self._utc_now(),
                    )
                )
                self._journals.clear(request.workflow.workflow_id, run_id)
                completed += 1
        return completed

    def _execute_action(
        self,
        request: RunRequest,
        step: ActionStep,
        run_id: UUID,
        date_context: DateContext,
        prepared: PreparedVariables,
        control: RunControl,
        cursor: tuple[LoopCursor, ...],
        iteration_path: tuple[int, ...],
        row_stack: tuple[FrozenMapping[str, DataCell], ...],
        outputs: FrozenMapping[UUID, FrozenJsonValue | TableData],
        *,
        persist_journal: bool = True,
    ) -> tuple[ActionResult, FrozenJsonValue | TableData | None, RuntimeEnvironment | None]:
        started = self._utc_now()
        context = self._context(
            run_id,
            step.step_id,
            iteration_path,
            prepared,
            date_context,
            request.inputs.output_directory,
            row_stack,
            outputs,
            cursor,
        )
        observed_runtime: RuntimeEnvironment | None = None
        try:
            if step.precondition is not None:
                self._poller.wait(step.precondition, context, control)
            if step.action_type == "windows.wait":
                if step.wait is None:
                    raise RpaError(ErrorCode.INVALID_SCHEMA, "대기 조건이 없습니다.")
                self._poller.wait(step.wait, context, control)
                outcome = AdapterActionResult(output=None, evidence=FrozenMapping.empty())
                attempts = 1
            else:
                adapter_id, _, _ = step.action_type.partition(".")
                adapter = self._registry.require(adapter_id)
                descriptor = adapter.descriptor()
                value: object | None = None
                if step.input_step_id is not None:
                    value = outputs.get(step.input_step_id)
                    if value is None:
                        raise RpaError(
                            ErrorCode.INVALID_SCHEMA, "같은 반복의 추출 결과가 없습니다."
                        )
                elif step.value is not None:
                    value = self._value_resolver.resolve(step.value, context)
                request_action = ActionRequest(
                    action_type=step.action_type,
                    target=step.target,
                    parameters=step.parameters,
                    value=value,  # type: ignore[arg-type]
                    has_postcondition_or_assertion=bool(step.postcondition or step.assertions),
                )
                journal = InProgressIterationJournal(
                    workflow_id=request.workflow.workflow_id,
                    run_id=run_id,
                    cursor=cursor,
                    actions=(
                        InProgressAction(
                            step_id=step.step_id,
                            action_type=step.action_type,
                            idempotent=step.action_type in descriptor.idempotent_actions,
                            state="inflight",
                        ),
                    ),
                    started_at=started,
                    updated_at=self._utc_now(),
                )
                if persist_journal:
                    previous = self._journals.load(request.workflow.workflow_id, run_id)
                    if previous is not None and previous.cursor == cursor:
                        journal = journal.model_copy(
                            update={"actions": (*previous.actions, *journal.actions)}
                        )
                    self._journals.save(journal)
                capability = AdapterActionCapability(
                    step.action_type,
                    step.action_type in descriptor.idempotent_actions,
                    descriptor.retryable_errors_by_action.get(step.action_type, frozenset()),
                )
                retried = self._retry.execute(
                    step.failure_policy,
                    capability,
                    lambda: adapter.execute(request_action, context, control),
                    control,
                )
                outcome, attempts = retried.result, retried.attempt_count
                observed_runtime = outcome.runtime
                if outcome.error_code is not None:
                    raise RpaError(outcome.error_code, outcome.safe_message, outcome.evidence)
                if persist_journal:
                    self._journals.save(
                        journal.model_copy(
                            update={
                                "actions": (
                                    *journal.actions[:-1],
                                    journal.actions[-1].model_copy(update={"state": "succeeded"}),
                                ),
                                "updated_at": self._utc_now(),
                            }
                        )
                    )
            if step.postcondition is not None:
                self._poller.wait(step.postcondition, context, control)
            subject = outcome.output_commit or outcome.output
            for assertion in step.assertions:
                evaluated = self._assertions.evaluate(
                    step.action_type, assertion, subject, step.target, context, control
                )
                if not evaluated.passed:
                    raise RpaError(
                        evaluated.error_code or ErrorCode.ASSERTION_FAILED,
                        evaluated.safe_message,
                        evaluated.evidence,
                    )
            return (
                ActionResult(
                    run_id=run_id,
                    step_id=step.step_id,
                    iteration_path=iteration_path,
                    iteration_cursor=cursor,
                    status="success",
                    started_at=started,
                    attempt_count=attempts,
                    evidence=outcome.evidence,
                    output_commit=outcome.output_commit,
                ),
                outcome.output,
                observed_runtime,
            )
        except RpaError as error:
            return (
                self._failure_result(run_id, step.step_id, iteration_path, cursor, error, started),
                None,
                observed_runtime,
            )

    def _failure_result(
        self,
        run_id: UUID,
        step_id: UUID,
        iteration_path: tuple[int, ...],
        cursor: tuple[LoopCursor, ...],
        error: RpaError,
        started: datetime | None = None,
    ) -> ActionResult:
        return ActionResult(
            run_id=run_id,
            step_id=step_id,
            iteration_path=iteration_path,
            iteration_cursor=cursor,
            status="cancelled" if error.code is ErrorCode.CANCELLED else "failed",
            started_at=started or self._utc_now(),
            error_code=error.code,
            safe_message=error.safe_message,
            evidence=error.evidence,
        )

    @staticmethod
    def _context(
        run_id: UUID,
        step_id: UUID,
        iteration_path: tuple[int, ...],
        prepared: PreparedVariables,
        date_context: DateContext,
        output_root: Path,
        row_stack: tuple[FrozenMapping[str, DataCell], ...],
        outputs: FrozenMapping[UUID, FrozenJsonValue | TableData],
        iteration_cursor: tuple[LoopCursor, ...] = (),
    ) -> ExecutionContext:
        return ExecutionContext(
            run_id=run_id,
            step_id=step_id,
            iteration_path=iteration_path,
            variables=prepared.values,
            credential_refs=prepared.credential_refs,
            date_context=date_context,
            output_root=output_root,
            row_stack=row_stack,
            action_outputs=outputs,
            iteration_cursor=iteration_cursor,
        )

    @staticmethod
    def _find_action_path(
        steps: Sequence[Step],
        step_id: UUID,
        loop_path: tuple[LoopStep, ...] = (),
    ) -> tuple[ActionStep, tuple[LoopStep, ...]] | None:
        for step in steps:
            if isinstance(step, ActionStep) and step.step_id == step_id:
                return step, loop_path
            if isinstance(step, LoopStep):
                found = ExecutionService._find_action_path(step.steps, step_id, (*loop_path, step))
                if found is not None:
                    return found
            elif isinstance(step, IfPresentStep):
                found = ExecutionService._find_action_path(step.steps, step_id, loop_path)
                if found is not None:
                    return found
        return None

    @staticmethod
    def _reconstruct_row_stack(
        loop_path: tuple[LoopStep, ...],
        cursor: tuple[LoopCursor, ...],
        snapshots: FrozenMapping[str, DataSourceSnapshot],
    ) -> tuple[FrozenMapping[str, DataCell], ...]:
        if len(cursor) != len(loop_path):
            raise RpaError(
                ErrorCode.INVALID_SCHEMA, "단계 테스트의 반복 행 선택이 완전하지 않습니다."
            )
        rows: list[FrozenMapping[str, DataCell]] = []
        for loop, selected in zip(loop_path, cursor, strict=True):
            if selected.loop_step_id != loop.step_id:
                raise RpaError(
                    ErrorCode.INVALID_SCHEMA, "단계 테스트의 반복 단계가 일치하지 않습니다."
                )
            try:
                snapshot = snapshots[loop.data_source_id]
            except KeyError:
                raise RpaError(
                    ErrorCode.DATA_SOURCE_INVALID, "반복 데이터 소스를 찾을 수 없습니다."
                ) from None
            rows.append(snapshot.row_mapping(selected.row_index))
        return tuple(rows)

    @staticmethod
    def _find_action(steps: Sequence[Step], step_id: UUID) -> ActionStep | None:
        for step in steps:
            if isinstance(step, ActionStep) and step.step_id == step_id:
                return step
            if isinstance(step, (LoopStep, IfPresentStep)):
                found = ExecutionService._find_action(step.steps, step_id)
                if found is not None:
                    return found
        return None

    def _fingerprint(
        self,
        request: RunRequest,
        prepared: PreparedVariables,
        snapshots: FrozenMapping[str, DataSourceSnapshot],
        runtime: RuntimeEnvironment | None,
    ) -> ResumeFingerprint:
        return self._fingerprint_builder.build(
            workflow=request.workflow,
            output_root=request.inputs.output_directory,
            prepared=prepared,
            snapshots=snapshots,
            registry=self._registry,
            runtime=runtime,
            secret_store=self._secret_store,
        )

    @staticmethod
    def _step_labels(steps: Sequence[Step]) -> FrozenMapping[UUID, str]:
        labels: list[tuple[UUID, str]] = []
        for step in steps:
            labels.append((step.step_id, step.label))
            if isinstance(step, (LoopStep, IfPresentStep)):
                labels.extend(ExecutionService._step_labels(step.steps).items())
        return FrozenMapping(tuple(labels))

    def _notify_started(
        self,
        observers: tuple[RunObserver, ...],
        request: RunRequest,
        run_id: UUID,
        started: datetime,
        runtime: RuntimeEnvironment,
    ) -> None:
        event = RunStarted(
            run_id=run_id,
            workflow_id=request.workflow.workflow_id,
            workflow_name=request.workflow.name,
            workflow_revision=request.workflow.revision,
            step_labels=self._step_labels(request.workflow.steps),
            started_at=started,
            runtime=runtime,
        )
        for observer in observers:
            try:
                observer.on_run_started(event)
            except Exception:
                raise RpaError(
                    ErrorCode.INTERNAL_ERROR, "실행 관찰자를 시작할 수 없습니다."
                ) from None

    @staticmethod
    def _notify_action(
        observers: tuple[RunObserver, ...],
        result: ActionResult,
        target: TargetSpec | None,
        runtime: RuntimeEnvironment | None,
    ) -> None:
        if not observers:
            return
        if runtime is None:
            raise RpaError(ErrorCode.ENVIRONMENT_MISMATCH, "작업 실행 환경을 확인할 수 없습니다.")
        event = RunActionObserved(result=result, target=target, runtime=runtime)
        for observer in observers:
            try:
                observer.on_action_result(event)
            except Exception:
                raise RpaError(
                    ErrorCode.INTERNAL_ERROR, "실행 결과 관찰자를 완료할 수 없습니다."
                ) from None

    def _report(
        self,
        request: RunRequest,
        run_id: UUID,
        started: datetime,
        results: tuple[ActionResult, ...],
        completed: int,
        cursor: tuple[LoopCursor, ...] | None,
        commits: tuple[OutputCommit, ...],
    ) -> RunReport:
        status = aggregate_run_status(results)
        failed = next(
            (result for result in reversed(results) if result.status in {"failed", "cancelled"}),
            None,
        )
        return RunReport(
            run_id=run_id,
            workflow_id=request.workflow.workflow_id,
            workflow_revision=request.workflow.revision,
            status=status,
            started_at=started,
            finished_at=self._utc_now(),
            error_code=failed.error_code if failed is not None else None,
            safe_message=failed.safe_message if failed is not None else "",
            results=results,
            completed_iterations=completed,
            last_checkpoint_cursor=cursor,
            output_commits=commits,
        )

    def _failed_report(
        self, request: RunRequest, run_id: UUID, started: datetime, error: RpaError | object
    ) -> RunReport:
        safe = (
            error
            if isinstance(error, RpaError)
            else RpaError(ErrorCode.INTERNAL_ERROR, "실행을 시작할 수 없습니다.")
        )
        return RunReport(
            run_id=run_id,
            workflow_id=request.workflow.workflow_id,
            workflow_revision=request.workflow.revision,
            status="cancelled" if safe.code is ErrorCode.CANCELLED else "failed",
            started_at=started,
            finished_at=self._utc_now(),
            error_code=safe.code,
            safe_message=safe.safe_message,
            results=(),
            completed_iterations=0,
        )

    @staticmethod
    def _last_cursor(results: Sequence[ActionResult]) -> tuple[LoopCursor, ...] | None:
        for result in reversed(results):
            if result.iteration_cursor:
                return result.iteration_cursor
        return None

    def _utc_now(self) -> datetime:
        value = self._now()
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


__all__ = [
    "ExecutionService",
    "ResumeCompatibility",
    "RunActionObserved",
    "RunObserver",
    "RunStarted",
    "StepTestEligibility",
    "StepTestRequest",
]
