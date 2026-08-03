"""The production bootstrap registers exactly the three shipped adapters."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from tests.helpers.recording_fakes import (
    FakeInputCapture,
    InMemoryRecordingStore,
    StaticWindowContext,
)
from universal_rpa.adapters.registry import AdapterRegistry
from universal_rpa.adapters.tabular.adapter import TabularAutomationAdapter
from universal_rpa.bootstrap import build_services
from universal_rpa.domain.errors import ErrorCode

NOW = datetime(2026, 8, 3, tzinfo=UTC)


def test_registry_reports_registered_adapter_ids_in_sorted_order() -> None:
    registry = AdapterRegistry()
    registry.register(TabularAutomationAdapter())

    assert registry.adapter_ids() == ("tabular",)


def test_bootstrap_registers_exactly_windows_clipboard_and_tabular(tmp_path: Path) -> None:
    services = build_services(
        local_app_data=tmp_path / "appdata",
        recording_store=InMemoryRecordingStore(),
        capture=FakeInputCapture(),
        window_context=StaticWindowContext(),
        source_repository_root=tmp_path / "source",
        now=NOW,
    )

    assert set(services.adapter_registry.adapter_ids()) == {"windows", "clipboard", "tabular"}
    assert services.execution_service is not None


def test_supplied_registry_is_used_verbatim_without_production_adapters(
    tmp_path: Path,
) -> None:
    services = build_services(
        local_app_data=tmp_path / "appdata",
        recording_store=InMemoryRecordingStore(),
        adapter_registry=AdapterRegistry(),
        capture=FakeInputCapture(),
        window_context=StaticWindowContext(),
        source_repository_root=tmp_path / "source",
        now=NOW,
    )

    assert services.adapter_registry.adapter_ids() == ()
    assert services.execution_service is None


def test_tabular_save_table_is_retryable_only_for_output_unavailable() -> None:
    descriptor = TabularAutomationAdapter().descriptor()

    assert descriptor.retryable_errors_by_action["tabular.save_table"] == frozenset(
        {ErrorCode.OUTPUT_UNAVAILABLE}
    )
