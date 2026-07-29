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
from universal_rpa.ports.credentials import SecretStorePort, SecretValue
from universal_rpa.ports.data_sources import DataPreview, DataSourcePort
from universal_rpa.ports.repositories import WorkflowRepositoryPort

__all__ = [
    "ActionRequest",
    "AdapterActionResult",
    "AdapterDescriptor",
    "AssertionObservation",
    "AutomationAdapter",
    "CancellationToken",
    "ConditionObservation",
    "DataPreview",
    "DataSourcePort",
    "ExecutionContext",
    "SecretStorePort",
    "SecretValue",
    "TargetCapturePort",
    "TargetCaptureRequest",
    "TargetCaptureResult",
    "WorkflowRepositoryPort",
]
