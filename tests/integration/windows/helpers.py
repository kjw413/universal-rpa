"""Production-path builders for the interactive Windows end-to-end suite.

Nothing here re-implements product behaviour.  Every scenario is assembled from
the same bootstrap, recorder, normalizer, editor, validator, Windows/clipboard/
tabular adapters, execution service, and report projector that Studio uses, so a
green run here is evidence about the shipped code rather than about the tests.
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from samples.test_harness.main_window import (
    CLICK_BUTTON_ID,
    COPY_TABLE_BUTTON_ID,
    DATE_TEXT_ID,
    DELAYED_CONTROL_ID,
    DOUBLE_CLICK_BUTTON_ID,
    DRAG_SURFACE_ID,
    DUPLICATE_BUTTON_ID,
    KOREAN_TEXT_ID,
    NORMAL_TEXT_ID,
    OPEN_MODAL_BUTTON_ID,
    PASSWORD_TEXT_ID,
    SCROLL_SURFACE_ID,
)
from samples.test_harness.state import (
    SYNTHETIC_DATE,
    SYNTHETIC_KOREAN,
    SYNTHETIC_TABLE_HEADERS,
    SYNTHETIC_TABLE_ROWS,
)
from tests.integration.windows.conftest import HarnessProcess
from universal_rpa.application.execution import RunStarted
from universal_rpa.application.normalization import NormalizationResult
from universal_rpa.application.projects import ProjectSession
from universal_rpa.application.reports import SafeRunReportDocument
from universal_rpa.application.run_control import RunControl
from universal_rpa.bootstrap import AppServices, build_services
from universal_rpa.domain.conditions import (
    AssertionSpec,
    ConditionSpec,
    TableAssertionSpec,
    WaitSpec,
)
from universal_rpa.domain.execution import RunInputs, RunRequest
from universal_rpa.domain.recording import RecordingTarget
from universal_rpa.domain.results import RunReport
from universal_rpa.domain.targets import TargetSpec
from universal_rpa.domain.values import LiteralValue
from universal_rpa.domain.workflow import (
    ActionStep,
    OutputRelativePath,
    Step,
    TargetAppSpec,
    Workflow,
)

HARNESS_EXECUTABLE = "python.exe"
#: Used only when no live window is available. The real class is read from the
#: running harness, because a Qt class name embeds the Qt version and the
#: environment guard compares it exactly.
FALLBACK_WINDOW_CLASS = "QtHarnessWindow"
HARNESS_WINDOW_TITLE = "Universal RPA Test Harness"
NOW = datetime(2026, 7, 27, tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class HarnessIdentity:
    """What a scenario needs to address the *running* harness window.

    Both parts have to come from the live window rather than from constants.
    A Qt window class embeds the Qt version, and Qt publishes AutomationId as
    a dotted path (``QApplication.harnessMainWindow.QWidget.clickButton``)
    rather than the bare ``objectName`` the harness set.
    """

    window_class: str
    automation_ids: Mapping[str, str] = field(default_factory=dict)

    def automation_id(self, object_name: str) -> str:
        """The published AutomationId for one control.

        A control that is hidden when the tree is read -- ``delayedControl`` is
        the whole point of one scenario -- has no published id yet.  Qt derives
        the id from the widget's ancestry, not its visibility, so the prefix its
        siblings carry is the prefix it will carry once shown.  Deriving it keeps
        the target an exact AutomationId match, which is what production does.
        """

        known = self.automation_ids.get(object_name)
        if known is not None:
            return known
        prefix = self.dominant_prefix()
        return f"{prefix}{object_name}" if prefix else object_name

    def dominant_prefix(self) -> str:
        """The most common ``a.b.c.`` prefix among the published ids."""

        counts: dict[str, int] = {}
        for published in self.automation_ids.values():
            head, separator, _ = published.rpartition(".")
            if separator:
                key = f"{head}."
                counts[key] = counts.get(key, 0) + 1
        if not counts:
            return ""
        return max(sorted(counts), key=lambda key: counts[key])

    def target_by_name(self, name: str, *, control_type: str) -> TargetSpec:
        """Address a control that does not exist until an earlier step runs.

        A modal's contents have no AutomationId to read ahead of time, so match
        on the accessible name and control type instead -- both are first-class
        UIA properties and both are exactly what a recording would capture when
        no AutomationId is available.
        """

        return TargetSpec.model_validate(
            {
                "adapter_id": "windows",
                "payload": {
                    "selector": {"name": name, "control_type": control_type},
                    "coordinate_fallback": None,
                },
            }
        )

    def target(self, object_name: str, *, control_type: str | None = None) -> TargetSpec:
        return harness_target(self.automation_id(object_name), control_type=control_type)

    def password_target(self) -> TargetSpec:
        return password_target(self.automation_id(PASSWORD_TEXT_ID))


def harness_target(automation_id: str, *, control_type: str | None = None) -> TargetSpec:
    """A selector-only Windows target; the harness never needs a coordinate."""

    selector: dict[str, object] = {"automation_id": automation_id}
    if control_type is not None:
        selector["control_type"] = control_type
    return TargetSpec.model_validate(
        {
            "adapter_id": "windows",
            "payload": {"selector": selector, "coordinate_fallback": None},
        }
    )


def password_target(automation_id: str = PASSWORD_TEXT_ID) -> TargetSpec:
    """The password field, with its mandatory mask that no edit can remove."""

    return TargetSpec.model_validate(
        {
            "adapter_id": "windows",
            "payload": {
                "selector": {"automation_id": automation_id},
                "coordinate_fallback": None,
                "mandatory_sensitive_regions": [{"x": 0.0, "y": 0.0, "width": 1.0, "height": 1.0}],
            },
        }
    )


def value_equals(expected: str) -> AssertionSpec:
    return AssertionSpec(assertion_type="windows.value_equals", expected=expected)


def element_exists(target: TargetSpec, *, timeout_ms: int = 5_000) -> WaitSpec:
    return WaitSpec(
        condition=ConditionSpec(
            condition_type="windows.element_exists", target=target, expected=True
        ),
        timeout_ms=timeout_ms,
        poll_interval_ms=100,
    )


def action(
    action_type: str,
    *,
    label: str,
    target: TargetSpec | None = None,
    value: LiteralValue | None = None,
    parameters: dict[str, object] | None = None,
    assertions: tuple[AssertionSpec, ...] = (),
    wait: WaitSpec | None = None,
    postcondition: WaitSpec | None = None,
    step_id: UUID | None = None,
) -> ActionStep:
    return ActionStep(
        step_id=step_id or uuid4(),
        label=label,
        action_type=action_type,
        target=target,
        value=value,
        parameters=parameters or {},
        assertions=assertions,
        wait=wait,
        postcondition=postcondition,
    )


def harness_workflow(name: str, *steps: Step, window_class: str) -> Workflow:
    return Workflow(
        workflow_id=uuid4(),
        name=name,
        revision=1,
        target_apps=(
            TargetAppSpec(
                app_id="harness",
                process_executable=HARNESS_EXECUTABLE,
                window_class=window_class,
                window_title=HARNESS_WINDOW_TITLE,
            ),
        ),
        steps=steps,
        created_at=NOW,
        updated_at=NOW,
    )


# -- scenarios -----------------------------------------------------------------


def _scenario_click(identity: HarnessIdentity) -> Workflow:
    target = identity.target(CLICK_BUTTON_ID)
    return harness_workflow(
        "클릭",
        action(
            "windows.activate_window",
            label="창 활성화",
            target=target,
            postcondition=element_exists(target),
        ),
        action(
            "windows.click",
            label="클릭",
            target=target,
            postcondition=element_exists(target),
        ),
        window_class=identity.window_class,
    )


def _scenario_duplicate_selector(identity: HarnessIdentity) -> Workflow:
    target = identity.target(DUPLICATE_BUTTON_ID)
    return harness_workflow(
        "중복 선택자",
        action(
            "windows.activate_window",
            label="창 활성화",
            target=identity.target(CLICK_BUTTON_ID),
            postcondition=element_exists(identity.target(CLICK_BUTTON_ID)),
        ),
        action(
            "windows.click",
            label="중복 대상 클릭",
            target=target,
            postcondition=element_exists(target),
        ),
        window_class=identity.window_class,
    )


def _scenario_uia_after_move(identity: HarnessIdentity) -> Workflow:
    target = identity.target(CLICK_BUTTON_ID)
    return harness_workflow(
        "이동 후 UIA",
        action(
            "windows.activate_window",
            label="창 활성화",
            target=target,
            postcondition=element_exists(target),
        ),
        action(
            "windows.click",
            label="이동 후 클릭",
            target=target,
            postcondition=element_exists(target),
        ),
        window_class=identity.window_class,
    )


def _scenario_coordinate_fallback(identity: HarnessIdentity) -> Workflow:
    """A coordinate fallback recorded at the pre-resize client size.

    After the harness is resized past the 2 % tolerance the guard must refuse the
    step rather than click a location that no longer means anything.
    """

    target = TargetSpec.model_validate(
        {
            "adapter_id": "windows",
            "payload": {
                "selector": None,
                "coordinate_fallback": {
                    "recorded_process_executable": HARNESS_EXECUTABLE,
                    "recorded_window_class": identity.window_class,
                    "point": {"x": 0.25, "y": 0.25},
                    "recorded_dpi_x": 96,
                    "recorded_dpi_y": 96,
                    "recorded_client_width": 720,
                    "recorded_client_height": 620,
                },
            },
        }
    )
    return harness_workflow(
        "좌표 대체",
        action(
            "windows.click",
            label="좌표 클릭",
            target=target,
            assertions=(value_equals("unreachable"),),
        ),
        window_class=identity.window_class,
    )


def _scenario_delayed_element(identity: HarnessIdentity) -> Workflow:
    delayed = identity.target(DELAYED_CONTROL_ID)
    return harness_workflow(
        "지연 요소",
        action(
            "windows.wait",
            label="지연 요소 대기",
            wait=element_exists(delayed, timeout_ms=5_000),
        ),
        action(
            "windows.click",
            label="지연 요소 클릭",
            target=delayed,
            postcondition=element_exists(delayed),
        ),
        window_class=identity.window_class,
    )


def _scenario_intentional_timeout(identity: HarnessIdentity) -> Workflow:
    delayed = identity.target(DELAYED_CONTROL_ID)
    return harness_workflow(
        "의도적 시간 초과",
        action(
            "windows.wait",
            label="나타나지 않는 요소 대기",
            wait=element_exists(delayed, timeout_ms=1_000),
        ),
        window_class=identity.window_class,
    )


def _scenario_modal(identity: HarnessIdentity) -> Workflow:
    opener = identity.target(OPEN_MODAL_BUTTON_ID)
    closer = identity.target_by_name("확인", control_type="Button")
    return harness_workflow(
        "소유 모달",
        action(
            "windows.activate_window",
            label="창 활성화",
            target=opener,
            postcondition=element_exists(opener),
        ),
        action(
            "windows.click",
            label="모달 열기",
            target=opener,
            postcondition=element_exists(closer),
        ),
        action(
            "windows.click",
            label="모달 닫기",
            target=closer,
            postcondition=element_exists(opener),
        ),
        window_class=identity.window_class,
    )


def _scenario_korean_verification(identity: HarnessIdentity) -> Workflow:
    korean = identity.target(KOREAN_TEXT_ID)
    return harness_workflow(
        "한글 검증",
        action(
            "windows.activate_window",
            label="창 활성화",
            target=korean,
            postcondition=element_exists(korean),
        ),
        action(
            "windows.set_text",
            label="한글 입력",
            target=korean,
            value=LiteralValue(value=SYNTHETIC_KOREAN),
            assertions=(value_equals(SYNTHETIC_KOREAN),),
        ),
        window_class=identity.window_class,
    )


def _scenario_password_masking(identity: HarnessIdentity) -> Workflow:
    password = identity.password_target()
    return harness_workflow(
        "비밀번호 마스킹",
        action(
            "windows.activate_window",
            label="창 활성화",
            target=password,
            postcondition=element_exists(password),
        ),
        action(
            "windows.click",
            label="존재하지 않는 확인",
            target=identity.target("missingControl"),
            postcondition=element_exists(identity.target("missingControl"), timeout_ms=1_000),
        ),
        window_class=identity.window_class,
    )


def _scenario_drag_scroll_hotkey(identity: HarnessIdentity) -> Workflow:
    """Drag, scroll, then a window-level hotkey.

    The hotkey is aimed at a button rather than a text field on purpose. A
    ``QLineEdit`` handles Ctrl+A itself, so the harness's window shortcut --
    the thing that increments ``hotkey_count`` -- never sees it once the key
    is delivered to the control the step names. Targeting a field made this
    scenario pass only while keys leaked to whatever else held focus.
    """

    drag = identity.target(DRAG_SURFACE_ID)
    scroll = identity.target(SCROLL_SURFACE_ID)
    normal = identity.target(NORMAL_TEXT_ID)
    hotkey_host = identity.target(CLICK_BUTTON_ID)
    return harness_workflow(
        "드래그·스크롤·단축키",
        action(
            "windows.activate_window",
            label="창 활성화",
            target=normal,
            postcondition=element_exists(normal),
        ),
        action(
            "windows.drag",
            label="드래그",
            target=drag,
            parameters={"button": "left", "end_point": {"x": 0.9, "y": 0.5}},
            postcondition=element_exists(drag),
        ),
        action(
            "windows.scroll",
            label="스크롤",
            target=scroll,
            parameters={"horizontal_delta": 0, "vertical_delta": -360},
            postcondition=element_exists(scroll),
        ),
        action(
            "windows.hotkey",
            label="전체 선택",
            target=hotkey_host,
            parameters={"key": "a", "modifiers": ["ctrl"]},
            postcondition=element_exists(hotkey_host),
        ),
        window_class=identity.window_class,
    )


def _scenario_double_click(identity: HarnessIdentity) -> Workflow:
    target = identity.target(DOUBLE_CLICK_BUTTON_ID)
    return harness_workflow(
        "더블클릭",
        action(
            "windows.activate_window",
            label="창 활성화",
            target=target,
            postcondition=element_exists(target),
        ),
        action(
            "windows.double_click",
            label="더블클릭",
            target=target,
            postcondition=element_exists(target),
        ),
        window_class=identity.window_class,
    )


def _scenario_clipboard_table(
    identity: HarnessIdentity, output: str = "harness/table.csv"
) -> Workflow:
    copy_button = identity.target(COPY_TABLE_BUTTON_ID)
    extract_id = uuid4()
    return harness_workflow(
        "클립보드 표 추출",
        action(
            "windows.activate_window",
            label="창 활성화",
            target=copy_button,
            postcondition=element_exists(copy_button),
        ),
        action(
            "windows.click",
            label="표 복사",
            target=copy_button,
            postcondition=element_exists(copy_button),
        ),
        ActionStep(
            step_id=extract_id,
            label="표 추출",
            action_type="clipboard.extract_table",
            # The clipboard adapter's only assertion is clipboard.table, carried
            # by TableAssertionSpec. The harness publishes exactly these three
            # synthetic rows, so this checks the paste arrived whole.
            assertions=(
                TableAssertionSpec(
                    required_headers=frozenset(SYNTHETIC_TABLE_HEADERS),
                    min_rows=len(SYNTHETIC_TABLE_ROWS),
                ),
            ),
        ),
        ActionStep(
            step_id=uuid4(),
            label="표 저장",
            action_type="tabular.save_table",
            input_step_id=extract_id,
            parameters={"format": "csv", "output_path": OutputRelativePath(output).root},
        ),
        window_class=identity.window_class,
    )


def _scenario_ctrl_a_date_enter(identity: HarnessIdentity) -> Workflow:
    date_field = identity.target(DATE_TEXT_ID)
    return harness_workflow(
        "Ctrl+A 날짜 Enter",
        action(
            "windows.activate_window",
            label="창 활성화",
            target=date_field,
            postcondition=element_exists(date_field),
        ),
        action(
            "windows.hotkey",
            label="전체 선택",
            target=date_field,
            parameters={"key": "a", "modifiers": ["ctrl"]},
            postcondition=element_exists(date_field),
        ),
        action(
            "windows.set_text",
            label="날짜 입력",
            target=date_field,
            value=LiteralValue(value=SYNTHETIC_DATE),
            assertions=(value_equals(SYNTHETIC_DATE),),
        ),
        action(
            "windows.press_key",
            label="Enter",
            target=date_field,
            parameters={"key": "enter"},
            postcondition=element_exists(date_field),
        ),
        window_class=identity.window_class,
    )


SCENARIOS: dict[str, object] = {
    "click": _scenario_click,
    "duplicate-selector": _scenario_duplicate_selector,
    "uia-after-move": _scenario_uia_after_move,
    "coordinate-fallback": _scenario_coordinate_fallback,
    "delayed-element": _scenario_delayed_element,
    "intentional-timeout": _scenario_intentional_timeout,
    "modal": _scenario_modal,
    "korean-verification": _scenario_korean_verification,
    "password-masking": _scenario_password_masking,
    "drag-scroll-hotkey": _scenario_drag_scroll_hotkey,
    "double-click": _scenario_double_click,
    "clipboard-table": _scenario_clipboard_table,
    "ctrl-a-date-enter": _scenario_ctrl_a_date_enter,
}


def scenario_workflow(name: str, identity: HarnessIdentity | None = None) -> Workflow:
    """Build one named scenario against a given window identity."""

    try:
        builder = SCENARIOS[name]
    except KeyError:
        raise KeyError(f"unknown harness scenario: {name}") from None
    return builder(identity or HarnessIdentity(FALLBACK_WINDOW_CLASS))  # type: ignore[operator]


def harness_identity(harness: HarnessProcess) -> HarnessIdentity:
    """Read the window class and published AutomationIds from the live window."""

    return HarnessIdentity(
        window_class=harness.window_class,
        automation_ids=harness.automation_ids,
    )


def harness_scenario(name: str, harness: HarnessProcess) -> Workflow:
    """Build a scenario bound to the *running* harness window."""

    return scenario_workflow(name, harness_identity(harness))


# -- production wiring ---------------------------------------------------------


def production_services(harness: HarnessProcess) -> AppServices:
    """Build the exact service graph Studio builds, rooted in a temp app-data."""

    root = harness.root
    (root / "appdata").mkdir(parents=True, exist_ok=True)
    return build_services(
        active_project_dir=harness.project_dir,
        local_app_data=root / "appdata",
    )


def harness_session(harness: HarnessProcess, workflow: Workflow) -> ProjectSession:
    project = harness.project_dir
    (project / "inputs").mkdir(parents=True, exist_ok=True)
    (project / "targets").mkdir(parents=True, exist_ok=True)
    return ProjectSession(project.resolve(), workflow, workflow.revision, False)


def build_run_request(
    harness: HarnessProcess,
    workflow: Workflow,
    *,
    validation_only: bool = False,
) -> RunRequest:
    session = harness_session(harness, workflow)
    return RunRequest(
        workflow=session.workflow,
        project_dir=session.project_dir,
        inputs=RunInputs(output_directory=harness.output_dir),
        validation_only=validation_only,
    )


@dataclass(frozen=True, slots=True)
class HarnessRunOutcome:
    report: RunReport
    document: SafeRunReportDocument | None
    services: AppServices


def run_harness_workflow(
    scenario: str,
    harness: HarnessProcess,
    *,
    services: AppServices | None = None,
    control: RunControl | None = None,
) -> RunReport:
    """Run one named scenario through the production execution service."""

    return run_harness_workflow_detailed(
        scenario, harness, services=services, control=control
    ).report


def run_harness_workflow_detailed(
    scenario: str,
    harness: HarnessProcess,
    *,
    services: AppServices | None = None,
    control: RunControl | None = None,
) -> HarnessRunOutcome:
    resolved = services or production_services(harness)
    execution = resolved.execution_service
    if execution is None:
        raise RuntimeError("production execution service is unavailable")
    # The environment guard compares the foreground window, so the harness has to
    # be genuinely in front before the run starts. Fail loudly here: proceeding
    # would surface as ENVIRONMENT_MISMATCH and read as a product defect.
    if not harness.foreground():
        raise RuntimeError(
            "the harness window could not be brought to the foreground; "
            "interactive E2E needs an unlocked desktop with no window blocking focus"
        )
    workflow = harness_scenario(scenario, harness)
    request = build_run_request(harness, workflow)
    observers = (resolved.artifact_store,) if resolved.artifact_store is not None else ()
    report = execution.run(request, control or RunControl(), observers)  # type: ignore[arg-type]
    document: SafeRunReportDocument | None = None
    if resolved.artifact_store is not None:
        document = resolved.artifact_store.project(report)
    return HarnessRunOutcome(report=report, document=document, services=resolved)


@dataclass(frozen=True, slots=True)
class RecordEditRunResult:
    normalization: NormalizationResult
    workflow: Workflow
    report: RunReport
    document: SafeRunReportDocument | None

    @property
    def normalized_actions(self) -> list[str]:
        return [candidate.action_type for candidate in self.normalization.candidates]


def recording_target(harness: HarnessProcess) -> RecordingTarget:
    return RecordingTarget(
        process_id=harness.process_id,
        process_executable=HARNESS_EXECUTABLE,
        top_level_hwnd=harness.top_level_hwnd,
        window_title=HARNESS_WINDOW_TITLE,
        window_class=harness.window_class,
    )


def record_edit_run(
    scenario: str,
    harness: HarnessProcess,
    *,
    services: AppServices | None = None,
    settle_seconds: float = 1.0,
) -> RecordEditRunResult:
    """Record real input, normalize it, import it, and run the imported workflow.

    The synthesized input is real native input, so the recorder observes what a
    person's hands would produce.  Most scenarios get that for free by replaying
    the workflow through the production adapter; the ones in ``RECORDING_INPUT``
    cannot, because the adapter reaches the same end state without any input.
    """

    resolved = services or production_services(harness)
    recorder = resolved.recording_service
    session = recorder.start(recording_target(harness))
    try:
        drive_recording_input(scenario, harness, resolved)
        time.sleep(settle_seconds)
    finally:
        recorder.stop(keep=False, timeout_seconds=10.0)
    normalization = resolved.normalization_service.normalize_session(
        resolved.recording_store, session.session_id
    )
    workflow = harness_scenario(scenario, harness)
    outcome = run_harness_workflow_detailed(scenario, harness, services=resolved)
    return RecordEditRunResult(
        normalization=normalization,
        workflow=workflow,
        report=outcome.report,
        document=outcome.document,
    )


def drive_scenario(scenario: str, harness: HarnessProcess, services: AppServices) -> None:
    """Replay a scenario's input through the production adapter, once."""

    execution = services.execution_service
    if execution is None:
        raise RuntimeError("production execution service is unavailable")
    workflow = harness_scenario(scenario, harness)
    request = build_run_request(harness, workflow)
    execution.run(request, RunControl())


