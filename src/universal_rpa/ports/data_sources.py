from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from math import isfinite
from pathlib import Path
from typing import Protocol

from universal_rpa.domain.types import DataCell, FrozenMapping
from universal_rpa.domain.workflow import DataSourceSpec


def _freeze_row(row: Mapping[str, DataCell]) -> FrozenMapping[str, DataCell]:
    items: list[tuple[str, DataCell]] = []
    for key, value in row.items():
        if not isinstance(key, str) or not key:
            raise ValueError("data row keys must be nonblank strings")
        if value is not None and not isinstance(value, (bool, int, float, str)):
            raise ValueError("data row values must be scalar")
        if isinstance(value, float) and not isfinite(value):
            raise ValueError("data row numbers must be finite")
        items.append((key, value))
    return FrozenMapping(tuple(items))


@dataclass(frozen=True, slots=True)
class DataPreview:
    headers: tuple[str, ...]
    rows: tuple[FrozenMapping[str, DataCell], ...]
    total_row_count: int | None

    def __post_init__(self) -> None:
        headers = tuple(self.headers)
        if any(not isinstance(header, str) or not header.strip() for header in headers):
            raise ValueError("preview headers must be nonblank strings")
        if len(set(headers)) != len(headers):
            raise ValueError("preview headers must be unique")
        rows = tuple(_freeze_row(row) for row in self.rows)
        if any(tuple(row) != headers for row in rows):
            raise ValueError("preview row keys must match headers")
        if self.total_row_count is not None:
            if self.total_row_count < 0 or self.total_row_count < len(rows):
                raise ValueError("total row count cannot be smaller than preview rows")
        object.__setattr__(self, "headers", headers)
        object.__setattr__(self, "rows", rows)


class DataSourcePort(Protocol):
    def preview(
        self,
        project_dir: Path,
        spec: DataSourceSpec,
        max_rows: int = 20,
    ) -> DataPreview: ...

    def iter_rows(
        self,
        project_dir: Path,
        spec: DataSourceSpec,
        required_columns: frozenset[str],
    ) -> Iterator[FrozenMapping[str, DataCell]]: ...


__all__ = ["DataPreview", "DataSourcePort"]
