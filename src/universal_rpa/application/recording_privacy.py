from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from universal_rpa.ports.repositories import RecordingStorePort


class SensitiveSourcePurgeFailed(RuntimeError):
    pass


class RecordingPrivacyService:
    def __init__(self, recording_store: RecordingStorePort) -> None:
        self._recording_store = recording_store

    def purge_before_secret_mode(
        self,
        source_session_ids: Sequence[UUID] | None,
        *,
        allow_retained: bool,
    ) -> None:
        if source_session_ids is None:
            raise SensitiveSourcePurgeFailed(
                "비밀값 전환 전에 관련 원본 기록을 선택해 삭제해야 합니다."
            )
        unique = tuple(dict.fromkeys(source_session_ids))
        for session_id in unique:
            try:
                summary = self._recording_store.load_summary(session_id)
                if summary.retained and not allow_retained:
                    raise SensitiveSourcePurgeFailed(
                        "보존 표시된 원본 기록의 삭제 승인이 필요합니다."
                    )
                self._recording_store.delete_session(
                    session_id,
                    reason="secret_mode_source_purge",
                )
            except SensitiveSourcePurgeFailed:
                raise
            except Exception:
                raise SensitiveSourcePurgeFailed(
                    "비밀값 원본 기록을 안전하게 삭제하지 못했습니다."
                ) from None


__all__ = ["RecordingPrivacyService", "SensitiveSourcePurgeFailed"]