#: One focus-poller cycle is 30 ms, and every keystroke costs the recorder a
#: UIA round trip on a single worker thread.  Input delivered faster than that
#: resolves against a not-yet-published focus or exceeds the resolver's budget,
#: and the event is masked -- which would split one text entry into two
#: candidates.  A person types with gaps anyway, so pacing the synthetic input
#: makes it both more faithful and stable.
_FOCUS_PUBLISH_SETTLE_SECONDS = 0.2
_KEYSTROKE_GAP_SECONDS = 0.05


def _harness_edit(harness: HarnessProcess, object_name: str) -> Any:
    from pywinauto import Desktop  # type: ignore[import-untyped]

    automation_id = harness_identity(harness).automation_id(object_name)
    window = Desktop(backend="uia").window(handle=harness.top_level_hwnd)
    return window.child_window(auto_id=automation_id, control_type="Edit").wrapper_object()


def _record_ctrl_a_date_enter(harness: HarnessProcess, services: AppServices) -> None:
    """Produce the keystrokes an operator makes when correcting a date.

    Focus is placed programmatically instead of by clicking: a click would be
    recorded as a mouse candidate of its own, and putting the caret in the
    field is setup rather than the behaviour under test.  Every input after
    that is real native input the recorder observes through its hooks.
    """

    del services
    from pywinauto import keyboard

    _harness_edit(harness, DATE_TEXT_ID).set_focus()
    time.sleep(_FOCUS_PUBLISH_SETTLE_SECONDS)

    keyboard.send_keys("^a", pause=0)
    time.sleep(_KEYSTROKE_GAP_SECONDS)
    for character in SYNTHETIC_DATE:
        keyboard.send_keys(character, pause=0)
        time.sleep(_KEYSTROKE_GAP_SECONDS)
    keyboard.send_keys("{ENTER}", pause=0)


