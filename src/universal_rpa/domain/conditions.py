from __future__ import annotations

from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    PlainSerializer,
    WithJsonSchema,
    field_serializer,
    model_validator,
)

from universal_rpa.domain.targets import TargetSpec
from universal_rpa.domain.types import (
    FrozenJsonValue,
    JsonValue,
    deep_freeze_json,
    thaw_json,
)


def _freeze_json_value(value: JsonValue | FrozenJsonValue) -> FrozenJsonValue:
    try:
        return deep_freeze_json(value)
    except TypeError as error:
        raise ValueError("expected must contain only JSON values") from error


ImmutableJsonValue = Annotated[
    FrozenJsonValue,
    BeforeValidator(_freeze_json_value),
    PlainSerializer(thaw_json, return_type=JsonValue),
    WithJsonSchema({}),
]


class ConditionSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, arbitrary_types_allowed=True)

    condition_type: str = Field(pattern=r"^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$")
    target: TargetSpec | None = None
    expected: ImmutableJsonValue = None


class WaitSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    condition: ConditionSpec
    timeout_ms: int = Field(gt=0, le=86_400_000)
    poll_interval_ms: int = Field(default=100, gt=0)

    @model_validator(mode="after")
    def poll_does_not_exceed_timeout(self) -> WaitSpec:
        if self.poll_interval_ms > self.timeout_ms:
            raise ValueError("poll interval must not exceed timeout")
        return self


class AssertionSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, arbitrary_types_allowed=True)

    assertion_type: str = Field(pattern=r"^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$")
    expected: ImmutableJsonValue = None


class TableAssertionSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    assertion_type: Literal["clipboard.table"] = "clipboard.table"
    required_headers: frozenset[str] = frozenset()
    min_rows: int | None = Field(default=None, ge=0)
    max_rows: int | None = Field(default=None, ge=0)
    required_tokens: frozenset[str] = frozenset()
    allow_empty: bool = False

    @field_serializer("required_headers", "required_tokens")
    def serialize_string_sets(self, value: frozenset[str]) -> tuple[str, ...]:
        return tuple(sorted(value))

    @model_validator(mode="after")
    def row_range_is_ordered(self) -> TableAssertionSpec:
        if (
            self.min_rows is not None
            and self.max_rows is not None
            and self.min_rows > self.max_rows
        ):
            raise ValueError("minimum rows must not exceed maximum rows")
        return self


__all__ = [
    "AssertionSpec",
    "ConditionSpec",
    "TableAssertionSpec",
    "WaitSpec",
]
