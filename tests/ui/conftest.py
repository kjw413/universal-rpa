from __future__ import annotations

from pathlib import Path

import pytest

from tests.helpers.recording_fakes import (
    FakeInputCapture,
    InMemoryRecordingStore,
    StaticWindowContext,
)
from universal_rpa.adapters.registry import AdapterRegistry
from universal_rpa.application.editing import WorkflowEditingService
from universal_rpa.application.normalization import NormalizationService
from universal_rpa.application.projects import ProjectService
from universal_rpa.application.recording import RecordingService
from universal_rpa.application.validation import ValidationService
from universal_rpa.bootstrap import AppServices


@pytest.fixture
def app_services(tmp_path: Path) -> AppServices:
    store = InMemoryRecordingStore()
    capture = FakeInputCapture()
    context = StaticWindowContext()
    registry = AdapterRegistry()
    return AppServices(
        project_service=ProjectService(),
        recording_service=RecordingService(capture=capture, context=context, store=store),
        normalization_service=NormalizationService(),
        editing_service=WorkflowEditingService(),
        validation_service=ValidationService(registry=registry),
        adapter_registry=registry,
        recording_store=store,
        window_context=context,
    )