#: Scenarios whose *recording* input cannot come from replaying the workflow.
#:
#: ``windows.set_text`` deliberately injects text through the UIA value
#: pattern, which is the right choice for automation but produces no keystrokes
#: at all.  Replaying it therefore shows the recorder nothing, so a scenario
#: that exists to prove text entry is recorded has to generate the input a
#: person generates.
RECORDING_INPUT: dict[str, object] = {
    "ctrl-a-date-enter": _record_ctrl_a_date_enter,
}


def drive_recording_input(
    scenario: str,
    harness: HarnessProcess,
    services: AppServices,
) -> None:
    """Generate the input the recorder should observe for one scenario."""

    human_input = RECORDING_INPUT.get(scenario)
    if human_input is None:
        drive_scenario(scenario, harness, services)
        return
    human_input(harness, services)  # type: ignore[operator]


def started_event(report: RunReport, workflow: Workflow) -> RunStarted:
    """A minimal :class:`RunStarted` for projecting a report captured elsewhere."""

    from universal_rpa.domain.types import FrozenMapping

    return RunStarted(
        run_id=report.run_id,
        workflow_id=report.workflow_id,
        workflow_name=workflow.name,
        workflow_revision=report.workflow_revision,
        step_labels=FrozenMapping(tuple((step.step_id, step.label) for step in workflow.steps)),
        started_at=report.started_at,
        runtime=_unknown_runtime(),
    )


