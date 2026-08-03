from __future__ import annotations

from pathlib import Path
from uuid import UUID

from PySide6.QtGui import QColor, QImage

from universal_rpa.domain.targets import TargetSpec
from universal_rpa.ui.target_preview import TargetPreview

STEP_ID = UUID("00000000-0000-0000-0000-000000000851")


class PreviewResolver:
    def __init__(self, path: Path | None) -> None:
        self.path = path
        self.calls: list[tuple[Path, UUID, TargetSpec]] = []

    def resolve(self, project_dir: Path, step_id: UUID, target: TargetSpec) -> Path | None:
        self.calls.append((project_dir, step_id, target))
        return self.path


def windows_target() -> TargetSpec:
    return TargetSpec.model_validate(
        {
            "adapter_id": "windows",
            "payload": {
                "selector": {"automation_id": "submit"},
                "coordinate_fallback": None,
                "target_region": {"x": 0.2, "y": 0.2, "width": 0.5, "height": 0.4},
            },
        }
    )


def test_preview_resolves_only_project_target_path(qtbot: object, tmp_path: Path) -> None:
    targets = tmp_path / "targets"
    targets.mkdir()
    path = targets / "preview.png"
    image = QImage(100, 80, QImage.Format.Format_RGB32)
    image.fill(QColor("white"))
    assert image.save(str(path), "PNG")
    resolver = PreviewResolver(path)
    preview = TargetPreview(resolver)
    qtbot.addWidget(preview)  # type: ignore[attr-defined]

    preview.set_target(tmp_path, STEP_ID, windows_target())

    assert preview.preview_path == path
    assert preview.step_id == STEP_ID
    assert not preview.label.pixmap().isNull()
    assert resolver.calls == [(tmp_path, STEP_ID, windows_target())]


def test_missing_or_outside_preview_shows_safe_message(qtbot: object, tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside.png"
    image = QImage(10, 10, QImage.Format.Format_RGB32)
    image.fill(QColor("red"))
    assert image.save(str(outside), "PNG")
    preview = TargetPreview(PreviewResolver(outside))
    qtbot.addWidget(preview)  # type: ignore[attr-defined]

    preview.set_target(tmp_path, STEP_ID, windows_target())

    assert preview.preview_path is None
    assert preview.label.text() == "미리보기 없음"
