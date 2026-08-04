"""Per-run artifacts store only safe JSON and capture screenshots on failure."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from universal_rpa.application.execution import RunActionObserved, RunStarted
from universal_rpa.application.reports import ReportProjector
from universal_rpa.domain.errors import ErrorCode
from universal_rpa.domain.results import ActionResult, LoopCursor, RunReport
from universal_rpa.domain.targets import RuntimeEnvironment, TargetSpec
from universal_rpa.domain.types import FrozenMapping
from universal_rpa.infrastructure.artifact_store import RunArtifactStore

RUN_ID = UUID("00000000-0000-0000-0000-000000000801")
WORKFLOW_ID = UUID("00000000-0000-0000-0000-000000000802")
STEP_ID = UUID("00000000-0000-0000-0000-000000000803")
LOOP_ID = UUID("00000000-0000-0000-0000-000000000804")
STARTED_AT = datetime(2026, 8, 3, 9, 0, tzinfo=UTC)
FINISHED_AT = datetime(2026, 8, 3, 9, 5, tzinfo=UTC)


def runtime_environment() -> RuntimeEnvironment:
    return RuntimeEnvironment(
        interactive_desktop=True,
        process_id=41,
        process_executable="mis.exe",
        top_level_hwnd=901,
        window_title="MIS",
        window_class="MisMainWindow",
        foreground_hwnd=901,
        dpi_x=96,
        dpi_y=96,
        client_width=200,
        client_height=100,
        monitor_scale=1.0,
    )


def windows_target() -> TargetSpec:
    return TargetSpec.model_validate(
        {
            "adapter_id": "windows",
            "payload": {
                "selector": {"automation_id": "grid"},
                "coordinate_fallback": None,
            },
        }
    )


def run_started() -> RunStarted:
    return RunStarted(
        run_id=RUN_ID,
        workflow_id=WORKFLOW_ID,
        workflow_name="월간 실적 수집",
        workflow_revision=4,
        step_labels=FrozenMapping(((STEP_ID, "실적 표 추출"),)),
        started_at=STARTED_AT,
        runtime=runtime_environment(),
    )


def success_result() -> ActionResult:
    return ActionResult(
        run_id=RUN_ID,
        step_id=STEP_ID,
        iteration_cursor=(LoopCursor(loop_step_id=LOOP_ID, row_index=0),),
        status="success",
        started_at=STARTED_AT,
        evidence=FrozenMapping((("row_count", 3),)),
    )


def failed_result() -> ActionResult:
    return ActionResult(
        run_id=RUN_ID,
        step_id=STEP_ID,
        iteration_cursor=(LoopCursor(loop_step_id=LOOP_ID, row_index=1),),
        status="failed",
        started_at=STARTED_AT,
        error_code=ErrorCode.TARGET_NOT_FOUND,
        safe_message="대상을 찾을 수 없습니다.",
    )


def observed_action(*, result: ActionResult) -> RunActionObserved:
    return RunActionObserved(
        result=result, target=windows_target(), runtime=runtime_environment()
    )


def run_report(*, results: tuple[ActionResult, ...], status: str) -> RunReport:
    return RunReport(
        run_id=RUN_ID,
        workflow_id=WORKFLOW_ID,
        workflow_revision=4,
        status=status,  # type: ignore[arg-type]
        started_at=STARTED_AT,
        finished_at=FINISHED_AT,
        error_code=None if status in {"success", "partial"} else ErrorCode.TARGET_NOT_FOUND,
        safe_message="" if status in {"success", "partial"} else "대상을 찾을 수 없습니다.",
        results=results,
        completed_iterations=len(results),
    )


class SpyScreenCapture:
    def __init__(self, *, destination_written: bool = True) -> None:
        self.calls: list[tuple[TargetSpec | None, Path]] = []
        self._destination_written = destination_written

    def capture_failure(
        self,
        target: TargetSpec | None,
        expected_runtime: RuntimeEnvironment,
        destination: Path,
    ) -> Path | None:
        del expected_runtime
        self.calls.append((target, destination))
        if not self._destination_written:
            return None
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"\x89PNG\r\n\x1a\n")
        return destination


def run_artifact_store(
    root: Path,
    *,
    capture: SpyScreenCapture | None = None,
) -> RunArtifactStore:
    return RunArtifactStore(
        root=root,
        projector=ReportProjector(),
        screenshots=capture,
    )


def test_success_result_never_captures_screenshot(tmp_path: Path) -> None:
    capture = SpyScreenCapture()
    store = run_artifact_store(tmp_path, capture=capture)

    store.on_run_started(run_started())
    store.on_action_result(observed_action(result=success_result()))

    assert capture.calls == []


def test_failure_result_captures_one_screenshot_per_failure(tmp_path: Path) -> None:
    capture = SpyScreenCapture()
    store = run_artifact_store(tmp_path, capture=capture)

    store.on_run_started(run_started())
    store.on_action_result(observed_action(result=failed_result()))

    assert len(capture.calls) == 1
    target, destination = capture.calls[0]
    assert target == windows_target()
    assert destination.parent == tmp_path / str(WORKFLOW_ID) / str(RUN_ID)


def test_report_is_written_as_safe_json_beneath_the_run_directory(tmp_path: Path) -> None:
    store = run_artifact_store(tmp_path)
    store.on_run_started(run_started())
    report = run_report(results=(success_result(),), status="success")

    store.on_run_finished(report)

    path = store.report_path(RUN_ID)
    assert path == tmp_path / str(WORKFLOW_ID) / str(RUN_ID) / "report.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    assert document["run_id"] == str(RUN_ID)
    assert document["workflow_name"] == "월간 실적 수집"
    assert document["successful_iterations"] == 1


def test_report_written_without_a_started_event_is_still_addressable(tmp_path: Path) -> None:
    store = run_artifact_store(tmp_path)

    store.on_run_finished(run_report(results=(success_result(),), status="success"))

    path = store.report_path(RUN_ID)
    assert path.is_file()
    assert path.parent.parent.name == str(WORKFLOW_ID)


def test_observer_failures_never_propagate_into_the_run(tmp_path: Path) -> None:
    class ExplodingCapture(SpyScreenCapture):
        def capture_failure(
            self,
            target: TargetSpec | None,
            expected_runtime: RuntimeEnvironment,
            destination: Path,
        ) -> Path | None:
            raise OSError("capture device unavailable")

    store = run_artifact_store(tmp_path, capture=ExplodingCapture())
    store.on_run_started(run_started())

    store.on_action_result(observed_action(result=failed_result()))

    assert store.screenshot_paths == ()


def test_stored_report_contains_no_window_title_or_hwnd(tmp_path: Path) -> None:
    store = run_artifact_store(tmp_path)
    store.on_run_started(run_started())

    store.on_run_finished(run_report(results=(failed_result(),), status="failed"))

    encoded = store.report_path(RUN_ID).read_text(encoding="utf-8")
    assert "901" not in encoded
    assert "MIS" not in encoded
