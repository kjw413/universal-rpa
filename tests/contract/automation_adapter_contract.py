from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date
from pathlib import Path
from uuid import UUID

import pytest

from universal_rpa.domain.errors import ErrorCode
from universal_rpa.domain.targets import DateContext, RuntimeEnvironment, TargetSpec
from universal_rpa.domain.types import FrozenMapping
from universal_rpa.ports.automation import (
    ActionRequest,
    AutomationAdapter,
    CancellationToken,
    ExecutionContext,
    TargetCaptureRequest,
    TargetCaptureResult,
)

RUN_ID = UUID("00000000-0000-0000-0000-000000000001")
STEP_ID = UUID("00000000-0000-0000-0000-000000000002")


def fake_target(adapter_id: str, *, candidate: int = 1) -> TargetSpec:
    return TargetSpec(adapter_id=adapter_id, payload={"candidate": candidate})


def runtime_environment() -> RuntimeEnvironment:
    return RuntimeEnvironment(
        interactive_desktop=True,
        process_id=100,
        process_executable="fake.exe",
        top_level_hwnd=200,
        window_title="Fake",
        window_class="FakeWindow",
        foreground_hwnd=200,
        dpi_x=96,
        dpi_y=96,
        client_width=800,
        client_height=600,
        monitor_scale=1.0,
    )


def target_capture_request() -> TargetCaptureRequest:
    return TargetCaptureRequest(
        runtime=runtime_environment(),
        screen_x=12,
        screen_y=34,
        focused_runtime_id=(1, 2),
    )


def execution_context() -> ExecutionContext:
    return ExecutionContext(
        run_id=RUN_ID,
        step_id=STEP_ID,
        iteration_path=(),
        variables=FrozenMapping.empty(),
        credential_refs=FrozenMapping.empty(),
        date_context=DateContext(today=date(2026, 7, 29), run_date=date(2026, 7, 29)),
        output_root=Path("artifacts"),
        row_stack=(),
        action_outputs=FrozenMapping.empty(),
    )


class AutomationAdapterContract(ABC):
    @abstractmethod
    def make_adapter(self) -> AutomationAdapter:
        raise NotImplementedError

    @abstractmethod
    def make_supported_request(self, adapter: AutomationAdapter) -> ActionRequest:
        raise NotImplementedError

    @abstractmethod
    def side_effect_count(self, adapter: AutomationAdapter) -> int:
        raise NotImplementedError

    def test_cancelled_request_has_no_side_effect(self) -> None:
        adapter = self.make_adapter()
        token = CancellationToken()
        token.cancel()

        result = adapter.execute(self.make_supported_request(adapter), execution_context(), token)

        assert result.error_code is ErrorCode.CANCELLED
        assert self.side_effect_count(adapter) == 0

    def test_unknown_action_has_no_side_effect(self) -> None:
        adapter = self.make_adapter()
        request = ActionRequest(
            action_type=f"{adapter.adapter_id}.unknown",
            target=None,
            parameters=FrozenMapping.empty(),
            value=None,
            has_postcondition_or_assertion=False,
        )

        result = adapter.execute(request, execution_context(), CancellationToken())

        assert result.error_code is ErrorCode.ACTION_UNSUPPORTED
        assert self.side_effect_count(adapter) == 0

    def test_capture_target_honors_descriptor_capability(self) -> None:
        adapter = self.make_adapter()

        captured = adapter.capture_target(target_capture_request(), CancellationToken())

        if not adapter.descriptor().supports_target_capture:
            assert captured.target is None
            assert captured.candidates == ()
            assert captured.preview_png is None
            assert [issue.code for issue in captured.issues] == [ErrorCode.ACTION_UNSUPPORTED]
            return
        assert captured.target is not None
        assert captured.target.adapter_id == adapter.adapter_id
        assert captured.target in captured.candidates
        assert captured.preview_png is None or captured.preview_png.startswith(b"\x89PNG")

    def test_capture_result_cannot_select_a_target_outside_candidates(self) -> None:
        with pytest.raises(ValueError, match="selected target must be one of candidates"):
            TargetCaptureResult(
                target=fake_target(adapter_id=self.make_adapter().adapter_id),
                candidates=(),
                preview_png=None,
            )


class TargetingAutomationAdapterContract(AutomationAdapterContract):
    @abstractmethod
    def configure_ambiguous_target(self, adapter: AutomationAdapter) -> None:
        raise NotImplementedError

    def test_ambiguous_target_maps_to_common_error_without_side_effect(self) -> None:
        adapter = self.make_adapter()
        self.configure_ambiguous_target(adapter)

        result = adapter.execute(
            self.make_supported_request(adapter),
            execution_context(),
            CancellationToken(),
        )

        assert result.error_code is ErrorCode.TARGET_AMBIGUOUS
        assert self.side_effect_count(adapter) == 0
