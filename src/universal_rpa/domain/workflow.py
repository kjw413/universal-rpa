from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from math import isfinite
from pathlib import Path, PurePosixPath
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    PlainSerializer,
    RootModel,
    WithJsonSchema,
    field_validator,
    model_validator,
)

from universal_rpa.domain.action_parameters import (
    BUILTIN_ACTION_PARAMETER_MODELS,
    validate_builtin_action_parameters,
)
from universal_rpa.domain.conditions import AssertionSpec, TableAssertionSpec, WaitSpec
from universal_rpa.domain.targets import TargetSpec
from universal_rpa.domain.types import (
    DataCell,
    FrozenJsonObject,
    FrozenMapping,
    JsonValue,
    deep_freeze_json,
    thaw_json,
)
from universal_rpa.domain.values import (
    DataColumnSource,
    RowBindingValue,
    ValueSpec,
    VariableDefinition,
    VariableValue,
)


def _freeze_json_object(value: object) -> FrozenJsonObject:
    try:
        frozen = deep_freeze_json(value)  # type: ignore[arg-type]
    except TypeError as error:
        raise ValueError("parameters must contain only JSON values") from error
    if not isinstance(frozen, FrozenMapping):
        raise ValueError("parameters must be a JSON object")
    return frozen


ImmutableJsonObject = Annotated[
    FrozenJsonObject,
    BeforeValidator(_freeze_json_object),
    PlainSerializer(thaw_json, return_type=dict[str, JsonValue]),
    WithJsonSchema({"type": "object", "additionalProperties": True}),
]


class FailurePolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    mode: Literal["stop", "retry", "skip_iteration"] = "stop"
    retry_count: int = Field(default=0, ge=0, le=3)
    backoff_ms: int = Field(default=500, ge=0, le=60_000)

    @model_validator(mode="after")
    def retries_are_declared_only_in_retry_mode(self) -> FailurePolicy:
        if self.mode != "retry" and self.retry_count != 0:
            raise ValueError("retry_count is valid only for retry mode")
        return self


class ActionStep(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        arbitrary_types_allowed=True,
    )

    step_id: UUID
    label: str = Field(min_length=1)
    kind: Literal["action"] = "action"
    enabled: bool = True
    failure_policy: FailurePolicy = FailurePolicy()
    action_type: str = Field(pattern=r"^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$")
    input_step_id: UUID | None = None
    target: TargetSpec | None = None
    value: ValueSpec | None = None
    parameters: ImmutableJsonObject = Field(default_factory=FrozenMapping.empty)
    precondition: WaitSpec | None = None
    postcondition: WaitSpec | None = None
    wait: WaitSpec | None = None
    assertions: tuple[AssertionSpec | TableAssertionSpec, ...] = ()

    @model_validator(mode="after")
    def action_contract_is_consistent(self) -> ActionStep:
        if self.action_type in BUILTIN_ACTION_PARAMETER_MODELS:
            canonical = validate_builtin_action_parameters(self.action_type, self.parameters)
            object.__setattr__(self, "parameters", canonical)

        if self.action_type == "windows.wait":
            if self.wait is None:
                raise ValueError("windows.wait requires a wait payload")
        elif self.wait is not None:
            raise ValueError("wait payload is valid only for windows.wait")

        if self.action_type == "tabular.save_table":
            if self.input_step_id is None:
                raise ValueError("tabular.save_table requires input_step_id")
        elif self.input_step_id is not None:
            raise ValueError("input_step_id is valid only for tabular.save_table")

        if self.action_type == "clipboard.extract_table" and not (
            self.assertions or self.postcondition is not None
        ):
            raise ValueError("clipboard extraction requires an assertion or postcondition")

        if self.target is not None:
            coordinate_fallback = self.target.payload.get("coordinate_fallback")
            if coordinate_fallback is not None and not (
                self.assertions or self.postcondition is not None
            ):
                raise ValueError("coordinate fallback requires an assertion or postcondition")
        return self


