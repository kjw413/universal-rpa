from __future__ import annotations

from uuid import UUID

import pytest

from universal_rpa.application.recording_privacy import (
    RecordingPrivacyService,
    SensitiveSourcePurgeFailed,
)

SESSION_ID = UUID("00000000-0000-0000-0000-000000000861")


class FailingDeleteStore:
    def __init__(self) -> None:
        self.delete_attempts = 0

    def load_summary(self, session_id: UUID):  # type: ignore[no-untyped-def]
        del session_id
        return type("Summary", (), {"retained": False})()

    def delete_session(self, session_id: UUID, *, reason: str) -> None:
        del session_id, reason
        self.delete_attempts += 1
        raise PermissionError


def test_secret_mode_is_rejected_when_source_raw_session_cannot_be_purged() -> None:
    store = FailingDeleteStore()
    privacy = RecordingPrivacyService(store)  # type: ignore[arg-type]

    with pytest.raises(SensitiveSourcePurgeFailed):
        privacy.purge_before_secret_mode((SESSION_ID,), allow_retained=True)

    assert store.delete_attempts == 1


def test_reopened_workflow_requires_explicit_source_session_selection() -> None:
    privacy = RecordingPrivacyService(FailingDeleteStore())  # type: ignore[arg-type]

    with pytest.raises(SensitiveSourcePurgeFailed, match="선택"):
        privacy.purge_before_secret_mode(None, allow_retained=True)
