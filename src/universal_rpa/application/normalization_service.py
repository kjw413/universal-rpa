from __future__ import annotations

import itertools
from collections.abc import Sequence
from uuid import UUID, uuid5

from universal_rpa.application.normalization import (
    CandidateLiteralValue,
    CandidateSuggestion,
    MouseThresholds,
    NormalizationResult,
    NormalizationWarning,
    StepCandidate,
    normalize_keyboard_events,
    normalize_mouse_events,
)
from universal_rpa.domain.action_parameters import validate_builtin_action_parameters
from universal_rpa.domain.recording import RawEventType
from universal_rpa.ports.repositories import RecordingStorePort

_MOUSE_EVENTS = frozenset(
    {
        RawEventType.MOUSE_DOWN,
        RawEventType.MOUSE_UP,
        RawEventType.MOUSE_MOVE,
        RawEventType.MOUSE_WHEEL,
    }
)
_KEYBOARD_EVENTS = frozenset({RawEventType.KEY_DOWN, RawEventType.KEY_UP})


class RecordingNotNormalizable(RuntimeError):
    pass


def _candidate_uuid(
    session_id: UUID,
    action_type: str,
    source_event_ids: Sequence[UUID],
    *,
    operation: str = "normalize",
) -> UUID:
    source = ":".join(str(event_id) for event_id in source_event_ids)
    return uuid5(session_id, f"{operation}:{action_type}:{source}")


def _deterministic_candidate(candidate: StepCandidate) -> StepCandidate:
    return candidate.model_copy(
        update={
            "candidate_id": _candidate_uuid(
                candidate.session_id,
                candidate.action_type,
                candidate.source_event_ids,
            )
        }
    )


