from __future__ import annotations

from pathlib import Path
from uuid import UUID

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QPoint
from PySide6.QtGui import QColor, QImage

from universal_rpa.domain.targets import TargetSpec, WindowsTarget
from universal_rpa.infrastructure.target_preview_store import TargetPreviewStore
from universal_rpa.ports.automation import TargetCaptureResult

STEP_ID = UUID("00000000-0000-0000-0000-000000000871")


def target() -> TargetSpec:
    return TargetSpec.model_validate(
        {
            "adapter_id": "windows",
            "payload": {
                "selector": None,
                "coordinate_fallback": {
                    "recorded_process_executable": "erp.exe",
                    "recorded_window_class": "ERPMain",
                    "point": {"x": 0.5, "y": 0.5},
                    "recorded_dpi_x": 96,
                    "recorded_dpi_y": 96,
                    "recorded_client_width": 20,
                    "recorded_client_height": 20,
                },
                "target_region": {"x": 0.0, "y": 0.0, "width": 1.0, "height": 1.0},
                "mandatory_sensitive_regions": ({"x": 0.0, "y": 0.0, "width": 0.5, "height": 0.5},),
            },
        }
    )


def png_bytes() -> bytes:
    image = QImage(20, 20, QImage.Format.Format_RGB32)
    image.fill(QColor("red"))
    payload = QByteArray()
    buffer = QBuffer(payload)
    assert buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    assert image.save(buffer, "PNG")
    buffer.close()
    return bytes(payload) + b"UNMASKED_SENTINEL"


def test_unmasked_capture_bytes_never_reach_project_disk(tmp_path: Path) -> None:
    capture = TargetCaptureResult(
        target=target(),
        candidates=(target(),),
        preview_png=png_bytes(),
    )
    store = TargetPreviewStore()

    staged = store.stage_masked(tmp_path, STEP_ID, capture)
    disk_bytes = b"".join(path.read_bytes() for path in (tmp_path / "targets").iterdir())
    image = QImage(str(staged.path))

    assert staged.path.is_relative_to((tmp_path / "targets").resolve())
    assert b"UNMASKED_SENTINEL" not in disk_bytes
    assert image.pixelColor(QPoint(2, 2)) == QColor("black")


def test_commit_and_resolve_use_target_hash_variant(tmp_path: Path) -> None:
    selected = target()
    capture = TargetCaptureResult(
        target=selected,
        candidates=(selected,),
        preview_png=png_bytes(),
    )
    store = TargetPreviewStore()
    variant = store.stage_masked(tmp_path, STEP_ID, capture)

    store.commit_variant(variant)

    assert not variant.path.exists()
    assert store.resolve(tmp_path, STEP_ID, selected) == variant.final_path


def test_secret_promotion_masks_entire_target_region_before_workflow_save(
    tmp_path: Path,
) -> None:
    selected = target()
    capture = TargetCaptureResult(
        target=selected,
        candidates=(selected,),
        preview_png=png_bytes(),
    )
    store = TargetPreviewStore()
    original = store.stage_masked(tmp_path, STEP_ID, capture)
    store.commit_variant(original)
    assert QImage(str(original.final_path)).pixelColor(QPoint(15, 15)) == QColor("red")

    secured_target, secured = store.stage_secret_mask(tmp_path, STEP_ID, selected)
    assert secured is not None
    store.commit_variant(secured)

    parsed = WindowsTarget.model_validate(secured_target.payload)
    assert parsed.target_region in parsed.mandatory_sensitive_regions
    secured_path = store.resolve(tmp_path, STEP_ID, secured_target)
    assert secured_path is not None
    assert QImage(str(secured_path)).pixelColor(QPoint(15, 15)) == QColor("black")
