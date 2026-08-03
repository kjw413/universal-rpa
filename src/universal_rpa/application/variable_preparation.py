"""One fail-closed preparation pass for non-secret execution variables."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

from universal_rpa.application.date_rules import evaluate_date_expression
from universal_rpa.application.loops import DataSourceSnapshot
from universal_rpa.domain.errors import ErrorCode, RpaError
from universal_rpa.domain.execution import RunInputs
from universal_rpa.domain.targets import DateContext
from universal_rpa.domain.types import FrozenMapping
from universal_rpa.domain.values import (
    CredentialSource,
    DataColumnSource,
    DateRuleSource,
    FixedDefaultSource,
    InlineChoiceSource,
    RunInputSource,
)
from universal_rpa.domain.workflow import Workflow
from universal_rpa.ports.automation import PreparedValue
from universal_rpa.ports.credentials import SecretStorePort


@dataclass(frozen=True, slots=True)
class PreparedVariables:
    values: FrozenMapping[str, PreparedValue]
    credential_refs: FrozenMapping[str, str]


class VariablePreparationService:
    def prepare(
        self,
        workflow: Workflow,
        run_inputs: RunInputs,
        project_dir: Path,
        date_context: DateContext,
        snapshots: FrozenMapping[str, DataSourceSnapshot],
        secret_store: SecretStorePort,
    ) -> PreparedVariables:
        del project_dir
        expected_ids = {variable.variable_id for variable in workflow.variables}
        unexpected = set(run_inputs.variable_values) - expected_ids
        if unexpected:
            raise RpaError(ErrorCode.INVALID_SCHEMA, "정의되지 않은 실행 변수가 포함되어 있습니다.")

        values: list[tuple[str, PreparedValue]] = []
        credentials: list[tuple[str, str]] = []
        for variable in workflow.variables:
            source = variable.source
            if isinstance(source, CredentialSource):
                if not secret_store.exists(source.credential_ref):
                    raise RpaError(ErrorCode.SECRET_MISSING, "선택한 자격 증명을 찾을 수 없습니다.")
                credentials.append((variable.variable_id, source.credential_ref))
                continue
            raw: object
            if isinstance(source, RunInputSource):
                if variable.variable_id not in run_inputs.variable_values:
                    if source.required:
                        raise RpaError(ErrorCode.INVALID_SCHEMA, "필수 실행 변수를 입력하세요.")
                    continue
                raw = run_inputs.variable_values[variable.variable_id]
            elif isinstance(source, FixedDefaultSource):
                raw = source.value
            elif isinstance(source, InlineChoiceSource):
                if variable.variable_id not in run_inputs.variable_values:
                    raise RpaError(ErrorCode.INVALID_SCHEMA, "선택 실행 변수를 입력하세요.")
                raw = run_inputs.variable_values[variable.variable_id]
                if raw not in source.options:
                    raise RpaError(ErrorCode.INVALID_SCHEMA, "허용되지 않은 선택 값입니다.")
            elif isinstance(source, DataColumnSource):
                if variable.variable_id not in run_inputs.variable_values:
                    if source.required:
                        raise RpaError(ErrorCode.INVALID_SCHEMA, "데이터 열 선택 값을 입력하세요.")
                    continue
                raw = run_inputs.variable_values[variable.variable_id]
                try:
                    snapshot = snapshots[source.data_source_id]
                    index = snapshot.headers.index(source.column_name)
                except (KeyError, ValueError):
                    raise RpaError(
                        ErrorCode.DATA_SOURCE_INVALID, "데이터 소스에 지정한 열이 없습니다."
                    ) from None
                if raw not in {row[index] for row in snapshot.rows}:
                    raise RpaError(
                        ErrorCode.DATA_SOURCE_INVALID, "선택 값이 데이터 소스에 없습니다."
                    )
            elif isinstance(source, DateRuleSource):
                values.append(
                    (
                        variable.variable_id,
                        evaluate_date_expression(source.expression, date_context),
                    )
                )
                continue
            else:
                raise RpaError(ErrorCode.INVALID_SCHEMA, "지원하지 않는 변수 원본입니다.")
            values.append((variable.variable_id, self._parse(variable.value_type, raw)))
        return PreparedVariables(FrozenMapping(tuple(values)), FrozenMapping(tuple(credentials)))

    @staticmethod
    def _parse(value_type: str, raw: object) -> PreparedValue:
        if value_type in {"text", "choice"}:
            if not isinstance(raw, str) or not (value := raw.strip()):
                raise RpaError(ErrorCode.INVALID_SCHEMA, "텍스트 값은 비워 둘 수 없습니다.")
            return value
        if value_type == "date":
            if not isinstance(raw, str):
                raise RpaError(ErrorCode.INVALID_SCHEMA, "날짜 값의 형식이 올바르지 않습니다.")
            try:
                return date.fromisoformat(raw)
            except ValueError:
                raise RpaError(
                    ErrorCode.INVALID_SCHEMA, "날짜는 YYYY-MM-DD 형식이어야 합니다."
                ) from None
        if value_type == "integer":
            if isinstance(raw, bool) or not isinstance(raw, (str, int)):
                raise RpaError(ErrorCode.INVALID_SCHEMA, "정수 값의 형식이 올바르지 않습니다.")
            try:
                return int(raw)
            except ValueError:
                raise RpaError(
                    ErrorCode.INVALID_SCHEMA, "정수 값의 형식이 올바르지 않습니다."
                ) from None
        if value_type == "decimal":
            if isinstance(raw, bool) or not isinstance(raw, (str, int, float)):
                raise RpaError(ErrorCode.INVALID_SCHEMA, "소수 값의 형식이 올바르지 않습니다.")
            try:
                decimal_value = Decimal(str(raw))
            except InvalidOperation:
                raise RpaError(
                    ErrorCode.INVALID_SCHEMA, "소수 값의 형식이 올바르지 않습니다."
                ) from None
            if not decimal_value.is_finite():
                raise RpaError(ErrorCode.INVALID_SCHEMA, "소수 값은 유한해야 합니다.")
            return decimal_value
        if value_type == "path":
            if not isinstance(raw, str) or not raw.strip():
                raise RpaError(ErrorCode.INVALID_SCHEMA, "경로 값을 입력하세요.")
            path = Path(raw)
            if path.is_absolute() or any(part == ".." for part in path.parts):
                raise RpaError(ErrorCode.INVALID_SCHEMA, "상대 경로만 사용할 수 있습니다.")
            return path
        raise RpaError(ErrorCode.INVALID_SCHEMA, "지원하지 않는 변수 유형입니다.")


__all__ = ["PreparedVariables", "VariablePreparationService"]
