from __future__ import annotations

from datetime import date
from typing import Annotated

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    PlainSerializer,
    WithJsonSchema,
    model_validator,
)

from universal_rpa.domain.types import (
    FrozenJsonObject,
    FrozenJsonValue,
    FrozenMapping,
    JsonValue,
    deep_freeze_json,
    thaw_json,
)


class RelativePoint(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    x: float = Field(ge=0.0, le=1.0)
    y: float = Field(ge=0.0, le=1.0)


class NormalizedRect(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    x: float = Field(ge=0.0, le=1.0)
    y: float = Field(ge=0.0, le=1.0)
    width: float = Field(gt=0.0, le=1.0)
    height: float = Field(gt=0.0, le=1.0)


class UiaSelector(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    automation_id: str | None = None
    control_type: str | None = None
    name: str | None = None
    class_name: str | None = None
    ancestor_path: tuple[UiaSelector, ...] = ()

    @model_validator(mode="after")
    def has_identity_attribute(self) -> UiaSelector:
        if not any((self.automation_id, self.control_type, self.name, self.class_name)):
            raise ValueError("UIA selector requires an identity attribute")
        return self


class CoordinateFallback(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    recorded_process_executable: str = Field(min_length=1)
    recorded_window_class: str = Field(min_length=1)
    point: RelativePoint
    recorded_dpi_x: int = Field(gt=0)
    recorded_dpi_y: int = Field(gt=0)
    recorded_client_width: int = Field(gt=0)
    recorded_client_height: int = Field(gt=0)


class WindowsTarget(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    selector: UiaSelector | None
    coordinate_fallback: CoordinateFallback | None
    target_region: NormalizedRect | None = None
    mandatory_sensitive_regions: tuple[NormalizedRect, ...] = ()
    user_sensitive_regions: tuple[NormalizedRect, ...] = ()
    diagnostic_absolute_x: int | None = None
    diagnostic_absolute_y: int | None = None

    @model_validator(mode="after")
    def has_selector_or_fallback(self) -> WindowsTarget:
        if self.selector is None and self.coordinate_fallback is None:
            raise ValueError("Windows target requires a selector or coordinate fallback")
        return self

    @property
    def masking_regions(self) -> tuple[NormalizedRect, ...]:
        return self.mandatory_sensitive_regions + self.user_sensitive_regions


def _freeze_json_object(value: JsonValue | FrozenJsonValue) -> FrozenJsonObject:
    try:
        frozen = deep_freeze_json(value)
    except TypeError as error:
        raise ValueError("target payload must contain only JSON values") from error
    if not isinstance(frozen, FrozenMapping):
        raise ValueError("target payload must be a JSON object")
    return frozen


class TargetSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, arbitrary_types_allowed=True)

    adapter_id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    payload: Annotated[
        FrozenJsonObject,
        BeforeValidator(_freeze_json_object),
        PlainSerializer(thaw_json, return_type=dict[str, JsonValue]),
        WithJsonSchema({"type": "object", "additionalProperties": True}),
    ]


class RuntimeEnvironment(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    interactive_desktop: bool
    process_id: int = Field(gt=0)
    process_executable: str
    top_level_hwnd: int
    window_title: str
    window_class: str
    foreground_hwnd: int
    dpi_x: int = Field(gt=0)
    dpi_y: int = Field(gt=0)
    client_width: int = Field(gt=0)
    client_height: int = Field(gt=0)
    monitor_scale: float = Field(gt=0)


class DateContext(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    today: date
    run_date: date
