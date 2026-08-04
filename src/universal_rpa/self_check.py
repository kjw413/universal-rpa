"""A path-free integrity check the packaged application can run on any machine.

The self-check is the first thing an operator runs on a new Windows box, and its
output is pasted into a sign-off record.  It therefore reports *only* names,
booleans, and short details — never a filesystem path — so the record can be
shared without leaking where the customer installed the app or where their data
lives.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from universal_rpa.adapters.windows.adapter import WindowsAutomationAdapter
from universal_rpa.application.workflow_codec import export_workflow_schema

#: The exact adapter identities a correct build registers, in bootstrap order.
BUILTIN_ADAPTER_IDS = ("windows", "clipboard", "tabular")
SELF_CHECK_NAMES = ("workflow_schema_v1", "builtin_adapters", "app_data_write", "dpi_awareness")


@dataclass(frozen=True, slots=True)
class SelfCheckItem:
    name: str
    ok: bool
    detail: str = ""


@dataclass(frozen=True, slots=True)
class SelfCheckReport:
    checks: tuple[SelfCheckItem, ...]

    @property
    def ok(self) -> bool:
        return all(item.ok for item in self.checks)

    def to_json(self) -> str:
        return json.dumps(
            {
                "ok": self.ok,
                "checks": [
                    {"name": item.name, "ok": item.ok, "detail": item.detail}
                    for item in self.checks
                ],
            },
            ensure_ascii=False,
            indent=2,
        )


def _check_workflow_schema() -> SelfCheckItem:
    try:
        schema = json.loads(export_workflow_schema())
    except Exception:
        return SelfCheckItem("workflow_schema_v1", False, "schema could not be generated")
    version = schema.get("properties", {}).get("schema_version", {})
    literal = version.get("const") or (version.get("enum") or [None])[0]
    if literal != "1":
        return SelfCheckItem("workflow_schema_v1", False, "schema version is not 1")
    return SelfCheckItem("workflow_schema_v1", True, "1")


def _check_builtin_adapters() -> SelfCheckItem:
    try:
        from universal_rpa.adapters.clipboard import ClipboardAutomationAdapter
        from universal_rpa.adapters.registry import AdapterRegistry
        from universal_rpa.adapters.tabular import TabularAutomationAdapter
    except Exception:
        return SelfCheckItem("builtin_adapters", False, "adapters could not be imported")
    registry = AdapterRegistry()
    try:
        # Registering exercises the descriptor contract, which is the part that
        # actually breaks when a build drops a module.
        registry.register(_windows_adapter(WindowsAutomationAdapter))
        registry.register(ClipboardAutomationAdapter())
        registry.register(TabularAutomationAdapter())
    except Exception:
        return SelfCheckItem("builtin_adapters", False, "an adapter failed to register")
    found = registry.adapter_ids()
    if set(found) != set(BUILTIN_ADAPTER_IDS):
        return SelfCheckItem("builtin_adapters", False, f"{len(found)} adapters registered")
    return SelfCheckItem("builtin_adapters", True, ", ".join(BUILTIN_ADAPTER_IDS))


def _windows_adapter(
    factory: type[WindowsAutomationAdapter],
) -> WindowsAutomationAdapter:
    from universal_rpa.adapters.windows.environment import WindowsEnvironmentProbe
    from universal_rpa.adapters.windows.foreground import ForegroundGuard
    from universal_rpa.adapters.windows.input_driver import WindowsInputDriver
    from universal_rpa.adapters.windows.target_resolver import WindowsTargetResolver

    probe = WindowsEnvironmentProbe()
    guard = ForegroundGuard(probe)
    return factory(WindowsTargetResolver(probe, guard), WindowsInputDriver(guard), probe)


def _check_app_data_write(app_data_root: Path | None) -> SelfCheckItem:
    base = app_data_root if app_data_root is not None else os.environ.get("LOCALAPPDATA")
    if not base:
        return SelfCheckItem("app_data_write", False, "no application-data location")
    directory = Path(base) / "UniversalRPAStudio" / "self-check"
    probe = directory / f"probe-{uuid4().hex}.tmp"
    try:
        directory.mkdir(parents=True, exist_ok=True)
        probe.write_bytes(b"universal-rpa-self-check")
        written = probe.read_bytes()
        probe.unlink()
    except OSError:
        return SelfCheckItem("app_data_write", False, "application data is not writable")
    if written != b"universal-rpa-self-check" or probe.exists():
        return SelfCheckItem("app_data_write", False, "probe file could not be verified")
    try:
        directory.rmdir()
    except OSError:
        pass
    return SelfCheckItem("app_data_write", True, "atomic write and delete")


def _check_dpi_awareness() -> SelfCheckItem:
    try:
        from universal_rpa.adapters.windows.dpi import enable_per_monitor_v2_dpi_awareness

        enable_per_monitor_v2_dpi_awareness()
    except OSError:
        return SelfCheckItem("dpi_awareness", False, "per-monitor-v2 could not be enabled")
    except Exception:
        return SelfCheckItem("dpi_awareness", False, "DPI support is unavailable")
    return SelfCheckItem("dpi_awareness", True, "per-monitor-v2")


def run_self_check(app_data_root: Path | None = None) -> SelfCheckReport:
    """Verify schema, adapters, application-data I/O, and DPI initialization."""

    return SelfCheckReport(
        checks=(
            _check_workflow_schema(),
            _check_builtin_adapters(),
            _check_app_data_write(app_data_root),
            _check_dpi_awareness(),
        )
    )


__all__ = [
    "BUILTIN_ADAPTER_IDS",
    "SELF_CHECK_NAMES",
    "SelfCheckItem",
    "SelfCheckReport",
    "run_self_check",
]
