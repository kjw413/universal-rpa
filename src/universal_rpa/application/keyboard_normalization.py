from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

from universal_rpa.application.normalization import (
    CandidateLiteralValue,
    CandidateSecretValue,
    CandidateSuggestion,
    NormalizationResult,
    StepCandidate,
    materialize_windows_target,
)
from universal_rpa.domain.action_parameters import validate_builtin_action_parameters
from universal_rpa.domain.recording import KeyChord, RawEventType, RawInputEvent
from universal_rpa.domain.types import JsonValue, thaw_json

_MODIFIER_ORDER = ("ctrl", "alt", "shift", "win")
_MODIFIER_KEYS = frozenset(
    {
        "ctrl",
        "ctrl_l",
        "ctrl_r",
        "alt",
        "alt_l",
        "alt_r",
        "shift",
        "shift_l",
        "shift_r",
        "win",
        "cmd",
        "cmd_l",
        "cmd_r",
    }
)
_MODIFIER_CANONICAL = {
    "ctrl": "ctrl",
    "ctrl_l": "ctrl",
    "ctrl_r": "ctrl",
    "alt": "alt",
    "alt_l": "alt",
    "alt_r": "alt",
    "shift": "shift",
    "shift_l": "shift",
    "shift_r": "shift",
    "win": "win",
    "cmd": "win",
    "cmd_l": "win",
    "cmd_r": "win",
}

COMMAND_KEYS = frozenset(
    {"enter", "tab", "esc", "left", "right", "up", "down"}
    | {f"f{number}" for number in range(1, 25)}
)


class KeyboardNormalizationConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, arbitrary_types_allowed=True)

    text_commit_gap_ns: int = Field(default=1_000_000_000, gt=0)
    toggle_hotkey: KeyChord = Field(
        default_factory=lambda: KeyChord("f11", frozenset({"ctrl", "shift"}))
    )
    stop_hotkey: KeyChord = Field(
        default_factory=lambda: KeyChord("f12", frozenset({"ctrl", "shift"}))
    )


@dataclass(slots=True)
class _TextGroup:
    first: RawInputEvent
    events: list[RawInputEvent] = field(default_factory=list)
    typed_text: list[str] = field(default_factory=list)
    probable_ime: bool = False

    def __post_init__(self) -> None:
        self.events.append(self.first)

    @property
    def last(self) -> RawInputEvent:
        return self.events[-1]


def _payload(event: RawInputEvent) -> dict[str, JsonValue]:
    value = thaw_json(event.payload)
    if not isinstance(value, dict):
        raise TypeError("raw event payload must be an object")
    return value


def _key_and_text(event: RawInputEvent) -> tuple[str | None, str | None]:
    payload = _payload(event)
    key_value = payload.get("key")
    text_value = payload.get("text")
    key = key_value.casefold() if isinstance(key_value, str) and key_value else None
    text = text_value if isinstance(text_value, str) else None
    return key, text


def _is_probable_ime(event: RawInputEvent) -> bool:
    payload = _payload(event)
    return payload.get("ime_active") is True or payload.get("ime_composing") is True


def _modifier_name(key: str) -> str | None:
    return _MODIFIER_CANONICAL.get(key)


def _target_identity(event: RawInputEvent) -> tuple[object, ...]:
    snapshot = event.target_snapshot
    return (
        event.window_context.top_level_hwnd,
        event.window_context.focused_hwnd,
        snapshot.focused_runtime_id if snapshot is not None else None,
        snapshot.selector_candidates if snapshot is not None else (),
        snapshot.is_password if snapshot is not None else False,
    )


def _same_text_group(
    group: _TextGroup,
    event: RawInputEvent,
    config: KeyboardNormalizationConfig,
) -> bool:
    return (
        _target_identity(group.first) == _target_identity(event)
        and 0 <= event.monotonic_ns - group.last.monotonic_ns < config.text_commit_gap_ns
    )


