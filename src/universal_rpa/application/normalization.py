from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import PureWindowsPath
from typing import Annotated, Literal, cast
from uuid import UUID, uuid4

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    PlainSerializer,
    WithJsonSchema,
)

from universal_rpa.domain.action_parameters import validate_builtin_action_parameters
from universal_rpa.domain.recording import RawEventType, RawInputEvent, TargetSnapshot
from universal_rpa.domain.targets import (
    CoordinateFallback,
    RelativePoint,
    TargetSpec,
    WindowsTarget,
)
from universal_rpa.domain.types import (
    FrozenJsonObject,
    FrozenMapping,
    JsonValue,
    deep_freeze_json,
    thaw_json,
)


def _freeze_json_object(value: object) -> FrozenJsonObject:
    try:
        frozen = deep_freeze_json(value)  # type: ignore[arg-type]
    except TypeError as error:
        raise ValueError("candidate metadata must contain finite JSON values") from error
    if not isinstance(frozen, FrozenMapping):
        raise ValueError("candidate metadata must be a JSON object")
    return frozen


CandidateJsonObject = Annotated[
    FrozenJsonObject,
    BeforeValidator(_freeze_json_object),
    PlainSerializer(thaw_json, return_type=dict[str, JsonValue]),
    WithJsonSchema({"type": "object", "additionalProperties": True}),
]