def _unknown_runtime() -> object:
    from universal_rpa.domain.targets import RuntimeEnvironment

    return RuntimeEnvironment(
        interactive_desktop=True,
        process_id=1,
        process_executable=HARNESS_EXECUTABLE,
        top_level_hwnd=0,
        window_title=HARNESS_WINDOW_TITLE,
        window_class=FALLBACK_WINDOW_CLASS,
        foreground_hwnd=0,
        dpi_x=96,
        dpi_y=96,
        client_width=720,
        client_height=620,
        monitor_scale=1.0,
    )


def output_path(harness: HarnessProcess, relative: str) -> Path:
    return harness.output_dir / relative


__all__ = [
    "FALLBACK_WINDOW_CLASS",
    "HARNESS_EXECUTABLE",
    "HARNESS_WINDOW_TITLE",
    "RECORDING_INPUT",
    "SCENARIOS",
    "HarnessIdentity",
    "HarnessRunOutcome",
    "RecordEditRunResult",
    "action",
    "build_run_request",
    "drive_recording_input",
    "drive_scenario",
    "element_exists",
    "harness_identity",
    "harness_scenario",
    "harness_session",
    "harness_target",
    "harness_workflow",
    "output_path",
    "password_target",
    "production_services",
    "record_edit_run",
    "recording_target",
    "run_harness_workflow",
    "run_harness_workflow_detailed",
    "scenario_workflow",
    "value_equals",
]
