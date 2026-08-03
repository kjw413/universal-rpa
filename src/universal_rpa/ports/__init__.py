"""Synchronous application boundaries implemented by trusted adapters."""

from universal_rpa.ports.automation import (
    ActionRequest,
    AdapterActionResult,
    AdapterDescriptor,
    AssertionObservation,
    AutomationAdapter,
    CancellationToken,
    ConditionObservation,
    ExecutionContext,
    TargetCapturePort,
    TargetCaptureRequest,
    TargetCaptureResult,
)
from universal_rpa.ports.capture import ControlCommand, ControlHotkeys, InputCapturePort
from universal_rpa.ports.context import CapturedEventContext, WindowContextPort
from universal_rpa.ports.credentials import SecretStorePort, SecretValue
from universal_rpa.ports.data_sources import DataPreview, DataSourcePort
from universal_rpa.ports.repositories import RecordingStorePort, WorkflowRepositoryPort

__all__ = [
    "ActionRequest",
    "AdapterActionResult",
    "AdapterDescriptor",
    "AssertionObservation",
    "AutomationAdapter",
    "CancellationToken",
    "CapturedEventContext",
    "ConditionObservation",
    "ControlCommand",
    "ControlHotkeys",
    "DataPreview",
    "DataSourcePort",
    "ExecutionContext",
    "InputCapturePort",
    "RecordingStorePort",
    "SecretStorePort",
    "SecretValue",
    "TargetCapturePort",
    "TargetCaptureRequest",
    "TargetCaptureResult",
    "WindowContextPort",
    "WorkflowRepositoryPort",
]