class CandidateLiteralValue(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    mode: Literal["literal"] = "literal"
    display_value: str | None


class CandidateSecretValue(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    mode: Literal["secret_ref"] = "secret_ref"
    display_value: None = None
    credential_ref_required: Literal[True] = True


CandidateValue = Annotated[
    CandidateLiteralValue | CandidateSecretValue,
    Field(discriminator="mode"),
]


class CandidateSuggestion(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, arbitrary_types_allowed=True)

    kind: Literal["date_variable", "number_variable", "path_variable", "wait_candidate"]
    source_event_ids: tuple[UUID, ...]
    details: CandidateJsonObject = Field(default_factory=FrozenMapping.empty)


class NormalizationWarning(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    code: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    source_event_ids: tuple[UUID, ...]
    safe_message: str


class StepCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, arbitrary_types_allowed=True)

    candidate_id: UUID
    session_id: UUID
    action_type: str
    source_event_ids: tuple[UUID, ...]
    first_monotonic_ns: int = Field(ge=0)
    target: TargetSpec | None
    target_snapshot: TargetSnapshot | None
    value: CandidateValue | None = None
    parameters: CandidateJsonObject = Field(default_factory=FrozenMapping.empty)
    suggestions: tuple[CandidateSuggestion, ...] = ()
    requires_confirmation: bool = False


class NormalizationResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    session_id: UUID
    candidates: tuple[StepCandidate, ...]
    warnings: tuple[NormalizationWarning, ...] = ()
    suggestions: tuple[CandidateSuggestion, ...] = ()


class MouseThresholds(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    double_click_time_ms: int = Field(default=500, gt=0)
    double_click_width_px: int = Field(default=4, gt=0)
    double_click_height_px: int = Field(default=4, gt=0)
    drag_width_px: int = Field(default=4, gt=0)
    drag_height_px: int = Field(default=4, gt=0)


@dataclass(frozen=True, slots=True)
class MaterializedWindowsTarget:
    target: TargetSpec | None
    requires_confirmation: bool


@dataclass(slots=True)
class _MouseGesture:
    down: RawInputEvent
    moves: list[RawInputEvent] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class _Click:
    down: RawInputEvent
    up: RawInputEvent
    button: str


def _payload(event: RawInputEvent) -> dict[str, JsonValue]:
    value = thaw_json(event.payload)
    if not isinstance(value, dict):
        raise TypeError("raw event payload must be an object")
    return value


def _payload_int(payload: Mapping[str, JsonValue], *names: str) -> int | None:
    for name in names:
        value = payload.get(name)
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    return None


def _mouse_point(event: RawInputEvent) -> tuple[int, int] | None:
    payload = _payload(event)
    x = _payload_int(payload, "x", "screen_x")
    y = _payload_int(payload, "y", "screen_y")
    return (x, y) if x is not None and y is not None else None


def _mouse_button(event: RawInputEvent) -> str | None:
    value = _payload(event).get("button")
    return str(value).casefold() if isinstance(value, str) else None


def _relative_point(
    event: RawInputEvent,
    action_point: RelativePoint | tuple[int, int] | None,
) -> RelativePoint | None:
    if isinstance(action_point, RelativePoint):
        return action_point
    environment = event.environment_snapshot
    screen_point = action_point if action_point is not None else _mouse_point(event)
    if screen_point is not None:
        x, y = screen_point
        return RelativePoint(
            x=min(max((x - environment.client_left) / environment.client_width, 0.0), 1.0),
            y=min(max((y - environment.client_top) / environment.client_height, 0.0), 1.0),
        )
    bounds = event.target_snapshot.bounds if event.target_snapshot is not None else None
    if bounds is not None:
        return RelativePoint(
            x=round(bounds.x + bounds.width / 2, 12),
            y=round(bounds.y + bounds.height / 2, 12),
        )
    return None


def materialize_windows_target(
    event: RawInputEvent,
    action_point: RelativePoint | tuple[int, int] | None = None,
) -> MaterializedWindowsTarget:
    context = event.window_context
    environment = event.environment_snapshot
    point = _relative_point(event, action_point)
    if (
        not context.process_executable.strip()
        or not context.window_class.strip()
        or environment.client_width <= 0
        or environment.client_height <= 0
        or point is None
    ):
        return MaterializedWindowsTarget(None, True)

    snapshot = event.target_snapshot
    selectors = snapshot.selector_candidates if snapshot is not None else ()
    selector = selectors[0] if len(selectors) == 1 else None
    requires_confirmation = len(selectors) != 1 or not context.context_confident
    target_region = snapshot.bounds if snapshot is not None else None
    mandatory_regions = (
        (target_region,)
        if snapshot is not None
        and target_region is not None
        and (snapshot.is_password or snapshot.editable)
        else ()
    )
    absolute = _mouse_point(event)
    windows_target = WindowsTarget(
        selector=selector,
        coordinate_fallback=CoordinateFallback(
            recorded_process_executable=PureWindowsPath(context.process_executable).name,
            recorded_window_class=context.window_class,
            point=point,
            recorded_dpi_x=environment.dpi_x,
            recorded_dpi_y=environment.dpi_y,
            recorded_client_width=environment.client_width,
            recorded_client_height=environment.client_height,
        ),
        target_region=target_region,
        mandatory_sensitive_regions=mandatory_regions,
        user_sensitive_regions=(),
        diagnostic_absolute_x=absolute[0] if absolute is not None else None,
        diagnostic_absolute_y=absolute[1] if absolute is not None else None,
    )
    return MaterializedWindowsTarget(
        TargetSpec.model_validate(
            {"adapter_id": "windows", "payload": windows_target.model_dump(mode="json")}
        ),
        requires_confirmation,
    )


def _candidate(
    *,
    event: RawInputEvent,
    action_type: str,
    source_events: Sequence[RawInputEvent],
    action_point: RelativePoint | tuple[int, int] | None,
    parameters: Mapping[str, JsonValue],
) -> StepCandidate:
    materialized = materialize_windows_target(event, action_point)
    validated_parameters = validate_builtin_action_parameters(action_type, parameters)
    return StepCandidate(
        candidate_id=uuid4(),
        session_id=event.session_id,
        action_type=action_type,
        source_event_ids=tuple(item.event_id for item in source_events),
        first_monotonic_ns=min(item.monotonic_ns for item in source_events),
        target=materialized.target,
        target_snapshot=event.target_snapshot,
        parameters=validated_parameters,
        requires_confirmation=materialized.requires_confirmation,
    )


def _warning(code: str, events: Sequence[RawInputEvent], message: str) -> NormalizationWarning:
    return NormalizationWarning(
        code=code,
        source_event_ids=tuple(event.event_id for event in events),
        safe_message=message,
    )


def _same_target(left: RawInputEvent, right: RawInputEvent) -> bool:
    return (
        left.window_context.top_level_hwnd == right.window_context.top_level_hwnd
        and left.target_snapshot == right.target_snapshot
    )


def _qualifies_as_double_click(
    first: _Click,
    second: _Click,
    thresholds: MouseThresholds,
) -> bool:
    if first.button != "left" or second.button != "left":
        return False
    if not _same_target(first.down, second.down):
        return False
    first_point = _mouse_point(first.down)
    second_point = _mouse_point(second.down)
    if first_point is None or second_point is None:
        return False
    gap_ns = second.down.monotonic_ns - first.up.monotonic_ns
    return (
        0 <= gap_ns <= thresholds.double_click_time_ms * 1_000_000
        and abs(second_point[0] - first_point[0]) <= thresholds.double_click_width_px
        and abs(second_point[1] - first_point[1]) <= thresholds.double_click_height_px
    )


def _is_drag(
    down: RawInputEvent,
    moves: Sequence[RawInputEvent],
    up: RawInputEvent,
    thresholds: MouseThresholds,
) -> bool:
    start = _mouse_point(down)
    if start is None:
        return False
    for event in (*moves, up):
        point = _mouse_point(event)
        if point is not None and (
            abs(point[0] - start[0]) > thresholds.drag_width_px
            or abs(point[1] - start[1]) > thresholds.drag_height_px
        ):
            return True
    return False


def _ensure_one_session(events: Sequence[RawInputEvent]) -> UUID:
    if not events:
        raise ValueError("normalization requires at least one raw event")
    session_id = events[0].session_id
    if any(event.session_id != session_id for event in events):
        raise ValueError("raw events must belong to one recording session")
    return session_id


def normalize_mouse_events(
    events: Sequence[RawInputEvent],
    *,
    thresholds: MouseThresholds,
) -> NormalizationResult:
    session_id = _ensure_one_session(events)
    active: dict[str, _MouseGesture] = {}
    completed_clicks: list[_Click] = []
    standalone: list[StepCandidate] = []
    warnings: list[NormalizationWarning] = []

    for event in events:
        if not event.in_scope or event.capture_state != "recording":
            continue
        if event.event_type is RawEventType.MOUSE_DOWN:
            button = _mouse_button(event)
            if button is None:
                continue
            previous = active.get(button)
            if previous is not None:
                warnings.append(
                    _warning(
                        "incomplete_mouse_gesture",
                        (previous.down, *previous.moves),
                        "마우스 버튼 놓기 이벤트가 없어 동작 후보를 만들지 않았습니다.",
                    )
                )
            active[button] = _MouseGesture(event)
        elif event.event_type is RawEventType.MOUSE_MOVE:
            payload = _payload(event)
            buttons_value = payload.get("buttons")
            buttons = (
                {str(item).casefold() for item in cast(list[object], buttons_value)}
                if isinstance(buttons_value, list)
                else set(active)
            )
            for button in buttons:
                gesture = active.get(button)
                if gesture is not None:
                    gesture.moves.append(event)
        elif event.event_type is RawEventType.MOUSE_UP:
            button = _mouse_button(event)
            gesture = active.pop(button, None) if button is not None else None
            if gesture is None or button is None:
                warnings.append(
                    _warning(
                        "incomplete_mouse_gesture",
                        (event,),
                        "대응하는 마우스 누르기 이벤트가 없어 동작 후보를 만들지 않았습니다.",
                    )
                )
                continue
            source_events = (gesture.down, *gesture.moves, event)
            if _is_drag(gesture.down, gesture.moves, event, thresholds):
                end_point = _relative_point(event, _mouse_point(event))
                if end_point is None:
                    warnings.append(
                        _warning(
                            "incomplete_mouse_gesture",
                            source_events,
                            "드래그 종료 좌표가 없어 동작 후보를 만들지 않았습니다.",
                        )
                    )
                    continue
                standalone.append(
                    _candidate(
                        event=gesture.down,
                        action_type="windows.drag",
                        source_events=source_events,
                        action_point=_mouse_point(gesture.down),
                        parameters={
                            "button": button,
                            "end_point": end_point.model_dump(mode="json"),
                        },
                    )
                )
            else:
                completed_clicks.append(_Click(gesture.down, event, button))
        elif event.event_type is RawEventType.MOUSE_WHEEL:
            payload = _payload(event)
            horizontal = _payload_int(payload, "horizontal_delta", "delta_x", "dx") or 0
            vertical = _payload_int(payload, "vertical_delta", "delta_y", "dy") or 0
            if horizontal == 0 and vertical == 0:
                continue
            standalone.append(
                _candidate(
                    event=event,
                    action_type="windows.scroll",
                    source_events=(event,),
                    action_point=_mouse_point(event),
                    parameters={
                        "horizontal_delta": horizontal,
                        "vertical_delta": vertical,
                    },
                )
            )

    for gesture in active.values():
        warnings.append(
            _warning(
                "incomplete_mouse_gesture",
                (gesture.down, *gesture.moves),
                "마우스 버튼 놓기 이벤트가 없어 동작 후보를 만들지 않았습니다.",
            )
        )

    click_candidates: list[StepCandidate] = []
    index = 0
    while index < len(completed_clicks):
        first = completed_clicks[index]
        if index + 1 < len(completed_clicks):
            second = completed_clicks[index + 1]
            if _qualifies_as_double_click(first, second, thresholds):
                click_candidates.append(
                    _candidate(
                        event=first.down,
                        action_type="windows.double_click",
                        source_events=(first.down, first.up, second.down, second.up),
                        action_point=_mouse_point(first.down),
                        parameters={"button": first.button},
                    )
                )
                index += 2
                continue
        click_candidates.append(
            _candidate(
                event=first.down,
                action_type="windows.click",
                source_events=(first.down, first.up),
                action_point=_mouse_point(first.down),
                parameters={"button": first.button},
            )
        )
        index += 1

    candidates = tuple(
        sorted(
            (*standalone, *click_candidates),
            key=lambda item: (item.first_monotonic_ns, str(item.candidate_id)),
        )
    )
    return NormalizationResult(
        session_id=session_id,
        candidates=candidates,
        warnings=tuple(warnings),
    )


from universal_rpa.application.keyboard_normalization import (  # noqa: E402, I001
    COMMAND_KEYS,
    KeyboardNormalizationConfig,
    normalize_keyboard_events,
    suggest_variable_types,
)


__all__ = [
    "COMMAND_KEYS",
    "CandidateLiteralValue",
    "CandidateSecretValue",
    "CandidateSuggestion",
    "CandidateValue",
    "KeyboardNormalizationConfig",
    "MaterializedWindowsTarget",
    "MouseThresholds",
    "NormalizationResult",
    "NormalizationWarning",
    "StepCandidate",
    "materialize_windows_target",
    "normalize_keyboard_events",
    "normalize_mouse_events",
    "suggest_variable_types",
]
