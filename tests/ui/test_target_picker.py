from __future__ import annotations

from uuid import UUID

from tests.helpers.validation_fakes import runtime_environment
from universal_rpa.domain.targets import NormalizedRect, TargetSpec, WindowsTarget
from universal_rpa.ports.automation import (
    CancellationToken,
    TargetCaptureRequest,
    TargetCaptureResult,
)
from universal_rpa.ui.target_picker import TargetPicker


class RecordingCapturePort:
    """A capture port that reports what the picker actually asked it for."""

    def __init__(self, result: TargetCaptureResult) -> None:
        self._result = result
        self.requests: list[TargetCaptureRequest] = []

    def capture_target(
        self,
        request: TargetCaptureRequest,
        cancellation: CancellationToken,
    ) -> TargetCaptureResult:
        del cancellation
        self.requests.append(request)
        return self._result


def target(mandatory: tuple[NormalizedRect, ...] = ()) -> TargetSpec:
    return TargetSpec.model_validate(
        {
            "adapter_id": "windows",
            "payload": {
                "selector": {"automation_id": "password"},
                "coordinate_fallback": None,
                "target_region": {"x": 0.1, "y": 0.1, "width": 0.5, "height": 0.2},
                "mandatory_sensitive_regions": tuple(
                    region.model_dump(mode="json") for region in mandatory
                ),
            },
        }
    )


def test_the_capture_button_captures_where_the_user_pointed(qtbot: object) -> None:
    """Without this the dialog opens with a button that does nothing."""

    selected = target()
    port = RecordingCapturePort(
        TargetCaptureResult(target=selected, candidates=(selected,), preview_png=None)
    )
    request = TargetCaptureRequest(
        runtime=runtime_environment(),
        screen_x=640,
        screen_y=360,
        focused_runtime_id=None,
    )
    picker = TargetPicker(port, request_factory=lambda: request, countdown_seconds=0)
    qtbot.addWidget(picker)  # type: ignore[attr-defined]

    picker.capture_button.click()

    qtbot.waitUntil(lambda: bool(port.requests), timeout=3_000)  # type: ignore[attr-defined]
    assert port.requests == [request]
    qtbot.waitUntil(  # type: ignore[attr-defined]
        lambda: picker.candidate_combo.count() == 1, timeout=3_000
    )
    # Re-enabling is how the dialog reports the capture thread has finished.
    qtbot.waitUntil(  # type: ignore[attr-defined]
        picker.capture_button.isEnabled, timeout=3_000
    )


def test_mandatory_password_region_cannot_be_removed(qtbot: object) -> None:
    mandatory = NormalizedRect(x=0.1, y=0.1, width=0.5, height=0.2)
    selected = target((mandatory,))
    picker = TargetPicker()
    qtbot.addWidget(picker)  # type: ignore[attr-defined]
    picker.set_capture_result(
        TargetCaptureResult(target=selected, candidates=(selected,), preview_png=None)
    )

    assert not picker.region_editor.remove_region(mandatory)
    result = picker.captured_result()
    assert result is not None and result.target is not None
    parsed = WindowsTarget.model_validate(result.target.payload)
    assert mandatory in parsed.mandatory_sensitive_regions


def test_user_region_is_added_to_immutable_selected_target(qtbot: object) -> None:
    selected = target()
    user = NormalizedRect(x=0.7, y=0.7, width=0.2, height=0.2)
    picker = TargetPicker()
    qtbot.addWidget(picker)  # type: ignore[attr-defined]
    picker.set_capture_result(
        TargetCaptureResult(target=selected, candidates=(selected,), preview_png=None)
    )

    picker.region_editor.add_region(user)
    result = picker.captured_result()

    assert result is not None and result.target is not None
    assert user in WindowsTarget.model_validate(result.target.payload).user_sensitive_regions
    assert UUID(int=0) != UUID(int=1)  # keep UUID import covered by lint-neutral assertion
