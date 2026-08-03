from __future__ import annotations

import ctypes
import os
import threading
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Protocol

from universal_rpa.adapters.clipboard import ClipboardAutomationAdapter
from universal_rpa.adapters.registry import AdapterRegistry
from universal_rpa.adapters.tabular import TabularAutomationAdapter, TabularDataSourceProvider
from universal_rpa.adapters.windows.adapter import WindowsAutomationAdapter
from universal_rpa.adapters.windows.capture import PynputInputCapture
from universal_rpa.adapters.windows.context import UiaFocusCache, WindowsWindowContext
from universal_rpa.adapters.windows.credentials import WindowsCredentialStore
from universal_rpa.adapters.windows.environment import WindowsEnvironmentProbe
from universal_rpa.adapters.windows.foreground import ForegroundGuard
from universal_rpa.adapters.windows.input_driver import WindowsInputDriver
from universal_rpa.adapters.windows.target_resolver import WindowsTargetResolver
from universal_rpa.adapters.windows.window_catalog import PyWin32WindowFacade, Win32WindowCatalog
from universal_rpa.application.editing import WorkflowEditingService
from universal_rpa.application.execution import ExecutionService
from universal_rpa.application.loops import LoopPlanner
from universal_rpa.application.normalization import NormalizationService
from universal_rpa.application.preflight import PreflightService
from universal_rpa.application.projects import ProjectService
from universal_rpa.application.recording import RecordingService
from universal_rpa.application.recording_privacy import RecordingPrivacyService
from universal_rpa.application.validation import ValidationService
from universal_rpa.application.value_resolution import ValueResolver
from universal_rpa.application.variable_preparation import VariablePreparationService
from universal_rpa.domain.recording import EventFocusSnapshot
from universal_rpa.infrastructure.checkpoint_store import JsonCheckpointStore
from universal_rpa.infrastructure.execution_journal import JsonExecutionJournalStore
from universal_rpa.infrastructure.recording_store import JsonlRecordingStore, RetentionSummary
from universal_rpa.infrastructure.target_preview_store import TargetPreviewStore
from universal_rpa.ports.capture import ControlSink, InputCapturePort, InputEventSink
from universal_rpa.ports.context import WindowContextPort
from universal_rpa.ports.data_sources import DataSourcePort
from universal_rpa.ports.repositories import RecordingStorePort


class RetentionRecordingStore(RecordingStorePort, Protocol):
    def purge_expired(
        self,
        *,
        now: datetime,
        retention: timedelta = timedelta(days=7),
    ) -> RetentionSummary: ...


class _CoordinateOnlyUia:
    def element_from_runtime_id(self, runtime_id: tuple[int, ...]) -> object | None:
        del runtime_id
        return None

    def elements_from_point(self, screen_x: int, screen_y: int) -> Iterable[object]:
        del screen_x, screen_y
        return ()

    def password_elements(self, top_level_hwnd: int) -> Iterable[object]:
        del top_level_hwnd
        return ()


class _FocusPollingCapture:
    def __init__(
        self,
        delegate: InputCapturePort,
        cache: UiaFocusCache,
        win32: PyWin32WindowFacade,
    ) -> None:
        self._delegate = delegate
        self._cache = cache
        self._win32 = win32
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self, event_sink: InputEventSink, control_sink: ControlSink) -> None:
        if self._thread is not None:
            raise RuntimeError("focus polling is already active")
        self._stop.clear()
        self._publish_focus()
        self._thread = threading.Thread(
            target=self._poll,
            name="universal-rpa-focus-poller",
            daemon=True,
        )
        self._thread.start()
        try:
            self._delegate.start(event_sink, control_sink)
        except Exception:
            self._stop.set()
            self._thread.join(1.0)
            self._thread = None
            raise

    def stop(self) -> None:
        self._delegate.stop()
        self._stop.set()
        thread = self._thread
        self._thread = None
        if thread is not None:
            thread.join(1.0)

    def _poll(self) -> None:
        while not self._stop.wait(0.03):
            self._publish_focus()

    def _publish_focus(self) -> None:
        try:
            hwnd = int(ctypes.windll.user32.GetForegroundWindow())
            if hwnd <= 0:
                return
            process_id = self._win32.window_process_id(hwnd)
            snapshot = EventFocusSnapshot(
                foreground_hwnd=hwnd,
                focused_hwnd=hwnd,
                foreground_process_id=process_id,
                cached_uia_runtime_id=None,
                focus_event_time_ms=int(time.monotonic() * 1_000),
                cache_generation=self._cache.next_generation(),
                cache_confirmed=True,
            )
            self._cache.publish(snapshot)
        except (OSError, RuntimeError, ValueError):
            return


