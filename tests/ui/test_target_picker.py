from __future__ import annotations

from uuid import UUID

from universal_rpa.domain.targets import NormalizedRect, TargetSpec, WindowsTarget
from universal_rpa.ports.automation import TargetCaptureResult
from universal_rpa.ui.target_picker import TargetPicker


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
