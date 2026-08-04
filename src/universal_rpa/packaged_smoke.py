"""An end-to-end smoke that the *packaged* application runs against itself.

The point of this module is that importing successfully proves nothing about a
packaged build.  So the smoke creates a real ``QApplication`` and the production
``MainWindow``, bootstraps the three real adapters, persists and reloads a
synthetic workflow through the production repository, validates it, executes it
with the production ``ExecutionService``, and projects a safe report.  It never
substitutes a test double for a product component.

Like the self-check, its report is path-free: it goes into a sign-off record.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from universal_rpa.self_check import BUILTIN_ADAPTER_IDS

#: The one action the synthetic workflow performs — a short, harmless wait.
SMOKE_ACTION = "windows.wait"
SMOKE_WAIT_MS = 50


class SmokeRejected(RuntimeError):
    """The supplied smoke root is unusable, so nothing was created or run."""


@dataclass(frozen=True, slots=True)
class PackagedSmokeReport:
    ok: bool
    main_window_created: bool
    adapter_ids: tuple[str, ...]
    workflow_action: str
    workflow_round_tripped: bool
    validation_error_count: int
    run_status: str
    safe_report_created: bool

    def to_json(self) -> str:
        return json.dumps(
            {
                "ok": self.ok,
                "main_window_created": self.main_window_created,
                "adapter_ids": list(self.adapter_ids),
                "workflow_action": self.workflow_action,
                "workflow_round_tripped": self.workflow_round_tripped,
                "validation_error_count": self.validation_error_count,
                "run_status": self.run_status,
                "safe_report_created": self.safe_report_created,
            },
            ensure_ascii=False,
            indent=2,
        )


def _is_link_like(path: Path) -> bool:
    if path.is_symlink():
        return True
    is_junction = getattr(path, "is_junction", None)
    if is_junction is not None and is_junction():
        return True
    try:
        attributes = getattr(path.lstat(), "st_file_attributes", 0)
    except OSError:
        return False
    return bool(attributes & 0x400)


def _require_empty_root(root: Path) -> Path:
    candidate = Path(root)
    if not candidate.is_dir():
        raise SmokeRejected("the smoke root must be an existing directory")
    if _is_link_like(candidate):
        raise SmokeRejected("the smoke root must not be a link or reparse point")
    resolved = candidate.resolve()
    if _is_link_like(resolved):
        raise SmokeRejected("the smoke root must not resolve through a reparse point")
    if any(resolved.iterdir()):
        raise SmokeRejected("the smoke root must be empty")
    return resolved


def _current_runtime() -> object:
    """Snapshot the foreground window the preflight guard will compare against."""

    from universal_rpa.adapters.windows.environment import WindowsEnvironmentProbe

    probe = WindowsEnvironmentProbe()
    try:
        return probe.snapshot(probe.foreground_hwnd())
    except Exception:
        raise SmokeRejected("no foreground window is available to smoke against") from None


def _synthetic_workflow(runtime: object) -> object:
    """Build a one-wait workflow whose target app is the live foreground window.

    The smoke deliberately points the workflow at the window it is running in
    front of.  Anything else would be refused by the environment guard, and the
    point of the smoke is to prove that guard passes when it should — not to
    bypass it.
    """

    from universal_rpa.domain.conditions import ConditionSpec, WaitSpec
    from universal_rpa.domain.targets import TargetSpec
    from universal_rpa.domain.workflow import ActionStep, TargetAppSpec, Workflow

    now = datetime.now(UTC)
    target = TargetSpec.model_validate(
        {
            "adapter_id": "windows",
            "payload": {
                "selector": {"automation_id": "packagedSmokeProbe"},
                "coordinate_fallback": None,
            },
        }
    )
    return Workflow(
        workflow_id=uuid4(),
        name="포장 점검",
        revision=0,
        target_apps=(
            TargetAppSpec(
                app_id="smoke",
                process_executable=runtime.process_executable,  # type: ignore[attr-defined]
                window_class=runtime.window_class,  # type: ignore[attr-defined]
            ),
        ),
        steps=(
            ActionStep(
                step_id=uuid4(),
                label="짧은 대기",
                action_type=SMOKE_ACTION,
                wait=WaitSpec(
                    condition=ConditionSpec(
                        condition_type="windows.fixed_delay",
                        target=target,
                        expected=True,
                    ),
                    timeout_ms=SMOKE_WAIT_MS,
                    poll_interval_ms=10,
                ),
            ),
        ),
        created_at=now,
        updated_at=now,
    )


def run_packaged_smoke(root: Path) -> PackagedSmokeReport:
    """Exercise the real window, adapters, repository, validator, and runner."""

    from PySide6.QtWidgets import QApplication

    from universal_rpa.application.run_control import RunControl
    from universal_rpa.bootstrap import build_services
    from universal_rpa.domain.execution import RunInputs, RunRequest
    from universal_rpa.infrastructure.json_repository import JsonWorkflowRepository
    from universal_rpa.ui.main_window import MainWindow

    smoke_root = _require_empty_root(root)
    project_dir = smoke_root / "project"
    output_dir = smoke_root / "output"
    app_data = smoke_root / "appdata"
    for directory in (project_dir / "inputs", project_dir / "targets", output_dir, app_data):
        directory.mkdir(parents=True, exist_ok=True)

    application = QApplication.instance() or QApplication([])
    services = build_services(active_project_dir=project_dir, local_app_data=app_data)
    window = MainWindow(services)
    main_window_created = window.page_count() == 5

    repository = JsonWorkflowRepository()
    workflow = _synthetic_workflow(_current_runtime())
    saved = repository.save(project_dir, workflow, expected_revision=0)  # type: ignore[arg-type]
    reloaded = repository.load(project_dir)
    round_tripped = (
        reloaded.workflow_id == saved.workflow_id and reloaded.revision == saved.revision
    )

    validation = services.validation_service.validate_static(reloaded)
    execution = services.execution_service
    if execution is None:
        raise SmokeRejected("the packaged build registered no execution service")
    request = RunRequest(
        workflow=reloaded,
        project_dir=project_dir,
        inputs=RunInputs(output_directory=output_dir),
    )
    report = execution.run(request, RunControl())
    document = services.artifact_store.project(report) if services.artifact_store else None

    window.close()
    application.processEvents()

    ok = (
        main_window_created
        and round_tripped
        and not validation.errors
        and report.status == "success"
        and document is not None
    )
    return PackagedSmokeReport(
        ok=ok,
        main_window_created=main_window_created,
        adapter_ids=tuple(sorted(services.adapter_registry.adapter_ids())),
        workflow_action=reloaded.steps[0].action_type,  # type: ignore[union-attr]
        workflow_round_tripped=round_tripped,
        validation_error_count=len(validation.errors),
        run_status=report.status,
        safe_report_created=document is not None,
    )


def distribution_file_names() -> frozenset[str]:
    """Top-level names a correct wheel/standalone distribution may contain."""

    from universal_rpa import __file__ as package_file

    package_root = Path(package_file).resolve().parent
    return frozenset({package_root.name, *(entry.name for entry in package_root.iterdir())})


__all__ = [
    "BUILTIN_ADAPTER_IDS",
    "SMOKE_ACTION",
    "PackagedSmokeReport",
    "SmokeRejected",
    "distribution_file_names",
    "run_packaged_smoke",
]