@dataclass(frozen=True, slots=True)
class AppServices:
    project_service: ProjectService
    recording_service: RecordingService
    normalization_service: NormalizationService
    editing_service: WorkflowEditingService
    validation_service: ValidationService
    adapter_registry: AdapterRegistry
    recording_store: RecordingStorePort
    window_context: WindowContextPort
    preview_store: TargetPreviewStore = field(default_factory=TargetPreviewStore)
    recording_privacy: RecordingPrivacyService | None = None
    data_sources: DataSourcePort | None = None
    execution_service: ExecutionService | None = None
    startup_warnings: tuple[str, ...] = ()


def _production_recording_boundaries() -> tuple[InputCapturePort, WindowContextPort]:
    win32 = PyWin32WindowFacade()
    initial = EventFocusSnapshot(
        foreground_hwnd=0,
        focused_hwnd=None,
        foreground_process_id=os.getpid(),
        cached_uia_runtime_id=None,
        focus_event_time_ms=int(time.monotonic() * 1_000),
        cache_generation=0,
        cache_confirmed=False,
    )
    cache = UiaFocusCache(initial)
    catalog = Win32WindowCatalog(win32)
    context = WindowsWindowContext(
        win32=win32,
        uia=_CoordinateOnlyUia(),
        focus_cache=cache,
        catalog=catalog,
    )
    capture = _FocusPollingCapture(
        PynputInputCapture(context_cache=cache),
        cache,
        win32,
    )
    return capture, context


def build_services(
    *,
    active_project_dir: Path | None = None,
    local_app_data: Path | None = None,
    source_repository_root: Path | None = None,
    recording_store: RetentionRecordingStore | None = None,
    capture: InputCapturePort | None = None,
    window_context: WindowContextPort | None = None,
    adapter_registry: AdapterRegistry | None = None,
    now: datetime | None = None,
) -> AppServices:
    source_root = (
        Path(source_repository_root)
        if source_repository_root is not None
        else Path(__file__).resolve().parents[2]
    )
    forbidden_roots = tuple(path for path in (active_project_dir, source_root) if path is not None)
    store: RetentionRecordingStore = recording_store or JsonlRecordingStore.open_default(
        local_app_data=local_app_data,
        forbidden_roots=forbidden_roots,
    )
    warnings: list[str] = []
    current = now or datetime.now(UTC)
    try:
        retention = store.purge_expired(now=current, retention=timedelta(days=7))
    except Exception:
        warnings.append("이전 기록의 보존 기간 정리를 완료하지 못했습니다.")
    else:
        if retention.failures:
            warnings.append("사용 중인 이전 기록 일부는 정리하지 못했습니다.")

    if (capture is None) != (window_context is None):
        raise ValueError("capture and window_context must be supplied together")
    if capture is None or window_context is None:
        capture, window_context = _production_recording_boundaries()

    registry = adapter_registry or AdapterRegistry()
    data_sources = TabularDataSourceProvider()
    secret_store = WindowsCredentialStore()
    execution_service: ExecutionService | None = None
    if adapter_registry is None:
        probe = WindowsEnvironmentProbe()
        guard = ForegroundGuard(probe)
        resolver = WindowsTargetResolver(probe, guard)
        windows_adapter = WindowsAutomationAdapter(
            resolver,
            WindowsInputDriver(guard),
            probe,
            target_capture=window_context if hasattr(window_context, "capture_target") else None,
        )
        registry.register(windows_adapter)
        registry.register(ClipboardAutomationAdapter())
        registry.register(TabularAutomationAdapter())
        app_data_root = (
            Path(local_app_data) if local_app_data is not None else Path(os.environ["LOCALAPPDATA"])
        )
        run_root = app_data_root / "UniversalRPAStudio" / "runs"
        execution_service = ExecutionService(
            preflight=PreflightService(
                ValidationService(
                    registry=registry, data_sources=data_sources, secret_store=secret_store
                ),
                lambda _: probe.snapshot(probe.foreground_hwnd()),
            ),
            registry=registry,
            loop_planner=LoopPlanner(data_sources),
            variable_preparation=VariablePreparationService(),
            value_resolver=ValueResolver(secret_store),
            secret_store=secret_store,
            checkpoints=JsonCheckpointStore(run_root / "checkpoints"),
            journals=JsonExecutionJournalStore(run_root / "journals"),
        )
    preview_store = TargetPreviewStore()
    privacy = RecordingPrivacyService(store)
    recording_service = RecordingService(
        capture=capture,
        context=window_context,
        store=store,
    )
    return AppServices(
        project_service=ProjectService(),
        recording_service=recording_service,
        normalization_service=NormalizationService(),
        editing_service=WorkflowEditingService(),
        validation_service=ValidationService(registry=registry, data_sources=data_sources),
        adapter_registry=registry,
        recording_store=store,
        window_context=window_context,
        preview_store=preview_store,
        recording_privacy=privacy,
        data_sources=data_sources,
        execution_service=execution_service,
        startup_warnings=tuple(warnings),
    )


__all__ = ["AppServices", "RetentionRecordingStore", "build_services"]
