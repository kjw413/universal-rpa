from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from universal_rpa.domain.recording import (
    NativeInputEvent,
    RecordingEnvironmentSnapshot,
    RecordingTarget,
    TargetSnapshot,
    WindowContextSnapshot,
)
from universal_rpa.ports.automation import (
    CancellationToken,
    TargetCaptureRequest,
    TargetCaptureResult,
)


@dataclass(frozen=True, slots=True)
class CapturedEventContext:
    window_context: WindowContextSnapshot
    target_snapshot: TargetSnapshot | None
    environment_snapshot: RecordingEnvironmentSnapshot
    in_scope: bool


class WindowContextPort(Protocol):
    def list_recordable_windows(self) -> tuple[RecordingTarget, ...]: ...

    def capture_context(
        self,
        event: NativeInputEvent,
        selected: RecordingTarget,
    ) -> CapturedEventContext: ...

    def capture_target(
        self,
        request: TargetCaptureRequest,
        cancellation: CancellationToken,
    ) -> TargetCaptureResult: ...


__all__ = ["CapturedEventContext", "WindowContextPort"]
