from __future__ import annotations

import shutil
from pathlib import Path
from uuid import UUID

from universal_rpa.application.normalization import NormalizationService
from universal_rpa.infrastructure.recording_store import JsonlRecordingStore

FIXTURE_SESSION_ID = UUID("00000000-0000-0000-0000-000000000702")


def fixture_directory() -> Path:
    return Path(__file__).resolve().parents[1] / "fixtures" / "recordings" / "ctrl-a-date-enter"


def fixture_store(tmp_path: Path) -> JsonlRecordingStore:
    session_dir = tmp_path / str(FIXTURE_SESSION_ID)
    session_dir.mkdir()
    for filename in ("manifest.json", "events.jsonl"):
        shutil.copy2(fixture_directory() / filename, session_dir / filename)
    return JsonlRecordingStore.for_test(tmp_path)


def test_same_jsonl_and_manifest_normalize_byte_identically(tmp_path: Path) -> None:
    store = fixture_store(tmp_path)
    service = NormalizationService()

    first = service.normalize_session(store, FIXTURE_SESSION_ID).model_dump_json()
    second = service.normalize_session(store, FIXTURE_SESSION_ID).model_dump_json()

    assert first == second


def test_golden_keyboard_session_yields_three_canonical_actions(tmp_path: Path) -> None:
    result = NormalizationService().normalize_session(
        fixture_store(tmp_path),
        FIXTURE_SESSION_ID,
    )
    assert [candidate.action_type for candidate in result.candidates] == [
        "windows.hotkey",
        "windows.set_text",
        "windows.press_key",
    ]
    assert result.candidates[1].value is not None
    assert result.candidates[1].value.display_value == "2026-07-27"
