from __future__ import annotations

from universal_rpa.application.normalization import normalize_keyboard_events

from .test_keyboard_normalization import keyboard_event


def test_password_candidate_contains_only_unassigned_secret_reference() -> None:
    events = tuple(
        keyboard_event(
            character,
            event_number=index,
            monotonic_ms=index * 10,
            text=character,
            observed_value="hunter2",
            is_password=True,
        )
        for index, character in enumerate("hunter2", start=1)
    )

    result = normalize_keyboard_events(events)
    encoded = result.model_dump_json()

    assert "hunter2" not in encoded
    assert '"mode":"secret_ref"' in encoded
    assert len(result.candidates) == 1
    assert result.candidates[0].requires_confirmation is True


def test_password_candidate_serialization_contains_no_key_labels() -> None:
    events = (
        keyboard_event(
            "SENTINEL_PASSWORD_KEY",
            event_number=1,
            monotonic_ms=0,
            text="SENTINEL_PASSWORD_TEXT",
            observed_value="SENTINEL_PASSWORD_VALUE",
            is_password=True,
        ),
    )
    encoded = normalize_keyboard_events(events).model_dump_json()
    assert "SENTINEL_PASSWORD" not in encoded
