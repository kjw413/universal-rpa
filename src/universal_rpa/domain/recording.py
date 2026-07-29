from __future__ import annotations

import threading
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from math import isfinite
from typing import Annotated, Any, Literal
from uuid import UUID, uuid4

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    PlainSerializer,
    WithJsonSchema,
    field_validator,
    model_validator,
)

from universal_rpa.domain.targets import NormalizedRect, UiaSelector
from universal_rpa.domain.types import (
    FrozenJsonObject,
    FrozenMapping,
    JsonValue,
    deep_freeze_json,
    thaw_json,
)


class RawEventType(StrEnum):
    MOUSE_DOWN = "mouse_down"
    MOUSE_UP = "mouse_up"
    MOUSE_MOVE = "mouse_move"
    MOUSE_WHEEL = "mouse_wheel"
    KEY_DOWN = "key_down"
    KEY_UP = "key_up"


_KEYBOARD_EVENTS = frozenset({RawEventType.KEY_DOWN, RawEventType.KEY_UP})
_REDACTED_PAYLOAD: dict[str, JsonValue] = {"redacted": True}


def _require_utc(value: datetime, *, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError(f"{field_name} must use UTC")
    return value


def _freeze_json_object(value: object) -> FrozenJsonObject:
    try:
        frozen = deep_freeze_json(value)  # type: ignore[arg-type]
    except TypeError as error:
        raise ValueError("event payload must contain finite JSON values") from error
    if not isinstance(frozen, FrozenMapping):
        raise ValueError("event payload must be a JSON object")
    return frozen


ImmutableJsonObject = Annotated[
    FrozenJsonObject,
    BeforeValidator(_freeze_json_object),
    PlainSerializer(thaw_json, return_type=dict[str, JsonValue]),
    WithJsonSchema({"type": "object", "additionalProperties": True}),
]


@dataclass(frozen=True, slots=True)
class EventFocusSnapshot:
    foreground_hwnd: int
    focused_hwnd: int | None
    foreground_process_id: int
    cached_uia_runtime_id: tuple[int, ...] | None
    focus_event_time_ms: int
    cache_generation: int
    cache_confirmed: bool

    def __post_init__(self) -> None:
        if self.foreground_process_id <= 0:
            raise ValueError("foreground process id must be positive")
        if self.focus_event_time_ms < 0 or self.cache_generation < 0:
            raise ValueError("focus timestamps and generation must be nonnegative")
        if self.cached_uia_runtime_id is not None:
            object.__setattr__(
                self,
                "cached_uia_runtime_id",
                tuple(self.cached_uia_runtime_id),
            )


@dataclass(frozen=True, slots=True)
class KeyChord:
    key: str
    modifiers: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        if not self.key:
            raise ValueError("key chord requires a key")
        object.__setattr__(self, "modifiers", frozenset(self.modifiers))


class SensitiveKeyToken:
    __slots__ = ("__key", "__lock", "__text", "__usable")

    def __init__(self, *, key: str, text: str | None) -> None:
        self.__key = key
        self.__text = text
        self.__usable = True
        self.__lock = threading.Lock()

    @classmethod
    def create(cls, *, key: str, text: str | None) -> SensitiveKeyToken:
        if not isinstance(key, str) or not key:
            raise ValueError("sensitive key token requires a nonblank key")
        if text is not None and not isinstance(text, str):
            raise TypeError("sensitive key text must be text or None")
        return cls(key=key, text=text)

    def reveal_once(self) -> tuple[str, str | None]:
        with self.__lock:
            if not self.__usable:
                raise RuntimeError("sensitive key token is no longer available")
            self.__usable = False
            key, text = self.__key, self.__text
            self.__key = ""
            self.__text = None
            return key, text

    def discard(self) -> None:
        with self.__lock:
            self.__usable = False
            self.__key = ""
            self.__text = None

    def __repr__(self) -> str:
        return "SensitiveKeyToken(<redacted>)"


@dataclass(frozen=True, slots=True)
class NativeInputEvent:
    monotonic_ns: int
    wall_time_utc: datetime
    hook_time_ms: int
    event_type: RawEventType
    focus: EventFocusSnapshot
    payload: FrozenJsonObject
    key_token: SensitiveKeyToken | None = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self.monotonic_ns < 0 or self.hook_time_ms < 0:
            raise ValueError("native event times must be nonnegative")
        object.__setattr__(
            self,
            "wall_time_utc",
            _require_utc(self.wall_time_utc, field_name="wall_time_utc"),
        )
        object.__setattr__(self, "event_type", RawEventType(self.event_type))
        object.__setattr__(self, "payload", _freeze_json_object(self.payload))
        if self.event_type not in _KEYBOARD_EVENTS and self.key_token is not None:
            raise ValueError("only keyboard events may carry a sensitive key token")


class RecordingTarget(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    process_id: int = Field(gt=0)
    process_executable: str = Field(min_length=1)
    top_level_hwnd: int
    window_title: str
    window_class: str = Field(min_length=1)


class WindowContextSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    foreground_hwnd: int
    focused_hwnd: int | None
    process_id: int = Field(gt=0)
    process_executable: str = Field(min_length=1)
    top_level_hwnd: int
    window_title: str
    window_class: str = Field(min_length=1)
    focused_runtime_id: tuple[int, ...] | None
    selected_top_level_hwnd: int
    owned_by_selected_window: bool
    context_confident: bool


class TargetSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    selector_candidates: tuple[UiaSelector, ...]
    focused_runtime_id: tuple[int, ...] | None
    editable: bool
    is_password: bool
    observed_value: str | None
    bounds: NormalizedRect | None


class RecordingEnvironmentSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    client_left: int
    client_top: int
    client_width: int = Field(gt=0)
    client_height: int = Field(gt=0)
    dpi_x: int = Field(gt=0)
    dpi_y: int = Field(gt=0)
    monitor_scale: float = Field(gt=0)
    monitor_id: str
    double_click_time_ms: int = Field(gt=0)
    drag_width_px: int = Field(gt=0)
    drag_height_px: int = Field(gt=0)

    @field_validator("monitor_scale")
    @classmethod
    def monitor_scale_is_finite(cls, value: float) -> float:
        if not isfinite(value):
            raise ValueError("monitor scale must be finite")
        return value


class RecordingSession(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    session_id: UUID
    target: RecordingTarget
    started_at: datetime
    retained: bool = False

    @field_validator("started_at")
    @classmethod
    def started_at_is_utc(cls, value: datetime) -> datetime:
        return _require_utc(value, field_name="started_at")


class RecordingSessionSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    session_id: UUID
    finalized: bool
    incomplete: bool
    retained: bool
    event_count: int = Field(ge=0)
    dropped_event_count: int = Field(ge=0)
    started_at: datetime
    finished_at: datetime | None

    @field_validator("started_at")
    @classmethod
    def started_at_is_utc(cls, value: datetime) -> datetime:
        return _require_utc(value, field_name="started_at")

    @field_validator("finished_at")
    @classmethod
    def finished_at_is_utc(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        return _require_utc(value, field_name="finished_at")

    @model_validator(mode="after")
    def timestamps_and_finalization_are_consistent(self) -> RecordingSessionSummary:
        if self.finished_at is not None and self.finished_at < self.started_at:
            raise ValueError("finished_at cannot precede started_at")
        if self.finalized and self.finished_at is None:
            raise ValueError("finalized session requires finished_at")
        return self


def _snapshot_value(snapshot: object, field_name: str, default: object = None) -> object:
    if isinstance(snapshot, Mapping):
        return snapshot.get(field_name, default)
    return getattr(snapshot, field_name, default)


def _redact_target_snapshot(snapshot: object) -> object:
    if isinstance(snapshot, TargetSnapshot):
        return snapshot.model_copy(update={"observed_value": None})
    if isinstance(snapshot, Mapping):
        copied = dict(snapshot)
        copied["observed_value"] = None
        return copied
    return snapshot


class RawInputEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, arbitrary_types_allowed=True)

    schema_version: Literal["1"] = "1"
    session_id: UUID
    event_id: UUID
    monotonic_ns: int = Field(ge=0)
    wall_time_utc: datetime
    event_type: RawEventType
    payload: ImmutableJsonObject
    in_scope: bool
    capture_state: Literal["recording", "paused"]
    window_context: WindowContextSnapshot
    target_snapshot: TargetSnapshot | None
    environment_snapshot: RecordingEnvironmentSnapshot

    @model_validator(mode="before")
    @classmethod
    def redact_unsafe_keyboard_payload(cls, value: Any) -> Any:
        if not isinstance(value, Mapping):
            return value
        copied = dict(value)
        raw_event_type = copied.get("event_type")
        if not isinstance(raw_event_type, (str, RawEventType)):
            return copied
        try:
            event_type = RawEventType(raw_event_type)
        except ValueError:
            return copied
        if event_type not in _KEYBOARD_EVENTS:
            return copied

        target = copied.get("target_snapshot")
        window_context = copied.get("window_context")
        unsafe = (
            copied.get("capture_state") != "recording"
            or copied.get("in_scope") is not True
            or _snapshot_value(window_context, "context_confident", False) is not True
            or target is None
            or _snapshot_value(target, "is_password", False) is True
        )
        if unsafe:
            copied["payload"] = dict(_REDACTED_PAYLOAD)
            if target is not None:
                copied["target_snapshot"] = _redact_target_snapshot(target)
        return copied

    @field_validator("wall_time_utc")
    @classmethod
    def wall_time_is_utc(cls, value: datetime) -> datetime:
        return _require_utc(value, field_name="wall_time_utc")


def _event_identity_matches(
    native: NativeInputEvent,
    context: WindowContextSnapshot,
    target: TargetSnapshot | None,
) -> bool:
    return (
        native.focus.cache_confirmed
        and context.context_confident
        and context.owned_by_selected_window
        and context.foreground_hwnd == native.focus.foreground_hwnd
        and context.focused_hwnd == native.focus.focused_hwnd
        and context.process_id == native.focus.foreground_process_id
        and context.focused_runtime_id == native.focus.cached_uia_runtime_id
        and target is not None
        and target.focused_runtime_id == native.focus.cached_uia_runtime_id
    )


def enrich_and_sanitize_event(
    native: NativeInputEvent,
    *,
    session_id: UUID,
    context: WindowContextSnapshot,
    target: TargetSnapshot | None,
    environment: RecordingEnvironmentSnapshot,
    in_scope: bool,
    capture_state: Literal["recording", "paused"] = "recording",
) -> RawInputEvent:
    payload: FrozenJsonObject | Mapping[str, JsonValue] = native.payload
    sanitized_target = target

    if native.event_type in _KEYBOARD_EVENTS:
        key_token = native.key_token
        safe_to_reveal = (
            capture_state == "recording"
            and in_scope
            and _event_identity_matches(native, context, target)
            and target is not None
            and not target.is_password
            and key_token is not None
        )
        if safe_to_reveal:
            assert key_token is not None
            key, text = key_token.reveal_once()
            thawed_payload = thaw_json(native.payload)
            if not isinstance(thawed_payload, dict):
                raise TypeError("native event payload must be an object")
            revealed = dict(thawed_payload)
            revealed["key"] = key
            if text is not None:
                revealed["text"] = text
            payload = revealed
        else:
            if key_token is not None:
                key_token.discard()
            payload = _REDACTED_PAYLOAD
            if sanitized_target is not None:
                sanitized_target = sanitized_target.model_copy(update={"observed_value": None})

    return RawInputEvent(
        session_id=session_id,
        event_id=uuid4(),
        monotonic_ns=native.monotonic_ns,
        wall_time_utc=native.wall_time_utc,
        event_type=native.event_type,
        payload=payload,  # type: ignore[arg-type]
        in_scope=in_scope,
        capture_state=capture_state,
        window_context=context,
        target_snapshot=sanitized_target,
        environment_snapshot=environment,
    )


__all__ = [
    "EventFocusSnapshot",
    "KeyChord",
    "NativeInputEvent",
    "RawEventType",
    "RawInputEvent",
    "RecordingEnvironmentSnapshot",
    "RecordingSession",
    "RecordingSessionSummary",
    "RecordingTarget",
    "SensitiveKeyToken",
    "TargetSnapshot",
    "WindowContextSnapshot",
    "enrich_and_sanitize_event",
]