class LoopStep(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    step_id: UUID
    label: str = Field(min_length=1)
    kind: Literal["loop"] = "loop"
    enabled: bool = True
    failure_policy: FailurePolicy = FailurePolicy()
    data_source_id: str
    steps: tuple[Step, ...]

    @model_validator(mode="after")
    def composite_cannot_retry(self) -> LoopStep:
        if self.failure_policy.mode == "retry":
            raise ValueError("loop steps cannot retry")
        return self


class PresenceSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    condition_type: str = Field(pattern=r"^[a-z][a-z0-9_]*\.(element_exists|window_exists)$")
    target: TargetSpec
    timeout_ms: int = Field(gt=0, le=86_400_000)
    poll_interval_ms: int = Field(default=100, gt=0)

    @model_validator(mode="after")
    def presence_matches_target_and_timeout(self) -> PresenceSpec:
        namespace, _, _ = self.condition_type.partition(".")
        if namespace != self.target.adapter_id:
            raise ValueError("presence condition namespace must match target adapter")
        if self.poll_interval_ms > self.timeout_ms:
            raise ValueError("poll interval must not exceed timeout")
        return self


class IfPresentStep(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    step_id: UUID
    label: str = Field(min_length=1)
    kind: Literal["if_present"] = "if_present"
    enabled: bool = True
    failure_policy: FailurePolicy = FailurePolicy()
    condition: PresenceSpec
    steps: tuple[Step, ...]

    @model_validator(mode="after")
    def composite_cannot_retry(self) -> IfPresentStep:
        if self.failure_policy.mode == "retry":
            raise ValueError("if-present steps cannot retry")
        return self


Step = Annotated[ActionStep | LoopStep | IfPresentStep, Field(discriminator="kind")]


def _validate_relative_path(value: object, *, require_inputs: bool) -> str:
    if not isinstance(value, str):
        raise ValueError("relative path must be a string")
    if not value or "\\" in value or "\x00" in value or ":" in value or value.startswith("/"):
        raise ValueError("path must be a normalized relative POSIX path")
    segments = value.split("/")
    if any(segment in {"", ".", ".."} for segment in segments):
        raise ValueError("path contains an unsafe segment")
    reserved_devices = {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"}
    for segment in segments:
        device_stem = segment.partition(".")[0].upper()
        if (
            segment.endswith((" ", "."))
            or device_stem in reserved_devices
            or (
                len(device_stem) == 4
                and device_stem[:3] in {"COM", "LPT"}
                and device_stem[3] in "123456789"
            )
        ):
            raise ValueError("path contains Windows device syntax")
    if PurePosixPath(value).as_posix() != value:
        raise ValueError("path must be normalized")
    if require_inputs and (len(segments) < 2 or segments[0] != "inputs"):
        raise ValueError("project-relative paths must be beneath inputs/")
    return value


def _is_reparse_point(path: Path) -> bool:
    try:
        stat_result = path.lstat()
    except FileNotFoundError:
        return False
    file_attributes = getattr(stat_result, "st_file_attributes", 0)
    reparse_attribute = getattr(stat_result, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return path.is_symlink() or bool(file_attributes & reparse_attribute)


def _resolve_under(value: str, root: Path) -> Path:
    supplied_root = Path(root)
    current = supplied_root
    if _is_reparse_point(current):
        raise ValueError("root cannot be a symlink or reparse point")
    for segment in value.split("/"):
        current = current / segment
        if _is_reparse_point(current):
            raise ValueError("path cannot traverse a symlink or reparse point")

    resolved_root = supplied_root.resolve()
    resolved_path = (supplied_root / PurePosixPath(value)).resolve()
    try:
        resolved_path.relative_to(resolved_root)
    except ValueError as error:
        raise ValueError("resolved path escapes supplied root") from error
    if resolved_path == resolved_root:
        raise ValueError("path must identify an entry beneath supplied root")
    return resolved_path


class ProjectRelativePath(RootModel[str]):
    model_config = ConfigDict(frozen=True)

    @field_validator("root", mode="before")
    @classmethod
    def is_safe_project_path(cls, value: object) -> str:
        return _validate_relative_path(value, require_inputs=True)

    def resolve_under(self, root: Path) -> Path:
        return _resolve_under(self.root, root)


class OutputRelativePath(RootModel[str]):
    model_config = ConfigDict(frozen=True)

    @field_validator("root", mode="before")
    @classmethod
    def is_safe_output_path(cls, value: object) -> str:
        return _validate_relative_path(value, require_inputs=False)

    def resolve_under(self, root: Path) -> Path:
        return _resolve_under(self.root, root)


def _validate_data_rows(value: object) -> object:
    if not isinstance(value, (list, tuple)):
        return value
    for row in value:
        if not isinstance(row, (list, tuple)):
            raise ValueError("inline rows must be sequences")
        if any(cell is not None and not isinstance(cell, (bool, int, float, str)) for cell in row):
            raise ValueError("inline data cells must be scalar values")
        if any(isinstance(cell, float) and not isfinite(cell) for cell in row):
            raise ValueError("inline data numbers must be finite")
    return value


class InlineDataSource(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_type: Literal["inline"] = "inline"
    data_source_id: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    label: str = Field(min_length=1)
    headers: tuple[str, ...] = Field(min_length=1)
    rows: tuple[tuple[DataCell, ...], ...] = Field(min_length=1)

    @field_validator("headers", mode="after")
    @classmethod
    def headers_are_trimmed_nonblank_unique(cls, headers: tuple[str, ...]) -> tuple[str, ...]:
        trimmed = tuple(header.strip() for header in headers)
        if any(not header for header in trimmed):
            raise ValueError("inline headers must not be blank")
        if len(set(trimmed)) != len(trimmed):
            raise ValueError("inline headers must be unique")
        return trimmed

    @field_validator("rows", mode="before")
    @classmethod
    def cells_are_scalar(cls, value: object) -> object:
        return _validate_data_rows(value)

    @field_validator("rows", mode="after")
    @classmethod
    def parsed_rows_have_only_finite_numbers(
        cls, rows: tuple[tuple[DataCell, ...], ...]
    ) -> tuple[tuple[DataCell, ...], ...]:
        if any(isinstance(cell, float) and not isfinite(cell) for row in rows for cell in row):
            raise ValueError("inline data numbers must be finite")
        return rows

    @model_validator(mode="after")
    def rows_match_header_width(self) -> InlineDataSource:
        if any(len(row) != len(self.headers) for row in self.rows):
            raise ValueError("inline row width must match header width")
        return self


class CsvDataSource(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_type: Literal["csv"] = "csv"
    data_source_id: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    label: str = Field(min_length=1)
    path: ProjectRelativePath
    encoding: Literal["utf-8", "utf-8-sig", "cp949"]


class XlsxDataSource(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_type: Literal["xlsx"] = "xlsx"
    data_source_id: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    label: str = Field(min_length=1)
    path: ProjectRelativePath
    sheet_name: str = Field(min_length=1)


DataSourceSpec = Annotated[
    InlineDataSource | CsvDataSource | XlsxDataSource,
    Field(discriminator="source_type"),
]


class TargetAppSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    app_id: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    process_executable: str = Field(min_length=1)
    window_class: str = Field(min_length=1)
    window_title: str | None = None


class EnvironmentPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    require_interactive_desktop: Literal[True] = True
    require_foreground_before_input: Literal[True] = True
    coordinate_client_size_tolerance_percent: Literal[2] = 2


class RunPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    max_iterations: int = Field(default=1_000, ge=1, le=10_000)
    max_runtime_seconds: int = Field(default=7_200, ge=1, le=86_400)


class OutputPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    artifact_retention_days: int = Field(default=30, ge=1, le=365)
    failure_screenshots: bool = True


def _utc_datetime(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError("datetime must use UTC")
    return value.astimezone(UTC)


def _all_steps(steps: Sequence[Step]) -> list[Step]:
    found: list[Step] = []
    for step in steps:
        found.append(step)
        if isinstance(step, (LoopStep, IfPresentStep)):
            found.extend(_all_steps(step.steps))
    return found


def _contains_if_present(steps: Sequence[Step]) -> bool:
    for step in steps:
        if isinstance(step, IfPresentStep):
            return True
        if isinstance(step, LoopStep) and _contains_if_present(step.steps):
            return True
    return False


def _validate_step_sequence(
    steps: Sequence[Step],
    *,
    data_sources: Mapping[str, DataSourceSpec],
    variable_ids: set[str],
    available_extractions: set[UUID],
    loop_depth: int,
) -> None:
    available = set(available_extractions)
    for step in steps:
        if step.failure_policy.mode == "skip_iteration" and loop_depth == 0:
            raise ValueError("skip_iteration is valid only inside a loop")

        if isinstance(step, ActionStep):
            if isinstance(step.value, VariableValue):
                if step.value.variable_id not in variable_ids:
                    raise ValueError("action references an unknown variable")
            if isinstance(step.value, RowBindingValue):
                if loop_depth == 0:
                    raise ValueError("row binding is valid only inside a loop")

            if step.action_type == "tabular.save_table":
                if step.input_step_id not in available:
                    raise ValueError(
                        "save_table requires an earlier dominating enabled extraction "
                        "in the same iteration frame"
                    )
            if step.enabled and step.action_type == "clipboard.extract_table":
                available.add(step.step_id)
            continue

        if isinstance(step, LoopStep):
            if loop_depth >= 2:
                raise ValueError("maximum loop depth is 2")
            if step.data_source_id not in data_sources:
                raise ValueError("loop references an unknown data source")
            _validate_step_sequence(
                step.steps,
                data_sources=data_sources,
                variable_ids=variable_ids,
                available_extractions=set(),
                loop_depth=loop_depth + 1,
            )
            continue

        if _contains_if_present(step.steps):
            raise ValueError("if_present steps cannot nest")
        _validate_step_sequence(
            step.steps,
            data_sources=data_sources,
            variable_ids=variable_ids,
            available_extractions=available,
            loop_depth=loop_depth,
        )


class Workflow(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1"] = "1"
    workflow_id: UUID
    name: str = Field(min_length=1)
    revision: int = Field(ge=0)
    target_apps: tuple[TargetAppSpec, ...] = Field(min_length=1)
    environment_policy: EnvironmentPolicy = EnvironmentPolicy()
    variables: tuple[VariableDefinition, ...] = ()
    data_sources: tuple[DataSourceSpec, ...] = ()
    steps: tuple[Step, ...] = Field(min_length=1)
    run_policy: RunPolicy = RunPolicy()
    output_policy: OutputPolicy = OutputPolicy()
    created_at: datetime
    updated_at: datetime

    @field_validator("created_at", "updated_at", mode="after")
    @classmethod
    def timestamps_are_utc(cls, value: datetime) -> datetime:
        return _utc_datetime(value)

    @model_validator(mode="after")
    def references_and_structure_are_valid(self) -> Workflow:
        if self.updated_at < self.created_at:
            raise ValueError("updated_at cannot precede created_at")

        all_steps = _all_steps(self.steps)
        step_ids = [step.step_id for step in all_steps]
        variable_ids = [variable.variable_id for variable in self.variables]
        data_source_ids = [source.data_source_id for source in self.data_sources]
        app_ids = [app.app_id for app in self.target_apps]
        for label, identifiers in (
            ("step", step_ids),
            ("variable", variable_ids),
            ("data source", data_source_ids),
            ("target app", app_ids),
        ):
            if len(set(identifiers)) != len(identifiers):
                raise ValueError(f"{label} identifiers must be unique")

        data_sources = {source.data_source_id: source for source in self.data_sources}
        for variable in self.variables:
            source = variable.source
            if not isinstance(source, DataColumnSource):
                continue
            data_source = data_sources.get(source.data_source_id)
            expected_type = CsvDataSource if source.source_type == "csv_column" else XlsxDataSource
            if not isinstance(data_source, expected_type):
                raise ValueError("variable column source must match an existing data-source kind")

        _validate_step_sequence(
            self.steps,
            data_sources=data_sources,
            variable_ids=set(variable_ids),
            available_extractions=set(),
            loop_depth=0,
        )
        return self


LoopStep.model_rebuild()
IfPresentStep.model_rebuild()
Workflow.model_rebuild()


__all__ = [
    "ActionStep",
    "CsvDataSource",
    "DataSourceSpec",
    "EnvironmentPolicy",
    "FailurePolicy",
    "IfPresentStep",
    "InlineDataSource",
    "LoopStep",
    "OutputPolicy",
    "OutputRelativePath",
    "PresenceSpec",
    "ProjectRelativePath",
    "RunPolicy",
    "Step",
    "TargetAppSpec",
    "Workflow",
    "XlsxDataSource",
]