class NormalizationService:
    def __init__(self, *, wait_suggestion_threshold_ns: int = 5_000_000_000) -> None:
        if wait_suggestion_threshold_ns <= 0:
            raise ValueError("wait suggestion threshold must be positive")
        self._wait_suggestion_threshold_ns = wait_suggestion_threshold_ns

    def normalize_session(
        self,
        store: RecordingStorePort,
        session_id: UUID,
    ) -> NormalizationResult:
        try:
            summary = store.load_summary(session_id)
        except Exception as error:
            raise RecordingNotNormalizable("recording session is not finalized") from error
        if not summary.finalized or summary.incomplete:
            raise RecordingNotNormalizable("recording session is incomplete")

        try:
            events = tuple(store.iter_events(session_id))
        except Exception as error:
            raise RecordingNotNormalizable("recording events cannot be loaded") from error
        if any(event.session_id != session_id for event in events):
            raise RecordingNotNormalizable("recording event session identity mismatch")
        if len(events) != summary.event_count:
            raise RecordingNotNormalizable("recording event count does not match its manifest")

        mouse_events = tuple(event for event in events if event.event_type in _MOUSE_EVENTS)
        keyboard_events = tuple(event for event in events if event.event_type in _KEYBOARD_EVENTS)
        candidates: list[StepCandidate] = []
        warnings: list[NormalizationWarning] = []
        if mouse_events:
            environment = mouse_events[0].environment_snapshot
            mouse_result = normalize_mouse_events(
                mouse_events,
                thresholds=MouseThresholds(
                    double_click_time_ms=environment.double_click_time_ms,
                    double_click_width_px=environment.drag_width_px,
                    double_click_height_px=environment.drag_height_px,
                    drag_width_px=environment.drag_width_px,
                    drag_height_px=environment.drag_height_px,
                ),
            )
            candidates.extend(mouse_result.candidates)
            warnings.extend(mouse_result.warnings)
        if keyboard_events:
            keyboard_result = normalize_keyboard_events(keyboard_events)
            candidates.extend(keyboard_result.candidates)
            warnings.extend(keyboard_result.warnings)

        ordered = tuple(
            _deterministic_candidate(candidate)
            for candidate in sorted(
                candidates,
                key=lambda item: (
                    item.first_monotonic_ns,
                    tuple(str(event_id) for event_id in item.source_event_ids),
                    item.action_type,
                ),
            )
        )
        event_times = {event.event_id: event.monotonic_ns for event in events}
        suggestions = [suggestion for candidate in ordered for suggestion in candidate.suggestions]
        suggestions.extend(self._wait_suggestions(ordered, event_times))
        return NormalizationResult(
            session_id=session_id,
            candidates=ordered,
            warnings=tuple(warnings),
            suggestions=tuple(suggestions),
        )

    def merge(
        self,
        candidates: Sequence[StepCandidate],
        indices: Sequence[int],
    ) -> StepCandidate:
        selected_indices = tuple(indices)
        if len(selected_indices) < 2:
            raise ValueError("merge requires at least two candidates")
        if selected_indices != tuple(range(selected_indices[0], selected_indices[-1] + 1)):
            raise ValueError("only adjacent candidates can be merged")
        try:
            selected = tuple(candidates[index] for index in selected_indices)
        except IndexError as error:
            raise ValueError("merge candidate index is out of range") from error
        if len({candidate.session_id for candidate in selected}) != 1:
            raise ValueError("merged candidates must belong to one session")
        if any(candidate.target != selected[0].target for candidate in selected[1:]):
            raise ValueError("merged candidates must have the same target")

        action_types = {candidate.action_type for candidate in selected}
        if action_types == {"windows.set_text"}:
            last_value = selected[-1].value
            if not isinstance(last_value, CandidateLiteralValue):
                raise ValueError("only literal text candidates can be merged")
            action_type = "windows.set_text"
            parameters = validate_builtin_action_parameters(action_type, {})
            value = last_value
            suggestions = selected[-1].suggestions
        elif action_types == {"windows.click"} and len(selected) == 2:
            action_type = "windows.double_click"
            parameters = validate_builtin_action_parameters(action_type, selected[0].parameters)
            value = None
            suggestions = ()
        else:
            raise ValueError("candidate types are not merge-compatible")

        source_event_ids = tuple(
            event_id for candidate in selected for event_id in candidate.source_event_ids
        )
        session_id = selected[0].session_id
        return StepCandidate(
            candidate_id=_candidate_uuid(
                session_id,
                action_type,
                source_event_ids,
                operation="merge",
            ),
            session_id=session_id,
            action_type=action_type,
            source_event_ids=source_event_ids,
            first_monotonic_ns=min(candidate.first_monotonic_ns for candidate in selected),
            target=selected[0].target,
            target_snapshot=selected[0].target_snapshot,
            value=value,
            parameters=parameters,
            suggestions=suggestions,
            requires_confirmation=any(candidate.requires_confirmation for candidate in selected),
        )

    def split(
        self,
        candidate: StepCandidate,
        at_event_id: UUID,
    ) -> tuple[StepCandidate, StepCandidate]:
        try:
            boundary = candidate.source_event_ids.index(at_event_id)
        except ValueError as error:
            raise ValueError("split event is not part of the candidate") from error
        if boundary == 0:
            raise ValueError("split boundary must leave events on both sides")
        left_ids = candidate.source_event_ids[:boundary]
        right_ids = candidate.source_event_ids[boundary:]
        if not left_ids or not right_ids:
            raise ValueError("split boundary must leave events on both sides")

        values: tuple[CandidateLiteralValue | None, CandidateLiteralValue | None]
        if candidate.action_type == "windows.double_click":
            if len(candidate.source_event_ids) != 4 or boundary != 2:
                raise ValueError("double-click can only split between complete clicks")
            action_type = "windows.click"
            parameters = validate_builtin_action_parameters(action_type, candidate.parameters)
            values = (None, None)
            confirmation = candidate.requires_confirmation
        elif candidate.action_type == "windows.set_text":
            action_type = candidate.action_type
            parameters = validate_builtin_action_parameters(action_type, {})
            values = (
                CandidateLiteralValue(display_value=None),
                CandidateLiteralValue(display_value=None),
            )
            confirmation = True
        else:
            raise ValueError("candidate type cannot be split safely")

        def part(
            source_ids: tuple[UUID, ...], value: CandidateLiteralValue | None
        ) -> StepCandidate:
            return StepCandidate(
                candidate_id=_candidate_uuid(
                    candidate.session_id,
                    action_type,
                    source_ids,
                    operation="split",
                ),
                session_id=candidate.session_id,
                action_type=action_type,
                source_event_ids=source_ids,
                first_monotonic_ns=candidate.first_monotonic_ns,
                target=candidate.target,
                target_snapshot=candidate.target_snapshot,
                value=value,
                parameters=parameters,
                requires_confirmation=confirmation,
            )

        return part(left_ids, values[0]), part(right_ids, values[1])

    def _wait_suggestions(
        self,
        candidates: Sequence[StepCandidate],
        event_times: dict[UUID, int],
    ) -> tuple[CandidateSuggestion, ...]:
        suggestions: list[CandidateSuggestion] = []
        for previous, current in itertools.pairwise(candidates):
            previous_end = max(event_times[event_id] for event_id in previous.source_event_ids)
            current_start = min(event_times[event_id] for event_id in current.source_event_ids)
            gap_ns = current_start - previous_end
            if gap_ns < self._wait_suggestion_threshold_ns:
                continue
            suggestions.append(
                CandidateSuggestion.model_validate(
                    {
                        "kind": "wait_candidate",
                        "source_event_ids": (
                            previous.source_event_ids[-1],
                            current.source_event_ids[0],
                        ),
                        "details": {"gap_ns": gap_ns},
                    }
                )
            )
        return tuple(suggestions)


__all__ = ["NormalizationService", "RecordingNotNormalizable"]
