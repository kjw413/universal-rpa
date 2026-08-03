"""Immutable data-source snapshots and bounded workflow loop planning."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from universal_rpa.domain.errors import ErrorCode, RpaError
from universal_rpa.domain.results import LoopCursor
from universal_rpa.domain.types import DataCell, FrozenMapping
from universal_rpa.domain.workflow import LoopStep, Workflow
from universal_rpa.ports.automation import ExecutionContext
from universal_rpa.ports.data_sources import DataSourcePort


@dataclass(frozen=True, slots=True)
class DataSourceSnapshot:
    data_source_id: str
    source_type: str
    headers: tuple[str, ...]
    rows: tuple[tuple[DataCell, ...], ...]
    content_sha256: str

    def row_mapping(self, index: int) -> FrozenMapping[str, DataCell]:
        try:
            row = self.rows[index]
        except IndexError:
            raise RpaError(
                ErrorCode.DATA_SOURCE_INVALID, "반복 데이터의 행 번호가 올바르지 않습니다."
            ) from None
        return FrozenMapping(tuple(zip(self.headers, row, strict=True)))


@dataclass(frozen=True, slots=True)
class IterationFrame:
    iteration_path: tuple[int, ...]
    cursor: tuple[LoopCursor, ...]
    row_stack: tuple[FrozenMapping[str, DataCell], ...]


def _canonical_hash(
    source_type: str, headers: tuple[str, ...], rows: tuple[tuple[DataCell, ...], ...]
) -> str:
    payload = json.dumps(
        {"source_type": source_type, "headers": headers, "rows": rows},
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


class LoopPlanner:
    """Loads each workflow data source once and plans at most depth-two loops."""

    def __init__(self, data_sources: DataSourcePort) -> None:
        self._data_sources = data_sources

    def materialize_snapshots(
        self,
        project_dir: Path,
        workflow: Workflow,
    ) -> FrozenMapping[str, DataSourceSnapshot]:
        snapshots: list[tuple[str, DataSourceSnapshot]] = []
        for source in workflow.data_sources:
            try:
                rows = tuple(self._data_sources.iter_rows(project_dir, source, frozenset()))
            except RpaError:
                raise
            except Exception:
                raise RpaError(
                    ErrorCode.DATA_SOURCE_INVALID, "반복 데이터 소스를 읽을 수 없습니다."
                ) from None
            headers = self._headers(rows)
            cells = tuple(tuple(row[header] for header in headers) for row in rows)
            snapshots.append(
                (
                    source.data_source_id,
                    DataSourceSnapshot(
                        data_source_id=source.data_source_id,
                        source_type=source.source_type,
                        headers=headers,
                        rows=cells,
                        content_sha256=_canonical_hash(source.source_type, headers, cells),
                    ),
                )
            )
        return FrozenMapping(tuple(snapshots))

    @staticmethod
    def _headers(rows: tuple[FrozenMapping[str, DataCell], ...]) -> tuple[str, ...]:
        if not rows:
            raise RpaError(ErrorCode.DATA_SOURCE_INVALID, "반복 데이터 소스가 비어 있습니다.")
        headers = tuple(rows[0])
        if not headers or any(tuple(row) != headers for row in rows):
            raise RpaError(
                ErrorCode.DATA_SOURCE_INVALID, "반복 데이터의 열 구성이 일치하지 않습니다."
            )
        return headers

    def validate_iteration_bound(
        self,
        workflow: Workflow,
        snapshots: FrozenMapping[str, DataSourceSnapshot],
    ) -> int:
        """Compute the complete enabled leaf-frame bound before any input."""

        total = 0
        for position, step in enumerate(workflow.steps):
            if not step.enabled or not isinstance(step, LoopStep):
                continue
            for _ in self._loop_frames(
                step,
                snapshots,
                iteration_path=(position,),
                cursor=(),
                row_stack=(),
            ):
                total += 1
                if total > workflow.run_policy.max_iterations:
                    raise RpaError(
                        ErrorCode.DATA_SOURCE_INVALID,
                        "반복 횟수가 실행 제한을 초과했습니다.",
                    )
        return total or 1

    def iter_workflow_frames(
        self,
        workflow: Workflow,
        context: ExecutionContext,
        *,
        snapshots: FrozenMapping[str, DataSourceSnapshot],
    ) -> Iterator[IterationFrame]:
        """Yield deterministic leaf frames in workflow order.

        A workflow without a loop has one empty frame.  Nested loops yield their
        depth-two leaf combinations; sequential loops keep distinct cursor roots.
        """

        yielded = 0
        for position, step in enumerate(workflow.steps):
            if not step.enabled or not isinstance(step, LoopStep):
                continue
            for frame in self._loop_frames(
                step,
                snapshots,
                iteration_path=(position,),
                cursor=(),
                row_stack=(),
            ):
                yielded += 1
                if yielded > workflow.run_policy.max_iterations:
                    raise RpaError(
                        ErrorCode.DATA_SOURCE_INVALID, "반복 횟수가 실행 제한을 초과했습니다."
                    )
                yield frame
        if yielded == 0:
            yield IterationFrame((), (), context.row_stack)

    def _loop_frames(
        self,
        step: LoopStep,
        snapshots: FrozenMapping[str, DataSourceSnapshot],
        *,
        iteration_path: tuple[int, ...],
        cursor: tuple[LoopCursor, ...],
        row_stack: tuple[FrozenMapping[str, DataCell], ...],
    ) -> Iterator[IterationFrame]:
        if len(cursor) >= 2:
            raise RpaError(
                ErrorCode.DATA_SOURCE_INVALID, "반복은 최대 두 단계까지 중첩할 수 있습니다."
            )
        try:
            snapshot = snapshots[step.data_source_id]
        except KeyError:
            raise RpaError(
                ErrorCode.DATA_SOURCE_INVALID, "반복 데이터 소스를 찾을 수 없습니다."
            ) from None
        child_loops = tuple(
            child for child in step.steps if child.enabled and isinstance(child, LoopStep)
        )
        for row_index in range(len(snapshot.rows)):
            next_cursor = (*cursor, LoopCursor(loop_step_id=step.step_id, row_index=row_index))
            next_rows = (*row_stack, snapshot.row_mapping(row_index))
            if not child_loops:
                yield IterationFrame((*iteration_path, row_index), next_cursor, next_rows)
                continue
            for child_position, child in enumerate(child_loops):
                yield from self._loop_frames(
                    child,
                    snapshots,
                    iteration_path=(*iteration_path, row_index, child_position),
                    cursor=next_cursor,
                    row_stack=next_rows,
                )


__all__ = ["DataSourceSnapshot", "IterationFrame", "LoopPlanner"]
