"""Typed, immutable inputs for a single guarded workflow run."""

from __future__ import annotations

from collections.abc import Mapping
from math import isfinite
from pathlib import Path
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from universal_rpa.domain.types import DataCell, FrozenMapping
from universal_rpa.domain.workflow import Workflow


def _is_reparse_point(path: Path) -> bool:
    """Return whether *path* itself is a Windows reparse point or symlink."""

    try:
        stat_result = path.lstat()
    except OSError:
        return False
    reparse_attribute = getattr(stat_result, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return path.is_symlink() or bool(
        getattr(stat_result, "st_file_attributes", 0) & reparse_attribute
    )


def _existing_directory(value: object, *, label: str) -> Path:
    path = Path(value) if isinstance(value, (str, Path)) else None
    if path is None or not path.is_dir() or _is_reparse_point(path):
        raise ValueError(f"{label} must be an existing non-reparse directory")
    resolved = path.resolve()
    if _is_reparse_point(resolved):
        raise ValueError(f"{label} must not resolve through a reparse directory")
    return resolved


def _freeze_run_values(value: object) -> FrozenMapping[str, DataCell]:
    if not isinstance(value, Mapping):
        raise ValueError("variable_values must be a mapping")
    items: list[tuple[str, DataCell]] = []
    for key, item in value.items():
        if not isinstance(key, str) or not key:
            raise ValueError("variable value keys must be nonblank strings")
        if item is not None and not isinstance(item, (bool, int, float, str)):
            raise ValueError("variable values must be scalar")
        if isinstance(item, float) and not isfinite(item):
            raise ValueError("variable values must be finite")
        items.append((key, item))
    return FrozenMapping(tuple(items))


class RunInputs(BaseModel):
    """User-provided, non-secret selections for one execution.

    Secret material is intentionally absent.  Workflow credential references are
    resolved later by :class:`ValueResolver` through ``SecretStorePort``.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, arbitrary_types_allowed=True)

    variable_values: FrozenMapping[str, DataCell] = Field(default_factory=FrozenMapping.empty)
    output_directory: Path

    @field_validator("variable_values", mode="before")
    @classmethod
    def freeze_variable_values(cls, value: object) -> FrozenMapping[str, DataCell]:
        return _freeze_run_values(value)

    @field_validator("output_directory", mode="after")
    @classmethod
    def output_directory_is_safe(cls, value: Path) -> Path:
        return _existing_directory(value, label="output_directory")


class ResumeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: UUID


class RunRequest(BaseModel):
    """A workflow and the immutable local context required to execute it."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    workflow: Workflow
    project_dir: Path
    inputs: RunInputs
    resume: ResumeRequest | None = None
    validation_only: bool = False

    @field_validator("project_dir", mode="after")
    @classmethod
    def project_directory_is_safe(cls, value: Path) -> Path:
        return _existing_directory(value, label="project_dir")


__all__ = ["ResumeRequest", "RunInputs", "RunRequest"]
