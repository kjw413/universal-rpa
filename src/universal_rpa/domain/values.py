from __future__ import annotations

from datetime import date
from math import isfinite
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator, model_validator

from universal_rpa.domain.targets import DateContext


class LiteralValue(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    mode: Literal["literal"] = "literal"
    value: str | int | float | bool | None

    @field_validator("value", mode="after")
    @classmethod
    def value_is_finite(
        cls, value: str | int | float | bool | None
    ) -> str | int | float | bool | None:
        if isinstance(value, float) and not isfinite(value):
            raise ValueError("literal numbers must be finite")
        return value


class VariableValue(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    mode: Literal["variable"] = "variable"
    variable_id: str


class RowBindingValue(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    mode: Literal["row_binding"] = "row_binding"
    template: str = Field(pattern=r"^\{\{ row\.[A-Za-z_][A-Za-z0-9_]* \}\}$")

    @property
    def column_name(self) -> str:
        return self.template[7:-3]


class SecretRefValue(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    mode: Literal["secret_ref"] = "secret_ref"
    credential_ref: str = Field(min_length=1)


ValueSpec = Annotated[
    LiteralValue | VariableValue | RowBindingValue | SecretRefValue,
    Field(discriminator="mode"),
]


class RunInputSource(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_type: Literal["run_input"] = "run_input"
    required: bool = True


class FixedDefaultSource(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_type: Literal["fixed_default"] = "fixed_default"
    value: str | int | float | bool | None


class InlineChoiceSource(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_type: Literal["inline_options"] = "inline_options"
    options: tuple[str, ...] = Field(min_length=1)

    @field_validator("options", mode="after")
    @classmethod
    def options_are_trimmed_nonblank_and_unique(cls, options: tuple[str, ...]) -> tuple[str, ...]:
        trimmed = tuple(option.strip() for option in options)
        if any(not option for option in trimmed):
            raise ValueError("inline options must not be blank")
        if len(set(trimmed)) != len(trimmed):
            raise ValueError("inline options must be unique")
        return trimmed


class DataColumnSource(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_type: Literal["csv_column", "xlsx_column"]
    data_source_id: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    column_name: str = Field(min_length=1)
    required: bool = True


class DateExpression(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    operation: Literal["today", "run_date", "add_days", "month_start", "month_end"]
    operand: DateExpression | None = None
    days: StrictInt | None = None

    @model_validator(mode="after")
    def has_valid_operation_shape(self) -> DateExpression:
        if self.operation in {"today", "run_date"}:
            if self.operand is not None or self.days is not None:
                raise ValueError(f"{self.operation} accepts no operand or days")
        elif self.operation in {"month_start", "month_end"}:
            if self.operand is None or self.days is not None:
                raise ValueError(f"{self.operation} requires one operand and no days")
        elif self.operand is None or self.days is None:
            raise ValueError("add_days requires one operand and integer days")
        return self


class DateRuleSource(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_type: Literal["date_rule"] = "date_rule"
    expression: DateExpression


class CredentialSource(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_type: Literal["credential_ref"] = "credential_ref"
    credential_ref: str = Field(min_length=1)


VariableSource = Annotated[
    RunInputSource
    | FixedDefaultSource
    | InlineChoiceSource
    | DataColumnSource
    | DateRuleSource
    | CredentialSource,
    Field(discriminator="source_type"),
]


class VariableDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    variable_id: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    label: str = Field(min_length=1)
    value_type: Literal["text", "date", "integer", "decimal", "path", "choice", "secret"]
    source: VariableSource

    @model_validator(mode="after")
    def source_matches_value_type(self) -> VariableDefinition:
        source = self.source
        valid_types: dict[type[BaseModel], set[str]] = {
            RunInputSource: {"text", "date", "integer", "decimal", "path"},
            FixedDefaultSource: {"text", "date", "integer", "decimal", "path"},
            InlineChoiceSource: {"choice"},
            DataColumnSource: {"choice"},
            DateRuleSource: {"date"},
            CredentialSource: {"secret"},
        }
        if self.value_type not in valid_types[type(source)]:
            raise ValueError("variable source does not support this value type")
        if isinstance(source, FixedDefaultSource):
            self._validate_fixed_default(source.value)
        return self

    def _validate_fixed_default(self, value: str | int | float | bool | None) -> None:
        if self.value_type in {"text", "path"} and not isinstance(value, str):
            raise ValueError("text and path defaults must be strings")
        if self.value_type == "date":
            if not isinstance(value, str) or len(value) != 10 or value[4] != "-" or value[7] != "-":
                raise ValueError("date defaults must be ISO dates")
            try:
                date.fromisoformat(value)
            except ValueError as error:
                raise ValueError("date defaults must use YYYY-MM-DD") from error
        if self.value_type == "integer" and (isinstance(value, bool) or not isinstance(value, int)):
            raise ValueError("integer defaults must be integers")
        if self.value_type == "decimal" and (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not isfinite(float(value))
        ):
            raise ValueError("decimal defaults must be finite numbers")


__all__ = [
    "CredentialSource",
    "DataColumnSource",
    "DateContext",
    "DateExpression",
    "DateRuleSource",
    "FixedDefaultSource",
    "InlineChoiceSource",
    "LiteralValue",
    "RowBindingValue",
    "RunInputSource",
    "SecretRefValue",
    "ValueSpec",
    "VariableDefinition",
    "VariableSource",
    "VariableValue",
]