def _source_ids(events: Sequence[RawInputEvent]) -> tuple[UUID, ...]:
    return tuple(event.event_id for event in events)


def _candidate(
    *,
    event: RawInputEvent,
    action_type: str,
    source_events: Sequence[RawInputEvent],
    parameters: Mapping[str, JsonValue],
    value: CandidateLiteralValue | CandidateSecretValue | None = None,
    suggestions: tuple[CandidateSuggestion, ...] = (),
    force_confirmation: bool = False,
) -> StepCandidate:
    materialized = materialize_windows_target(event)
    return StepCandidate(
        candidate_id=uuid4(),
        session_id=event.session_id,
        action_type=action_type,
        source_event_ids=_source_ids(source_events),
        first_monotonic_ns=min(item.monotonic_ns for item in source_events),
        target=materialized.target,
        target_snapshot=event.target_snapshot,
        value=value,
        parameters=validate_builtin_action_parameters(action_type, parameters),
        suggestions=suggestions,
        requires_confirmation=materialized.requires_confirmation or force_confirmation,
    )


_ISO_DATE = re.compile(r"^\d{4}[-/.]\d{1,2}[-/.]\d{1,2}$")
_NUMBER = re.compile(r"^[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?$")
_WINDOWS_PATH = re.compile(r"^(?:[A-Za-z]:[\\/]|\\\\).+")


def suggest_variable_types(
    text: str,
    *,
    source_event_ids: tuple[UUID, ...] = (),
) -> tuple[CandidateSuggestion, ...]:
    kinds: list[Literal["date_variable", "number_variable", "path_variable"]] = []
    stripped = text.strip()
    if _ISO_DATE.fullmatch(stripped):
        kinds.append("date_variable")
    elif _NUMBER.fullmatch(stripped):
        kinds.append("number_variable")
    if _WINDOWS_PATH.fullmatch(stripped):
        kinds.append("path_variable")
    return tuple(
        CandidateSuggestion(kind=kind, source_event_ids=source_event_ids) for kind in kinds
    )


def _final_observed_value(group: _TextGroup) -> str | None:
    for event in reversed(group.events):
        snapshot = event.target_snapshot
        if (
            snapshot is not None
            and not snapshot.is_password
            and snapshot.observed_value is not None
        ):
            return snapshot.observed_value
    return None


def _flush_text_group(group: _TextGroup) -> StepCandidate:
    observed = _final_observed_value(group)
    display_value: str | None
    if observed is not None:
        display_value = observed
    elif not group.probable_ime and group.typed_text:
        display_value = "".join(group.typed_text)
    else:
        display_value = None
    source_ids = _source_ids(group.events)
    suggestions = (
        suggest_variable_types(display_value, source_event_ids=source_ids)
        if display_value is not None
        else ()
    )
    return _candidate(
        event=group.first,
        action_type="windows.set_text",
        source_events=group.events,
        parameters={},
        value=CandidateLiteralValue(display_value=display_value),
        suggestions=suggestions,
        force_confirmation=display_value is None,
    )


def _flush_secret_group(group: _TextGroup) -> StepCandidate:
    return _candidate(
        event=group.first,
        action_type="windows.set_text",
        source_events=group.events,
        parameters={},
        value=CandidateSecretValue(),
        force_confirmation=True,
    )


def _is_reserved_control(
    key: str,
    modifiers: frozenset[str],
    config: KeyboardNormalizationConfig,
) -> bool:
    for chord in (config.toggle_hotkey, config.stop_hotkey):
        if key == chord.key.casefold() and chord.modifiers.issubset(modifiers):
            return True
    return False


def _ensure_one_session(events: Sequence[RawInputEvent]) -> UUID:
    if not events:
        raise ValueError("normalization requires at least one raw event")
    session_id = events[0].session_id
    if any(event.session_id != session_id for event in events):
        raise ValueError("raw events must belong to one recording session")
    return session_id


