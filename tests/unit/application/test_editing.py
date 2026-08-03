from __future__ import annotations

import shutil
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest

from universal_rpa.application.editing import (
    EditRejected,
    ImportCandidates,
    MergeSteps,
    RenameStep,
    SetStepValue,
    SplitStep,
    WorkflowEditingService,
)
from universal_rpa.application.normalization import NormalizationService, StepCandidate
from universal_rpa.domain.targets import TargetSpec
from universal_rpa.domain.values import LiteralValue, SecretRefValue
from universal_rpa.domain.workflow import ActionStep, TargetAppSpec, Workflow
from universal_rpa.infrastructure.recording_store import JsonlRecordingStore

SESSION_ID = UUID("00000000-0000-0000-0000-000000000702")
STEP_ID = UUID("00000000-0000-0000-0000-000000000811")
NOW = datetime(2026, 7, 27, tzinfo=UTC)
TARGET = TargetSpec.model_validate(
    {
        "adapter_id": "windows",
        "payload": {
            "selector": {"automation_id": "query-date"},
            "coordinate_fallback": None,
        },
    }
)


def workflow_with_steps(*steps: ActionStep) -> Workflow:
    return Workflow(
        workflow_id=UUID("00000000-0000-0000-0000-000000000810"),
        name="편집 테스트",
        revision=1,
        target_apps=(
            TargetAppSpec(
                app_id="erp",
                process_executable="erp.exe",
                window_class="ERPMain",
            ),
        ),
        steps=steps
        or (
            ActionStep(
                step_id=STEP_ID,
                label="첫 단계",
                enabled=False,
                action_type="windows.activate_window",
            ),
        ),
        created_at=NOW,
        updated_at=NOW,
    )


def text_step(value: LiteralValue | SecretRefValue) -> ActionStep:
    return ActionStep(
        step_id=STEP_ID,
        label="텍스트 입력",
        action_type="windows.set_text",
        target=TARGET,
        value=value,
    )


def golden_candidates(tmp_path: Path) -> tuple[StepCandidate, ...]:
    session_dir = tmp_path / str(SESSION_ID)
    session_dir.mkdir()
    fixture_dir = (
        Path(__file__).resolve().parents[2] / "fixtures" / "recordings" / "ctrl-a-date-enter"
    )
    for filename in ("manifest.json", "events.jsonl"):
        shutil.copy2(fixture_dir / filename, session_dir / filename)
    store = JsonlRecordingStore.for_test(tmp_path)
    return NormalizationService().normalize_session(store, SESSION_ID).candidates


def test_empty_label_is_rejected_without_mutating_workflow() -> None:
    before = workflow_with_steps(text_step(LiteralValue(value="visible")))

    with pytest.raises(EditRejected):
        WorkflowEditingService().apply(before, RenameStep(step_id=STEP_ID, label=""))

    assert before.steps[0].label == "텍스트 입력"


def test_full_secret_value_replaces_previous_literal_atomically() -> None:
    before = workflow_with_steps(text_step(LiteralValue(value="visible")))

    after = WorkflowEditingService().apply(
        before,
        SetStepValue(
            step_id=STEP_ID,
            value=SecretRefValue(credential_ref="mis/query-password"),
        ),
    )

    assert "visible" not in after.model_dump_json()
    assert isinstance(after.steps[0], ActionStep)
    assert after.steps[0].value == SecretRefValue(credential_ref="mis/query-password")
    original_step = before.steps[0]
    assert isinstance(original_step, ActionStep)
    assert original_step.value == LiteralValue(value="visible")


def test_import_reviewed_keyboard_candidates_creates_canonical_actions(tmp_path: Path) -> None:
    candidates = golden_candidates(tmp_path)
    date_candidate = candidates[1]
    command = ImportCandidates.from_review(
        candidates,
        labels=("전체 선택", "조회일 입력", "확인"),
        confirmed_values={date_candidate.candidate_id: LiteralValue(value="2026-07-27")},
    )

    after = WorkflowEditingService().apply(workflow_with_steps(), command)

    assert [step.action_type for step in after.steps if isinstance(step, ActionStep)] == [
        "windows.hotkey",
        "windows.set_text",
        "windows.press_key",
    ]
    assert all(step.failure_policy.mode == "stop" for step in after.steps)
    for step, candidate in zip(after.steps, candidates, strict=True):
        assert isinstance(step, ActionStep)
        assert step.target == candidate.target


def test_import_requires_labels_and_missing_target_is_rejected(tmp_path: Path) -> None:
    candidates = golden_candidates(tmp_path)
    with pytest.raises(EditRejected, match="이름"):
        ImportCandidates.from_review(candidates, labels=("", "날짜", "확인"), confirmed_values={})

    targetless = candidates[0].model_copy(update={"target": None})
    command = ImportCandidates.from_review((targetless,), labels=("단계",), confirmed_values={})
    with pytest.raises(EditRejected, match="다시 지정"):
        WorkflowEditingService().apply(workflow_with_steps(), command)


def test_double_click_can_round_trip_through_split_and_merge() -> None:
    double_click = ActionStep(
        step_id=STEP_ID,
        label="더블클릭",
        action_type="windows.double_click",
        target=TARGET,
    )
    split = WorkflowEditingService().apply(
        workflow_with_steps(double_click),
        SplitStep(step_id=STEP_ID),
    )
    merged = WorkflowEditingService().apply(
        split,
        MergeSteps(step_ids=tuple(step.step_id for step in split.steps), label="다시 합침"),
    )

    assert len(merged.steps) == 1
    assert isinstance(merged.steps[0], ActionStep)
    assert merged.steps[0].action_type == "windows.double_click"
    assert merged.steps[0].label == "다시 합침"


def test_merge_rejects_empty_selection_without_mutating_workflow() -> None:
    before = workflow_with_steps(text_step(LiteralValue(value="original")))

    with pytest.raises(EditRejected, match="합칠 단계"):
        WorkflowEditingService().apply(before, MergeSteps(step_ids=(), label=""))

    assert before.steps[0] == text_step(LiteralValue(value="original"))
