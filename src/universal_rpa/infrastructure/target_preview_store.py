from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

from PySide6.QtCore import QRect
from PySide6.QtGui import QColor, QImage, QPainter

from universal_rpa.domain.targets import NormalizedRect, TargetSpec, WindowsTarget
from universal_rpa.ports.automation import TargetCaptureResult


class TargetPreviewStoreError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class MaskedPreviewVariant:
    path: Path
    target_sha256: str
    final_path: Path
    step_id: UUID


class TargetPreviewStore:
    def stage_masked(
        self,
        project_dir: Path,
        step_id: UUID,
        capture: TargetCaptureResult,
    ) -> MaskedPreviewVariant:
        target = capture.target
        payload = capture.preview_png
        if target is None or payload is None or target.adapter_id != "windows":
            raise TargetPreviewStoreError("저장할 수 있는 Windows 대상 미리보기가 없습니다.")
        try:
            windows = WindowsTarget.model_validate(target.payload)
        except Exception:
            raise TargetPreviewStoreError("대상 미리보기 정보가 올바르지 않습니다.") from None
        fallback = windows.coordinate_fallback
        if fallback is None or windows.target_region is None:
            raise TargetPreviewStoreError("미리보기의 크기 또는 대상 영역을 확인할 수 없습니다.")
        targets_dir = self._safe_targets_dir(project_dir)
        image = QImage.fromData(payload)
        if image.isNull() or (image.width(), image.height()) != (
            fallback.recorded_client_width,
            fallback.recorded_client_height,
        ):
            raise TargetPreviewStoreError("미리보기 크기가 기록 환경과 일치하지 않습니다.")
        for region in windows.masking_regions:
            self._mask(image, region)

        digest = self._target_hash(target)
        final_path = targets_dir / f"{step_id}-{digest[:16]}.png"
        staged_path = targets_dir / f".stage-{step_id}-{uuid4().hex}.png"
        if not image.save(str(staged_path)):
            raise TargetPreviewStoreError("마스킹된 미리보기를 저장하지 못했습니다.")
        return MaskedPreviewVariant(staged_path, digest, final_path, step_id)

    def stage_secret_mask(
        self,
        project_dir: Path,
        step_id: UUID,
        target: TargetSpec,
    ) -> tuple[TargetSpec, MaskedPreviewVariant | None]:
        if target.adapter_id != "windows":
            return target, None
        try:
            windows = WindowsTarget.model_validate(target.payload)
        except Exception:
            raise TargetPreviewStoreError("비밀값 대상 정보가 올바르지 않습니다.") from None
        region = windows.target_region
        if region is None:
            raise TargetPreviewStoreError("비밀값 입력 영역을 확인할 수 없습니다.")
        mandatory = tuple(dict.fromkeys((*windows.mandatory_sensitive_regions, region)))
        secured_windows = windows.model_copy(update={"mandatory_sensitive_regions": mandatory})
        secured_target = TargetSpec.model_validate(
            {"adapter_id": "windows", "payload": secured_windows.model_dump(mode="json")}
        )
        current = self.resolve(project_dir, step_id, target)
        if current is None:
            return secured_target, None
        image = QImage(str(current))
        fallback = secured_windows.coordinate_fallback
        if (
            image.isNull()
            or fallback is None
            or (image.width(), image.height())
            != (fallback.recorded_client_width, fallback.recorded_client_height)
        ):
            raise TargetPreviewStoreError("기존 미리보기를 안전하게 다시 마스킹할 수 없습니다.")
        self._mask(image, region)
        variant = self._stage_image(project_dir, step_id, secured_target, image)
        return secured_target, variant

    def delete_variants(self, project_dir: Path, step_id: UUID) -> None:
        targets_dir = self._safe_targets_dir(project_dir)
        for candidate in targets_dir.glob(f"{step_id}-*.png"):
            if self._is_link_like(candidate):
                raise TargetPreviewStoreError("연결된 미리보기 파일을 삭제할 수 없습니다.")
            candidate.unlink(missing_ok=True)

    def resolve(
        self,
        project_dir: Path,
        step_id: UUID,
        target: TargetSpec,
    ) -> Path | None:
        try:
            targets_dir = self._safe_targets_dir(project_dir)
            candidate = targets_dir / f"{step_id}-{self._target_hash(target)[:16]}.png"
            resolved = candidate.resolve(strict=True)
        except (OSError, TargetPreviewStoreError):
            return None
        if not resolved.is_relative_to(targets_dir) or self._is_link_like(resolved):
            return None
        return resolved if resolved.is_file() else None

    def commit_variant(self, variant: MaskedPreviewVariant) -> None:
        if not variant.path.is_file() or self._is_link_like(variant.path):
            raise TargetPreviewStoreError("준비된 미리보기를 찾을 수 없습니다.")
        if variant.path.parent != variant.final_path.parent:
            raise TargetPreviewStoreError("미리보기 경계가 올바르지 않습니다.")
        os.replace(variant.path, variant.final_path)
        for candidate in variant.final_path.parent.glob(f"{variant.step_id}-*.png"):
            if candidate != variant.final_path and not self._is_link_like(candidate):
                candidate.unlink(missing_ok=True)

    def discard_variant(self, variant: MaskedPreviewVariant) -> None:
        try:
            if not self._is_link_like(variant.path):
                variant.path.unlink(missing_ok=True)
        except OSError:
            pass

    def _stage_image(
        self,
        project_dir: Path,
        step_id: UUID,
        target: TargetSpec,
        image: QImage,
    ) -> MaskedPreviewVariant:
        targets_dir = self._safe_targets_dir(project_dir)
        digest = self._target_hash(target)
        final_path = targets_dir / f"{step_id}-{digest[:16]}.png"
        staged_path = targets_dir / f".stage-{step_id}-{uuid4().hex}.png"
        if not image.save(str(staged_path)):
            raise TargetPreviewStoreError("마스킹된 미리보기를 저장하지 못했습니다.")
        return MaskedPreviewVariant(staged_path, digest, final_path, step_id)

    @staticmethod
    def _mask(image: QImage, region: NormalizedRect) -> None:
        rectangle = QRect(
            round(region.x * image.width()),
            round(region.y * image.height()),
            max(1, round(region.width * image.width())),
            max(1, round(region.height * image.height())),
        )
        painter = QPainter(image)
        painter.fillRect(rectangle, QColor("black"))
        painter.end()

    @classmethod
    def _safe_targets_dir(cls, project_dir: Path) -> Path:
        project = Path(project_dir)
        targets = project / "targets"
        if cls._is_link_like(project) or cls._is_link_like(targets):
            raise TargetPreviewStoreError("연결된 프로젝트 경로에는 미리보기를 저장할 수 없습니다.")
        try:
            resolved_project = project.resolve(strict=True)
            targets.mkdir(exist_ok=True)
            resolved_targets = targets.resolve(strict=True)
        except OSError:
            raise TargetPreviewStoreError("프로젝트 targets 폴더를 사용할 수 없습니다.") from None
        if not resolved_targets.is_relative_to(resolved_project):
            raise TargetPreviewStoreError("미리보기 경로가 프로젝트를 벗어났습니다.")
        return resolved_targets

    @staticmethod
    def _target_hash(target: TargetSpec) -> str:
        canonical = json.dumps(
            target.model_dump(mode="json"),
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(canonical).hexdigest()

    @staticmethod
    def _is_link_like(path: Path) -> bool:
        if path.is_symlink():
            return True
        is_junction = getattr(path, "is_junction", None)
        return bool(is_junction is not None and is_junction())


__all__ = [
    "MaskedPreviewVariant",
    "TargetPreviewStore",
    "TargetPreviewStoreError",
]