def normalize_keyboard_events(
    events: Sequence[RawInputEvent],
    *,
    config: KeyboardNormalizationConfig | None = None,
) -> NormalizationResult:
    selected_config = config or KeyboardNormalizationConfig()
    session_id = _ensure_one_session(events)
    candidates: list[StepCandidate] = []
    modifiers: set[str] = set()
    modifier_sources: dict[str, RawInputEvent] = {}
    text_group: _TextGroup | None = None
    secret_group: _TextGroup | None = None

    def flush_groups() -> None:
        nonlocal text_group, secret_group
        if text_group is not None:
            candidates.append(_flush_text_group(text_group))
            text_group = None
        if secret_group is not None:
            candidates.append(_flush_secret_group(secret_group))
            secret_group = None

    for event in events:
        if event.event_type not in {RawEventType.KEY_DOWN, RawEventType.KEY_UP}:
            continue
        if not event.in_scope or event.capture_state != "recording":
            flush_groups()
            continue

        snapshot = event.target_snapshot
        if snapshot is not None and snapshot.is_password:
            if secret_group is None or not _same_text_group(secret_group, event, selected_config):
                flush_groups()
                secret_group = _TextGroup(event)
            elif secret_group.last.event_id != event.event_id:
                secret_group.events.append(event)
            secret_group.probable_ime = True
            continue

        key, text = _key_and_text(event)
        if key is None:
            flush_groups()
            continue
        modifier = _modifier_name(key)
        if event.event_type is RawEventType.KEY_UP:
            if modifier is not None:
                modifiers.discard(modifier)
                modifier_sources.pop(modifier, None)
            continue
        if modifier is not None:
            modifiers.add(modifier)
            modifier_sources[modifier] = event
            continue

        active_modifiers = frozenset(modifiers)
        if _is_reserved_control(key, active_modifiers, selected_config):
            flush_groups()
            continue
        if active_modifiers:
            flush_groups()
            canonical_modifiers = tuple(
                modifier_name
                for modifier_name in _MODIFIER_ORDER
                if modifier_name in active_modifiers
            )
            source_events = (
                *tuple(
                    modifier_sources[name] for name in _MODIFIER_ORDER if name in modifier_sources
                ),
                event,
            )
            candidates.append(
                _candidate(
                    event=event,
                    action_type="windows.hotkey",
                    source_events=source_events,
                    parameters={"key": key, "modifiers": list(canonical_modifiers)},
                )
            )
            continue
        if key in COMMAND_KEYS:
            flush_groups()
            candidates.append(
                _candidate(
                    event=event,
                    action_type="windows.press_key",
                    source_events=(event,),
                    parameters={"key": key},
                )
            )
            continue

        editable = snapshot is not None and snapshot.editable
        if not editable:
            flush_groups()
            candidates.append(
                _candidate(
                    event=event,
                    action_type="windows.press_key",
                    source_events=(event,),
                    parameters={"key": key},
                )
            )
            continue
        if text_group is None or not _same_text_group(text_group, event, selected_config):
            flush_groups()
            text_group = _TextGroup(event)
        elif text_group.last.event_id != event.event_id:
            text_group.events.append(event)
        if text is not None:
            text_group.typed_text.append(text)
        text_group.probable_ime = text_group.probable_ime or _is_probable_ime(event)

    flush_groups()
    ordered = tuple(
        sorted(candidates, key=lambda item: (item.first_monotonic_ns, str(item.candidate_id)))
    )
    suggestions = tuple(suggestion for candidate in ordered for suggestion in candidate.suggestions)
    return NormalizationResult(
        session_id=session_id,
        candidates=ordered,
        suggestions=suggestions,
    )


__all__ = [
    "COMMAND_KEYS",
    "KeyboardNormalizationConfig",
    "normalize_keyboard_events",
    "suggest_variable_types",
]
