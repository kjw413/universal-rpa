from __future__ import annotations

import copy
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from pydantic import ValidationError

from universal_rpa.application.normalization import (
    CandidateLiteralValue,
    CandidateSecretValue,
    StepCandidate,
)
from universal_rpa.domain.conditions import ConditionSpec, WaitSpec
from universal_rpa.domain.targets import TargetSpec
from universal_rpa.domain.types import FrozenMapping
from universal_rpa.domain.values import LiteralValue, SecretRefValue, ValueSpec, VariableDefinition
from universal_rpa.domain.workflow import (
    ActionStep,
    DataSourceSpec,
    FailurePolicy,
    IfPresentStep,
    LoopStep,
    Step,
    Workflow,
)


class EditRejected(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class RenameStep:
    step_id: UUID
    label: str


@dataclass(frozen=True, slots=True)
class PatchActionStep:
    step_id: UUID
    changes: FrozenMapping[str, object]

    def __init__(self, step_id: UUID, changes: Mapping[str, object]) -> None:
        object.__setattr__(self, "step_id", step_id)
        object.__setattr__(
            self,
            "changes",
            FrozenMapping.from_mapping(copy.deepcopy(dict(changes))),
        )


@dataclass(frozen=True, slots=True)
class MoveStep:
    step_id: UUID
    parent_step_id: UUID | None
    index: int


@dataclass(frozen=True, slots=True)
class WrapInLoop:
    step_ids: tuple[UUID, ...]
    data_source_id: str
    label: str
    loop_step_id: UUID

    def __init__(
        self,
        step_ids: Sequence[UUID],
        data_source_id: str,
        label: str,
        loop_step_id: UUID | None = None,
    ) -> None:
        object.__setattr__(self, "step_ids", tuple(step_ids))
        object.__setattr__(self, "data_source_id", data_source_id)
        object.__setattr__(self, "label", label)
        object.__setattr__(self, "loop_step_id", loop_step_id or uuid4())


@dataclass(frozen=True, slots=True)
class MergeSteps:
    step_ids: tuple[UUID, ...]
    label: str

    def __init__(self, step_ids: Sequence[UUID], label: str) -> None:
        object.__setattr__(self, "step_ids", tuple(step_ids))
        object.__setattr__(self, "label", label)


@dataclass(frozen=True, slots=True)
class SplitStep:
    step_id: UUID
    second_step_id: UUID

    def __init__(self, step_id: UUID, second_step_id: UUID | None = None) -> None:
        object.__setattr__(self, "step_id", step_id)
        object.__setattr__(self, "second_step_id", second_step_id or uuid4())


@dataclass(frozen=True, slots=True)
class ReplaceTarget:
    step_id: UUID
    target: TargetSpec | None


@dataclass(frozen=True, slots=True)
class SetStepValue:
    step_id: UUID
    value: ValueSpec | None


@dataclass(frozen=True, slots=True)
class UpsertVariable:
    variable: VariableDefinition


@dataclass(frozen=True, slots=True)
class UpsertDataSource:
    data_source: DataSourceSpec


@dataclass(frozen=True, slots=True)
class ImportCandidates:
    candidates: tuple[StepCandidate, ...]
    labels: FrozenMapping[UUID, str]
    confirmed_values: FrozenMapping[UUID, ValueSpec | None]
    credential_refs: FrozenMapping[UUID, str]

    @classmethod
    def from_review(
        cls,
        candidates: Sequence[StepCandidate],
        labels: Sequence[str],
        confirmed_values: Mapping[UUID, ValueSpec | None],
        credential_refs: Mapping[UUID, str] | None = None,
    ) -> ImportCandidates:
        frozen_candidates = tuple(candidates)
        frozen_labels = tuple(label.strip() for label in labels)
        if len(frozen_candidates) != len(frozen_labels) or any(
            not label for label in frozen_labels
        ):
            raise EditRejected("모든 후보 단계의 이름을 입력하세요.")
        label_items: tuple[tuple[UUID, str], ...] = tuple(
            (candidate.candidate_id, label)
            for candidate, label in zip(
                frozen_candidates,
                frozen_labels,
                strict=True,
            )
        )
        confirmed_items: tuple[tuple[UUID, ValueSpec | None], ...] = tuple(
            (candidate_id, copy.deepcopy(value)) for candidate_id, value in confirmed_values.items()
        )
        credential_items: tuple[tuple[UUID, str], ...] = tuple(
            (candidate_id, reference) for candidate_id, reference in (credential_refs or {}).items()
        )
        return cls(
            candidates=frozen_candidates,
            labels=FrozenMapping(label_items),
            confirmed_values=FrozenMapping(confirmed_items),
            credential_refs=FrozenMapping(credential_items),
        )


type EditCommand = (
    RenameStep
    | PatchActionStep
    | MoveStep
    | WrapInLoop
    | MergeSteps
    | SplitStep
    | ReplaceTarget
    | SetStepValue
    | UpsertVariable
    | UpsertDataSource
    | ImportCandidates
)


class WorkflowEditingService:
    def apply(self, workflow: Workflow, command: EditCommand) -> Workflow:
        try:
            updated = self._apply(workflow, command)
            return Workflow.model_validate(
                updated.model_dump(mode="python", exclude={"updated_at"})
                | {"updated_at": datetime.now(UTC)}
            )
        except EditRejected:
            raise
        except (KeyError, TypeError, ValueError, ValidationError):
            raise EditRejected("편집 내용을 적용할 수 없습니다.") from None

    def _apply(self, workflow: Workflow, command: EditCommand) -> Workflow:
        if isinstance(command, RenameStep):
            if not command.label.strip():
                raise EditRejected("단계 이름을 입력하세요.")
            return workflow.model_copy(
                update={
                    "steps": self._replace_step(
                        workflow.steps,
                        command.step_id,
                        lambda step: step.model_copy(update={"label": command.label.strip()}),
                    )
                }
            )
        if isinstance(command, PatchActionStep):
            forbidden = {"step_id", "kind"} & set(command.changes)
            if forbidden:
                raise EditRejected("단계 식별자는 변경할 수 없습니다.")

            def patch(step: Step) -> Step:
                if not isinstance(step, ActionStep):
                    raise EditRejected("실행 단계만 속성을 수정할 수 있습니다.")
                return ActionStep.model_validate(
                    step.model_dump(mode="python") | dict(command.changes)
                )

            return workflow.model_copy(
                update={"steps": self._replace_step(workflow.steps, command.step_id, patch)}
            )
        if isinstance(command, ReplaceTarget):
            return workflow.model_copy(
                update={
                    "steps": self._replace_action(
                        workflow.steps,
                        command.step_id,
                        {"target": command.target},
                    )
                }
            )
        if isinstance(command, SetStepValue):
            return workflow.model_copy(
                update={
                    "steps": self._replace_action(
                        workflow.steps,
                        command.step_id,
                        {"value": command.value},
                    )
                }
            )
        if isinstance(command, UpsertVariable):
            variables = (
                *tuple(
                    variable
                    for variable in workflow.variables
                    if variable.variable_id != command.variable.variable_id
                ),
                command.variable,
            )
            return workflow.model_copy(update={"variables": variables})
        if isinstance(command, UpsertDataSource):
            sources = (
                *tuple(
                    source
                    for source in workflow.data_sources
                    if source.data_source_id != command.data_source.data_source_id
                ),
                command.data_source,
            )
            return workflow.model_copy(update={"data_sources": sources})
        if isinstance(command, MoveStep):
            remaining, removed = self._remove_step(workflow.steps, command.step_id)
            if removed is None:
                raise EditRejected("이동할 단계를 찾을 수 없습니다.")
            return workflow.model_copy(
                update={
                    "steps": self._insert_step(
                        remaining,
                        command.parent_step_id,
                        command.index,
                        removed,
                    )
                }
            )
        if isinstance(command, WrapInLoop):
            if not command.label.strip() or not command.step_ids:
                raise EditRejected("반복할 단계와 이름을 확인하세요.")
            return workflow.model_copy(
                update={
                    "steps": self._wrap_adjacent(
                        workflow.steps,
                        command,
                    )
                }
            )
        if isinstance(command, MergeSteps):
            if not command.label.strip() or len(command.step_ids) < 2:
                raise EditRejected("합칠 단계와 이름을 확인하세요.")
            return workflow.model_copy(
                update={"steps": self._merge_adjacent(workflow.steps, command)}
            )
        if isinstance(command, SplitStep):
            return workflow.model_copy(update={"steps": self._split_step(workflow.steps, command)})
        if isinstance(command, ImportCandidates):
            imported = tuple(
                self._candidate_step(candidate, command) for candidate in command.candidates
            )
            current = workflow.steps
            if (
                len(current) == 1
                and isinstance(current[0], ActionStep)
                and not current[0].enabled
                and current[0].action_type == "windows.activate_window"
            ):
                current = ()
            return workflow.model_copy(update={"steps": (*current, *imported)})
        raise EditRejected("지원하지 않는 편집 명령입니다.")

    def _candidate_step(
        self,
        candidate: StepCandidate,
        command: ImportCandidates,
    ) -> ActionStep:
        if candidate.target is None:
            raise EditRejected("대상을 다시 지정한 뒤 후보를 가져오세요.")
        if (
            candidate.requires_confirmation
            and candidate.candidate_id not in command.confirmed_values
        ):
            raise EditRejected("확인이 필요한 후보 값을 검토하세요.")
        value: ValueSpec | None = command.confirmed_values.get(candidate.candidate_id)
        if isinstance(candidate.value, CandidateSecretValue):
            credential_ref = command.credential_refs.get(candidate.candidate_id)
            if credential_ref is None or not credential_ref.strip():
                raise EditRejected("비밀값 자격 증명을 선택하세요.")
            value = SecretRefValue(credential_ref=credential_ref.strip())
        elif value is None and isinstance(candidate.value, CandidateLiteralValue):
            value = LiteralValue(value=candidate.value.display_value)
        postcondition = None
        if candidate.target.payload.get("coordinate_fallback") is not None:
            postcondition = WaitSpec(
                condition=ConditionSpec(
                    condition_type="windows.element_exists",
                    target=candidate.target,
                    expected=True,
                ),
                timeout_ms=3_000,
                poll_interval_ms=100,
            )
        return ActionStep(
            step_id=uuid4(),
            label=command.labels[candidate.candidate_id],
            failure_policy=FailurePolicy(mode="stop"),
            action_type=candidate.action_type,
            target=candidate.target,
            value=value,
            parameters=candidate.parameters,
            postcondition=postcondition,
        )

    @classmethod
    def _replace_action(
        cls,
        steps: tuple[Step, ...],
        step_id: UUID,
        update: Mapping[str, object],
    ) -> tuple[Step, ...]:
        def transform(step: Step) -> Step:
            if not isinstance(step, ActionStep):
                raise EditRejected("실행 단계만 수정할 수 있습니다.")
            return ActionStep.model_validate(step.model_dump(mode="python") | dict(update))

        return cls._replace_step(steps, step_id, transform)

    @classmethod
    def _replace_step(
        cls,
        steps: tuple[Step, ...],
        step_id: UUID,
        transform: Callable[[Step], Step],
    ) -> tuple[Step, ...]:
        changed = False
        result: list[Step] = []
        for step in steps:
            if step.step_id == step_id:
                result.append(transform(step))
                changed = True
            elif isinstance(step, (LoopStep, IfPresentStep)):
                try:
                    children = cls._replace_step(step.steps, step_id, transform)
                except EditRejected:
                    result.append(step)
                else:
                    result.append(step.model_copy(update={"steps": children}))
                    changed = True
            else:
                result.append(step)
        if not changed:
            raise EditRejected("단계를 찾을 수 없습니다.")
        return tuple(result)

    @classmethod
    def _remove_step(
        cls,
        steps: tuple[Step, ...],
        step_id: UUID,
    ) -> tuple[tuple[Step, ...], Step | None]:
        result: list[Step] = []
        removed: Step | None = None
        for step in steps:
            if step.step_id == step_id:
                if removed is not None:
                    raise EditRejected("중복된 단계 식별자입니다.")
                removed = step
                continue
            if isinstance(step, (LoopStep, IfPresentStep)):
                children, child_removed = cls._remove_step(step.steps, step_id)
                if child_removed is not None:
                    if removed is not None:
                        raise EditRejected("중복된 단계 식별자입니다.")
                    removed = child_removed
                    step = step.model_copy(update={"steps": children})
            result.append(step)
        return tuple(result), removed

    @classmethod
    def _insert_step(
        cls,
        steps: tuple[Step, ...],
        parent_id: UUID | None,
        index: int,
        inserted: Step,
    ) -> tuple[Step, ...]:
        if parent_id is None:
            if index < 0 or index > len(steps):
                raise EditRejected("이동 위치가 올바르지 않습니다.")
            return (*steps[:index], inserted, *steps[index:])
        result: list[Step] = []
        found = False
        for step in steps:
            if step.step_id == parent_id:
                if not isinstance(step, (LoopStep, IfPresentStep)):
                    raise EditRejected("그룹 단계 안으로만 이동할 수 있습니다.")
                if index < 0 or index > len(step.steps):
                    raise EditRejected("이동 위치가 올바르지 않습니다.")
                step = step.model_copy(
                    update={"steps": (*step.steps[:index], inserted, *step.steps[index:])}
                )
                found = True
            elif isinstance(step, (LoopStep, IfPresentStep)):
                try:
                    children = cls._insert_step(step.steps, parent_id, index, inserted)
                except EditRejected:
                    pass
                else:
                    step = step.model_copy(update={"steps": children})
                    found = True
            result.append(step)
        if not found:
            raise EditRejected("이동할 상위 단계를 찾을 수 없습니다.")
        return tuple(result)

    @classmethod
    def _wrap_adjacent(
        cls,
        steps: tuple[Step, ...],
        command: WrapInLoop,
    ) -> tuple[Step, ...]:
        ids = command.step_ids
        positions = [index for index, step in enumerate(steps) if step.step_id in ids]
        if len(positions) == len(ids) and positions == list(range(positions[0], positions[-1] + 1)):
            selected = tuple(steps[index] for index in positions)
            if tuple(step.step_id for step in selected) != ids:
                raise EditRejected("선택한 단계 순서를 확인하세요.")
            loop = LoopStep(
                step_id=command.loop_step_id,
                label=command.label.strip(),
                data_source_id=command.data_source_id,
                steps=selected,
            )
            return (*steps[: positions[0]], loop, *steps[positions[-1] + 1 :])
        for index, step in enumerate(steps):
            if isinstance(step, (LoopStep, IfPresentStep)):
                try:
                    children = cls._wrap_adjacent(step.steps, command)
                except EditRejected:
                    continue
                else:
                    updated = step.model_copy(update={"steps": children})
                    return (*steps[:index], updated, *steps[index + 1 :])
        raise EditRejected("같은 그룹의 연속된 단계만 반복할 수 있습니다.")

    @classmethod
    def _merge_adjacent(
        cls,
        steps: tuple[Step, ...],
        command: MergeSteps,
    ) -> tuple[Step, ...]:
        ids = command.step_ids
        positions = [index for index, step in enumerate(steps) if step.step_id in ids]
        if len(positions) == len(ids) and positions == list(range(positions[0], positions[-1] + 1)):
            selected = tuple(steps[index] for index in positions)
            if not all(isinstance(step, ActionStep) for step in selected):
                raise EditRejected("실행 단계만 합칠 수 있습니다.")
            actions = tuple(step for step in selected if isinstance(step, ActionStep))
            if len(actions) == 2 and all(step.action_type == "windows.click" for step in actions):
                merged = actions[0].model_copy(
                    update={"label": command.label.strip(), "action_type": "windows.double_click"}
                )
            elif all(step.action_type == "windows.set_text" for step in actions):
                merged = actions[-1].model_copy(
                    update={"step_id": actions[0].step_id, "label": command.label.strip()}
                )
            else:
                raise EditRejected("호환되는 클릭 또는 텍스트 단계만 합칠 수 있습니다.")
            return (*steps[: positions[0]], merged, *steps[positions[-1] + 1 :])
        for index, step in enumerate(steps):
            if isinstance(step, (LoopStep, IfPresentStep)):
                try:
                    children = cls._merge_adjacent(step.steps, command)
                except EditRejected:
                    continue
                return (
                    *steps[:index],
                    step.model_copy(update={"steps": children}),
                    *steps[index + 1 :],
                )
        raise EditRejected("같은 그룹의 연속된 단계만 합칠 수 있습니다.")

    @classmethod
    def _split_step(
        cls,
        steps: tuple[Step, ...],
        command: SplitStep,
    ) -> tuple[Step, ...]:
        for index, step in enumerate(steps):
            if step.step_id == command.step_id:
                if not isinstance(step, ActionStep) or step.action_type != "windows.double_click":
                    raise EditRejected("더블클릭 단계만 두 클릭으로 나눌 수 있습니다.")
                first = step.model_copy(
                    update={"action_type": "windows.click", "label": f"{step.label} 1"}
                )
                second = step.model_copy(
                    update={
                        "step_id": command.second_step_id,
                        "action_type": "windows.click",
                        "label": f"{step.label} 2",
                    }
                )
                return (*steps[:index], first, second, *steps[index + 1 :])
            if isinstance(step, (LoopStep, IfPresentStep)):
                try:
                    children = cls._split_step(step.steps, command)
                except EditRejected:
                    continue
                return (
                    *steps[:index],
                    step.model_copy(update={"steps": children}),
                    *steps[index + 1 :],
                )
        raise EditRejected("나눌 단계를 찾을 수 없습니다.")


__all__ = [
    "EditCommand",
    "EditRejected",
    "ImportCandidates",
    "MergeSteps",
    "MoveStep",
    "PatchActionStep",
    "RenameStep",
    "ReplaceTarget",
    "SetStepValue",
    "SplitStep",
    "UpsertDataSource",
    "UpsertVariable",
    "WorkflowEditingService",
    "WrapInLoop",
]
