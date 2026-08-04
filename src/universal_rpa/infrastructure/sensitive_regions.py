"""Union of declared and live sensitive regions in one capture's pixel basis."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from universal_rpa.domain.targets import (
    NormalizedRect,
    RuntimeEnvironment,
    TargetSpec,
    WindowsTarget,
)

if TYPE_CHECKING:  # pragma: no cover - import cycle only matters for typing
    from universal_rpa.infrastructure.screenshots import ClientCapture


@dataclass(frozen=True, slots=True)
class PixelRegion:
    x: int
    y: int
    width: int
    height: int


class PasswordRegionProbe(Protocol):
    """Returns live password-field bounds in screen coordinates."""

    def password_screen_rects(self, hwnd: int) -> Iterable[tuple[int, int, int, int]]: ...


def _clip(x: int, y: int, width: int, height: int, bounds: tuple[int, int]) -> PixelRegion | None:
    max_width, max_height = bounds
    left = max(0, x)
    top = max(0, y)
    right = min(max_width, x + width)
    bottom = min(max_height, y + height)
    if right <= left or bottom <= top:
        return None
    return PixelRegion(x=left, y=top, width=right - left, height=bottom - top)


class SensitiveRegionProvider:
    def __init__(self, password_probe: PasswordRegionProbe | None = None) -> None:
        self._password_probe = password_probe

    def resolve(
        self,
        target: TargetSpec,
        capture: ClientCapture,
        expected_runtime: RuntimeEnvironment,
    ) -> tuple[PixelRegion, ...]:
        """Return every region to mask, or raise when a source cannot be trusted."""

        bounds = (capture.width, capture.height)
        regions: list[PixelRegion] = []
        for rect in self._declared_regions(target):
            region = _clip(
                round(rect.x * capture.width),
                round(rect.y * capture.height),
                max(1, round(rect.width * capture.width)),
                max(1, round(rect.height * capture.height)),
                bounds,
            )
            if region is not None and region not in regions:
                regions.append(region)

        for screen_rect in self._live_password_regions(expected_runtime.top_level_hwnd):
            screen_x, screen_y, width, height = screen_rect
            region = _clip(
                screen_x - capture.client_screen_x,
                screen_y - capture.client_screen_y,
                width,
                height,
                bounds,
            )
            if region is not None and region not in regions:
                regions.append(region)
        return tuple(regions)

    @staticmethod
    def _declared_regions(target: TargetSpec) -> tuple[NormalizedRect, ...]:
        if target.adapter_id != "windows":
            raise ValueError("only Windows targets declare sensitive regions")
        windows = WindowsTarget.model_validate(target.payload)
        return windows.masking_regions

    def _live_password_regions(self, hwnd: int) -> tuple[tuple[int, int, int, int], ...]:
        if self._password_probe is None:
            return ()
        found: list[tuple[int, int, int, int]] = []
        for rect in self._password_probe.password_screen_rects(hwnd):
            screen_x, screen_y, width, height = (int(value) for value in rect)
            if width <= 0 or height <= 0:
                continue
            found.append((screen_x, screen_y, width, height))
        return tuple(found)


__all__ = ["PasswordRegionProbe", "PixelRegion", "SensitiveRegionProvider"]
