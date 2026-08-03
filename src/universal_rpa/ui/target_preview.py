from __future__ import annotations

from pathlib import Path
from typing import Protocol
from uuid import UUID

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap, QResizeEvent
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from universal_rpa.domain.targets import TargetSpec, WindowsTarget


class TargetPreviewResolver(Protocol):
    def resolve(
        self,
        project_dir: Path,
        step_id: UUID,
        target: TargetSpec,
    ) -> Path | None: ...


class MissingTargetPreviewResolver:
    def resolve(
        self,
        project_dir: Path,
        step_id: UUID,
        target: TargetSpec,
    ) -> Path | None:
        del project_dir, step_id, target
        return None


class TargetPreview(QWidget):
    def __init__(
        self,
        resolver: TargetPreviewResolver,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._resolver = resolver
        self.step_id: UUID | None = None
        self.preview_path: Path | None = None
        self._source = QPixmap()
        self._target: TargetSpec | None = None
        self.label = QLabel("미리보기 없음")
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.label.setMinimumSize(240, 180)
        self.label.setScaledContents(False)
        layout = QVBoxLayout(self)
        layout.addWidget(self.label, 1)

    def set_target(
        self,
        project_dir: Path,
        step_id: UUID,
        target: TargetSpec | None,
    ) -> None:
        self.step_id = step_id
        self.preview_path = None
        self._target = target
        self._source = QPixmap()
        if target is None:
            self.label.setPixmap(QPixmap())
            self.label.setText("미리보기 없음")
            return
        try:
            path = self._resolver.resolve(project_dir, step_id, target)
        except Exception:
            path = None
        try:
            resolved_targets = (project_dir / "targets").resolve(strict=True)
            resolved_path = path.resolve(strict=True) if path is not None else None
        except (OSError, RuntimeError):
            resolved_path = None
            resolved_targets = project_dir
        if (
            resolved_path is None
            or not resolved_path.is_relative_to(resolved_targets)
            or not resolved_path.is_file()
            or resolved_path.is_symlink()
        ):
            self.label.setPixmap(QPixmap())
            self.label.setText("미리보기 없음")
            return
        path = resolved_path
        pixmap = QPixmap(str(path))
        if pixmap.isNull():
            self.label.setPixmap(QPixmap())
            self.label.setText("미리보기 없음")
            return
        self.preview_path = path
        self._source = self._with_region_overlay(pixmap, target)
        self.label.setText("")
        self._refresh_scaled()

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._refresh_scaled()

    def _refresh_scaled(self) -> None:
        if self._source.isNull():
            return
        size = self.label.size()
        self.label.setPixmap(
            self._source.scaled(
                size,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )

    @staticmethod
    def _with_region_overlay(pixmap: QPixmap, target: TargetSpec) -> QPixmap:
        if target.adapter_id != "windows":
            return pixmap
        try:
            windows = WindowsTarget.model_validate(target.payload)
        except Exception:
            return pixmap
        region = windows.target_region
        if region is None:
            return pixmap
        rendered = QPixmap(pixmap)
        painter = QPainter(rendered)
        painter.setPen(QPen(QColor("#00A3FF"), 3))
        painter.drawRect(
            round(region.x * rendered.width()),
            round(region.y * rendered.height()),
            max(1, round(region.width * rendered.width())),
            max(1, round(region.height * rendered.height())),
        )
        painter.end()
        return rendered


__all__ = ["MissingTargetPreviewResolver", "TargetPreview", "TargetPreviewResolver"]
