from __future__ import annotations

import inspect
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest

from universal_rpa.domain.recording import (
    RawEventType,
    RawInputEvent,
    RecordingEnvironmentSnapshot,
    RecordingSession,
    RecordingTarget,
    TargetSnapshot,
    WindowContextSnapshot,
)
from universal_rpa.domain.targets import UiaSelector
from universal_rpa.infrastructure.recording_store import (
    CorruptRecordingError,
    JsonlRecordingStore,
    RecordingNotFinalizedError,
    UnsafeRecordingPathError,
)

SESSION_ID = UUID("00000000-0000-0000-0000-000000000201")
EVENT_ID = UUID("00000000-0000-0000-0000-000000000202")
NOW = datetime(2026, 7, 29, 9, 0, tzinfo=UTC)


def recording_session(
    *,
    session_id: UUID = SESSION_ID,
    started_at: datetime = NOW,
    retained: bool = False,
) -> RecordingSession:
    return RecordingSession(
        session_id=session_id,
        target=RecordingTarget(
            process_id=200,
            process_executable="mis.exe",
            top_level_hwnd=100,
            window_title="MIS",
            window_class="MisWindow",
        ),
        started_at=started_at,
        retained=retained,
    )


def raw_event(*, session_id: UUID = SESSION_ID, event_id: UUID = EVENT_ID) -> RawInputEvent:
    return RawInputEvent(
        session_id=session_id,
        event_id=event_id,
        monotonic_ns=10,
        wall_time_utc=NOW,
        event_type=RawEventType.MOUSE_DOWN,
        payload={"button": "left", "x": 10, "y": 20},
        in_scope=True,
        capture_state="recording",
        window_context=WindowContextSnapshot(
            foreground_hwnd=100,
            focused_hwnd=101,
            process_id=200,
            process_executable="mis.exe",
            top_level_hwnd=100,
            window_title="MIS",
            window_class="MisWindow",
            focused_runtime_id=(1, 2, 3),
            selected_top_level_hwnd=100,
            owned_by_selected_window=True,
            context_confident=True,
        ),
        target_snapshot=TargetSnapshot(
            selector_candidates=(UiaSelector(automation_id="submit"),),
            focused_runtime_id=(1, 2, 3),
            editable=False,
            is_password=False,
            observed_value=None,
            bounds=None,
        ),
        environment_snapshot=RecordingEnvironmentSnapshot(
            client_left=0,
            client_top=0,
            client_width=800,
            client_height=600,
            dpi_x=96,
            dpi_y=96,
            monitor_scale=1.0,
            monitor_id="DISPLAY1",
            double_click_time_ms=500,
            drag_width_px=4,
            drag_height_px=4,
        ),
    )


def test_production_store_has_no_arbitrary_root_parameter() -> None:
    assert tuple(inspect.signature(JsonlRecordingStore.open_default).parameters) == (
        "local_app_data",
        "forbidden_roots",
    )


def test_production_store_rejects_project_overlap(tmp_path: Path) -> None:
    with pytest.raises(UnsafeRecordingPathError):
        JsonlRecordingStore.open_default(
            local_app_data=tmp_path,
            forbidden_roots=(tmp_path / "UniversalRPAStudio",),
        )


def test_append_writes_one_json_object_per_line(tmp_path: Path) -> None:
    store = JsonlRecordingStore.for_test(tmp_path)
    session = recording_session()
    store.create_session(session)
    store.append(raw_event())
    store.append(raw_event(event_id=UUID("00000000-0000-0000-0000-000000000203")))

    lines = (
        (tmp_path / str(session.session_id) / "events.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    )
    assert len(lines) == 2
    assert all(json.loads(line)["schema_version"] == "1" for line in lines)


def test_finalize_does_not_rewrite_events_and_loads_summary(tmp_path: Path) -> None:
    store = JsonlRecordingStore.for_test(tmp_path, clock=lambda: NOW + timedelta(minutes=1))
    session = recording_session()
    store.create_session(session)
    store.append(raw_event())
    events_path = tmp_path / str(session.session_id) / "events.jsonl"
    original = events_path.read_bytes()

    summary = store.finalize(
        session.session_id,
        retained=False,
        incomplete=True,
        dropped_event_count=2,
    )

    assert events_path.read_bytes() == original
    assert summary.event_count == 1
    assert summary.dropped_event_count == 2
    assert summary.incomplete is True
    assert store.load_summary(session.session_id) == summary
    assert tuple(store.iter_events(session.session_id)) == (raw_event(),)


def test_unfinalized_summary_is_rejected(tmp_path: Path) -> None:
    store = JsonlRecordingStore.for_test(tmp_path)
    session = recording_session()
    store.create_session(session)
    with pytest.raises(RecordingNotFinalizedError):
        store.load_summary(session.session_id)


def test_iter_events_reports_corrupt_line_number(tmp_path: Path) -> None:
    store = JsonlRecordingStore.for_test(tmp_path)
    session = recording_session()
    store.create_session(session)
    store.append(raw_event())
    events_path = tmp_path / str(session.session_id) / "events.jsonl"
    with events_path.open("a", encoding="utf-8") as stream:
        stream.write("not-json\n")

    with pytest.raises(CorruptRecordingError) as caught:
        tuple(store.iter_events(session.session_id))
    assert caught.value.line_number == 2


def test_retained_session_survives_default_retention(tmp_path: Path) -> None:
    old_time = NOW - timedelta(days=8)
    store = JsonlRecordingStore.for_test(tmp_path, clock=lambda: old_time)
    session = recording_session(started_at=old_time, retained=True)
    store.create_session(session)
    store.finalize(session.session_id, retained=True, incomplete=False)

    assert store.purge_expired(now=NOW).deleted == ()
    assert (tmp_path / str(session.session_id)).exists()


def test_expired_unretained_session_is_deleted(tmp_path: Path) -> None:
    old_time = NOW - timedelta(days=8)
    store = JsonlRecordingStore.for_test(tmp_path, clock=lambda: old_time)
    session = recording_session(started_at=old_time)
    store.create_session(session)
    store.finalize(session.session_id, retained=False, incomplete=False)

    result = store.purge_expired(now=NOW)
    assert result.deleted == (session.session_id,)
    assert result.failures == ()
    assert not (tmp_path / str(session.session_id)).exists()


def test_explicit_sensitive_reclassification_deletes_source_session(tmp_path: Path) -> None:
    store = JsonlRecordingStore.for_test(tmp_path)
    session = recording_session(retained=True)
    store.create_session(session)
    store.finalize(session.session_id, retained=True, incomplete=False)

    store.delete_session(session.session_id, reason="reclassified_as_secret")

    assert not (tmp_path / str(session.session_id)).exists()
