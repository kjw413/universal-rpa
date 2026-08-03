from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from universal_rpa.infrastructure.recording_store import JsonlRecordingStore


@dataclass(frozen=True, slots=True)
class AppServices:
    recording_store: JsonlRecordingStore


def build_services(
    *,
    active_project_dir: Path | None = None,
    local_app_data: Path | None = None,
    source_repository_root: Path | None = None,
) -> AppServices:
    source_root = (
        Path(source_repository_root)
        if source_repository_root is not None
        else Path(__file__).resolve().parents[2]
    )
    forbidden_roots = tuple(path for path in (active_project_dir, source_root) if path is not None)
    return AppServices(
        recording_store=JsonlRecordingStore.open_default(
            local_app_data=local_app_data,
            forbidden_roots=forbidden_roots,
        )
    )


__all__ = ["AppServices", "build_services"]
