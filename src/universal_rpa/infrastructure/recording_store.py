from __future__ import annotations

import json
import os
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from pydantic import ValidationError

from universal_rpa.domain.recording import (
    RawInputEvent,
    RecordingSession,
    RecordingSessionSummary,
)
from universal_rpa.infrastructure.app_paths import default_recordings_root

_PRODUCTION_STORE = object()
_TEST_STORE = object()


class RecordingStoreError(RuntimeError):
    """Base class for safe recording-store failures."""


class UnsafeRecordingPathError(RecordingStoreError):
    """Raised when a requested path could escape or alias the recording root."""


class RecordingNotFinalizedError(RecordingStoreError):
    """Raised when finalized recording data was requested too early."""


class CorruptRecordingError(RecordingStoreError):
    """Raised when persisted recording data cannot be validated safely."""

    def __init__(self, message: str, *, line_number: int | None = None) -> None:
        super().__init__(message)
        self.line_number = line_number


@dataclass(frozen=True, slots=True)
class RetentionFailure:
    session_id: UUID | None
    reason: str


@dataclass(frozen=True, slots=True)
class RetentionSummary:
    deleted: tuple[UUID, ...] = ()
    failures: tuple[RetentionFailure, ...] = ()


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _require_utc(value: datetime, *, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError(f"{field_name} must use UTC")
    return value


def _paths_overlap(left: Path, right: Path) -> bool:
    return left == right or left.is_relative_to(right) or right.is_relative_to(left)


def _is_link_like(path: Path) -> bool:
    if path.is_symlink():
        return True
    is_junction = getattr(path, "is_junction", None)
    return bool(is_junction is not None and is_junction())


class JsonlRecordingStore:
    def __init__(
        self,
        root: Path,
        *,
        _factory_token: object,
        _clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        if _factory_token not in {_PRODUCTION_STORE, _TEST_STORE}:
            raise TypeError("use open_default() or for_test()")
        self._root = Path(root).resolve(strict=False)
        self._clock = _clock
        self._root.mkdir(parents=True, exist_ok=True)
        if not self._root.is_dir():
            raise RecordingStoreError("recording root is not a directory")

    @classmethod
    def open_default(
        cls,
        local_app_data: Path | None = None,
        forbidden_roots: tuple[Path, ...] = (),
    ) -> JsonlRecordingStore:
        root = default_recordings_root(local_app_data).resolve(strict=False)
        for forbidden in forbidden_roots:
            resolved_forbidden = Path(forbidden).resolve(strict=False)
            if _paths_overlap(root, resolved_forbidden):
                raise UnsafeRecordingPathError(
                    "recording storage must not overlap a project or source repository"
                )
        return cls(root, _factory_token=_PRODUCTION_STORE)

    @classmethod
    def for_test(
        cls,
        root: Path,
        *,
        clock: Callable[[], datetime] = _utc_now,
    ) -> JsonlRecordingStore:
        return cls(root, _factory_token=_TEST_STORE, _clock=clock)

    @property
    def root(self) -> Path:
        return self._root

    def create_session(self, session: RecordingSession) -> None:
        session_dir = self._session_dir(session.session_id)
        try:
            session_dir.mkdir()
        except FileExistsError:
            raise RecordingStoreError("recording session already exists") from None
        self._atomic_write_json(
            session_dir / "manifest.json",
            session.model_dump(mode="json"),
        )

    def append(self, event: RawInputEvent) -> None:
        session_dir = self._existing_session_dir(event.session_id)
        manifest = self._read_manifest(session_dir)
        if manifest.get("finalized") is True:
            raise RecordingStoreError("cannot append to a finalized recording session")
        try:
            RecordingSession.model_validate(manifest)
        except ValidationError as error:
            raise CorruptRecordingError("recording manifest is corrupt") from error

        events_path = session_dir / "events.jsonl"
        self._reject_link(events_path)
        serialized = event.model_dump_json()
        with events_path.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(serialized)
            stream.write("\n")
            stream.flush()

    def finalize(
        self,
        session_id: UUID,
        *,
        retained: bool,
        incomplete: bool,
        dropped_event_count: int = 0,
    ) -> RecordingSessionSummary:
        if dropped_event_count < 0:
            raise ValueError("dropped_event_count must be nonnegative")
        session_dir = self._existing_session_dir(session_id)
        manifest = self._read_manifest(session_dir)
        if manifest.get("finalized") is True:
            return self._validate_summary(manifest)
        try:
            session = RecordingSession.model_validate(manifest)
        except ValidationError as error:
            raise CorruptRecordingError("recording manifest is corrupt") from error

        finished_at = _require_utc(self._clock(), field_name="finished_at")
        if finished_at < session.started_at:
            finished_at = session.started_at
        summary = RecordingSessionSummary(
            session_id=session.session_id,
            finalized=True,
            incomplete=incomplete,
            retained=retained,
            event_count=self._event_line_count(session_dir),
            dropped_event_count=dropped_event_count,
            started_at=session.started_at,
            finished_at=finished_at,
        )
        self._atomic_write_json(
            session_dir / "manifest.json",
            summary.model_dump(mode="json"),
        )
        return summary

    def load_summary(self, session_id: UUID) -> RecordingSessionSummary:
        manifest = self._read_manifest(self._existing_session_dir(session_id))
        if manifest.get("finalized") is not True:
            raise RecordingNotFinalizedError("recording session is not finalized")
        return self._validate_summary(manifest)

    def iter_events(self, session_id: UUID) -> Iterator[RawInputEvent]:
        session_dir = self._existing_session_dir(session_id)
        events_path = session_dir / "events.jsonl"
        self._reject_link(events_path)
        if not events_path.exists():
            return
        with events_path.open("r", encoding="utf-8") as stream:
            for line_number, line in enumerate(stream, start=1):
                try:
                    raw = json.loads(line)
                    event = RawInputEvent.model_validate(raw)
                except (json.JSONDecodeError, ValidationError) as error:
                    raise CorruptRecordingError(
                        "recording event is corrupt",
                        line_number=line_number,
                    ) from error
                if event.session_id != session_id:
                    raise CorruptRecordingError(
                        "recording event belongs to another session",
                        line_number=line_number,
                    )
                yield event

    def list_sessions(self) -> tuple[RecordingSessionSummary, ...]:
        summaries: list[RecordingSessionSummary] = []
        for session_id, session_dir in self._session_directories():
            manifest = self._read_manifest(session_dir)
            if manifest.get("finalized") is not True:
                continue
            summary = self._validate_summary(manifest)
            if summary.session_id != session_id:
                raise CorruptRecordingError("recording manifest has the wrong session id")
            summaries.append(summary)
        return tuple(sorted(summaries, key=lambda item: (item.started_at, str(item.session_id))))

    def delete_session(self, session_id: UUID, *, reason: str) -> None:
        if not reason.strip():
            raise ValueError("recording deletion requires a reason")
        session_dir = self._session_dir(session_id)
        if not session_dir.exists() and not _is_link_like(session_dir):
            return
        if _is_link_like(session_dir):
            raise UnsafeRecordingPathError("recording session path must not be a link")
        self._remove_tree_without_following_links(session_dir)

    def purge_expired(
        self,
        *,
        now: datetime,
        retention: timedelta = timedelta(days=7),
    ) -> RetentionSummary:
        now = _require_utc(now, field_name="now")
        if retention < timedelta(0):
            raise ValueError("retention must be nonnegative")
        cutoff = now - retention
        deleted: list[UUID] = []
        failures: list[RetentionFailure] = []

        for child in sorted(self._root.iterdir(), key=lambda path: path.name):
            session_id = self._parse_session_directory_name(child.name)
            if session_id is None:
                continue
            if _is_link_like(child):
                failures.append(RetentionFailure(session_id, "unsafe_link"))
                continue
            if not child.is_dir():
                continue
            try:
                manifest = self._read_manifest(child)
                retained, reference_time = self._retention_metadata(manifest)
                if retained or reference_time > cutoff:
                    continue
                self._remove_tree_without_following_links(child)
            except PermissionError:
                failures.append(RetentionFailure(session_id, "locked"))
            except (OSError, RecordingStoreError, ValidationError):
                failures.append(RetentionFailure(session_id, "delete_failed"))
            else:
                deleted.append(session_id)

        return RetentionSummary(tuple(deleted), tuple(failures))

    def _session_dir(self, session_id: UUID) -> Path:
        if not isinstance(session_id, UUID):
            raise TypeError("session_id must be a UUID")
        path = self._root / str(session_id)
        if path.parent != self._root or path.name != str(session_id):
            raise UnsafeRecordingPathError("invalid recording session path")
        return path

    def _existing_session_dir(self, session_id: UUID) -> Path:
        path = self._session_dir(session_id)
        if _is_link_like(path):
            raise UnsafeRecordingPathError("recording session path must not be a link")
        if not path.is_dir():
            raise FileNotFoundError("recording session does not exist")
        return path

    def _session_directories(self) -> Iterator[tuple[UUID, Path]]:
        for child in sorted(self._root.iterdir(), key=lambda path: path.name):
            session_id = self._parse_session_directory_name(child.name)
            if session_id is None or _is_link_like(child) or not child.is_dir():
                continue
            yield session_id, child

    @staticmethod
    def _parse_session_directory_name(name: str) -> UUID | None:
        try:
            session_id = UUID(name)
        except ValueError:
            return None
        return session_id if str(session_id) == name else None

    def _read_manifest(self, session_dir: Path) -> dict[str, Any]:
        manifest_path = session_dir / "manifest.json"
        self._reject_link(manifest_path)
        try:
            loaded = json.loads(manifest_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise RecordingStoreError("recording manifest is missing") from None
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise CorruptRecordingError("recording manifest is corrupt") from error
        if not isinstance(loaded, dict):
            raise CorruptRecordingError("recording manifest is corrupt")
        return loaded

    @staticmethod
    def _validate_summary(manifest: Mapping[str, Any]) -> RecordingSessionSummary:
        try:
            summary = RecordingSessionSummary.model_validate(manifest)
        except ValidationError as error:
            raise CorruptRecordingError("recording summary is corrupt") from error
        if not summary.finalized:
            raise RecordingNotFinalizedError("recording session is not finalized")
        return summary

    def _event_line_count(self, session_dir: Path) -> int:
        events_path = session_dir / "events.jsonl"
        self._reject_link(events_path)
        if not events_path.exists():
            return 0
        with events_path.open("rb") as stream:
            return sum(1 for _ in stream)

    @staticmethod
    def _retention_metadata(manifest: Mapping[str, Any]) -> tuple[bool, datetime]:
        if manifest.get("finalized") is True:
            summary = JsonlRecordingStore._validate_summary(manifest)
            return summary.retained, summary.finished_at or summary.started_at
        try:
            session = RecordingSession.model_validate(manifest)
        except ValidationError as error:
            raise CorruptRecordingError("recording manifest is corrupt") from error
        return session.retained, session.started_at

    @staticmethod
    def _reject_link(path: Path) -> None:
        if _is_link_like(path):
            raise UnsafeRecordingPathError("recording files must not be links")

    @staticmethod
    def _atomic_write_json(path: Path, value: Mapping[str, Any]) -> None:
        JsonlRecordingStore._reject_link(path)
        temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
        try:
            with temporary.open("x", encoding="utf-8", newline="\n") as stream:
                json.dump(value, stream, ensure_ascii=False, separators=(",", ":"))
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            if temporary.exists():
                temporary.unlink()

    @staticmethod
    def _remove_tree_without_following_links(path: Path) -> None:
        if _is_link_like(path):
            raise UnsafeRecordingPathError("recording session path must not be a link")
        with os.scandir(path) as entries:
            for entry in entries:
                child = Path(entry.path)
                if entry.is_symlink() or _is_link_like(child):
                    child.unlink()
                elif entry.is_dir(follow_symlinks=False):
                    JsonlRecordingStore._remove_tree_without_following_links(child)
                else:
                    child.unlink()
        path.rmdir()


__all__ = [
    "CorruptRecordingError",
    "JsonlRecordingStore",
    "RecordingNotFinalizedError",
    "RecordingStoreError",
    "RetentionFailure",
    "RetentionSummary",
    "UnsafeRecordingPathError",
]
