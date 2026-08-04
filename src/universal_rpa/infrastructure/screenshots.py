"""Fail-closed failure screenshots bound to the exact observed window.

A capture is only written when the live window still matches the process,
top-level handle, and client size that the runner actually used, and when every
declared and live sensitive region has been masked in memory first.
"""

from __future__ import annotations

import os
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from PySide6.QtCore import QBuffer, QIODevice, QRect
from PySide6.QtGui import QColor, QImage, QPainter

from universal_rpa.domain.targets import RuntimeEnvironment, TargetSpec
from universal_rpa.infrastructure.sensitive_regions import PixelRegion, SensitiveRegionProvider

SCREENSHOT_TEMP_SUFFIX = ".universal-rpa-shot.tmp"


@dataclass(frozen=True, slots=True)
class ClientCapture:
    process_id: int
    hwnd: int
    client_screen_x: int
    client_screen_y: int
    width: int
    height: int
    image: QImage


class ExactWindowCapturePort(Protocol):
    """Captures the client area of exactly one already-identified window."""

    def capture_client(self, process_id: int, hwnd: int) -> ClientCapture | None: ...


class FailureScreenshotService:
    def __init__(
        self,
        *,
        capture: ExactWindowCapturePort,
        regions: SensitiveRegionProvider,
    ) -> None:
        self._capture = capture
        self._regions = regions

    def capture_failure(
        self,
        target: TargetSpec | None,
        expected_runtime: RuntimeEnvironment,
        destination: Path,
    ) -> Path | None:
        if target is None or target.adapter_id != "windows":
            return None
        temporary: Path | None = None
        try:
            capture = self._capture.capture_client(
                expected_runtime.process_id, expected_runtime.top_level_hwnd
            )
            if capture is None or not self._matches(capture, expected_runtime):
                return None
            regions = self._regions.resolve(target, capture, expected_runtime)
            payload = self._encode_png(self._masked(capture, regions))
            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary = destination.parent / f"{uuid4().hex}{SCREENSHOT_TEMP_SUFFIX}"
            with temporary.open("wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, destination)
        except Exception:
            if temporary is not None:
                with suppress(OSError):
                    temporary.unlink(missing_ok=True)
            return None
        return destination

    @staticmethod
    def _matches(capture: ClientCapture, runtime: RuntimeEnvironment) -> bool:
        return (
            capture.process_id == runtime.process_id
            and capture.hwnd == runtime.top_level_hwnd
            and capture.width == runtime.client_width
            and capture.height == runtime.client_height
            and not capture.image.isNull()
            and capture.image.width() == runtime.client_width
            and capture.image.height() == runtime.client_height
        )

    @staticmethod
    def _masked(capture: ClientCapture, regions: tuple[PixelRegion, ...]) -> QImage:
        image = capture.image.copy()
        if not regions:
            return image
        painter = QPainter(image)
        try:
            for region in regions:
                painter.fillRect(
                    QRect(region.x, region.y, region.width, region.height),
                    QColor("black"),
                )
        finally:
            painter.end()
        return image

    @staticmethod
    def _encode_png(image: QImage) -> bytes:
        buffer = QBuffer()
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        try:
            # PySide6 accepts only ``str`` here; its stub declares ``bytes``.
            if not image.save(buffer, "PNG"):  # type: ignore[call-overload]
                raise OSError("masked capture could not be encoded")
            payload: bytes = buffer.data().data()
            return payload
        finally:
            buffer.close()


__all__ = [
    "SCREENSHOT_TEMP_SUFFIX",
    "ClientCapture",
    "ExactWindowCapturePort",
    "FailureScreenshotService",
]
