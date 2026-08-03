"""Strict value resolution without expressions or secret interpolation."""

from __future__ import annotations

from universal_rpa.domain.errors import ErrorCode, RpaError
from universal_rpa.domain.results import OutputCommit, TableData
from universal_rpa.domain.types import FrozenJsonValue
from universal_rpa.domain.values import (
    LiteralValue,
    RowBindingValue,
    SecretRefValue,
    ValueSpec,
    VariableValue,
)
from universal_rpa.ports.automation import ExecutionContext, PreparedValue
from universal_rpa.ports.credentials import SecretStorePort, SecretValue


class ValueResolver:
    def __init__(self, secret_store: SecretStorePort) -> None:
        self._secret_store = secret_store

    def resolve(
        self,
        value: ValueSpec,
        context: ExecutionContext,
    ) -> FrozenJsonValue | PreparedValue | TableData | OutputCommit | SecretValue:
        if isinstance(value, LiteralValue):
            return value.value
        if isinstance(value, VariableValue):
            try:
                return context.variables[value.variable_id]
            except KeyError:
                raise RpaError(ErrorCode.INVALID_SCHEMA, "실행 변수를 찾을 수 없습니다.") from None
        if isinstance(value, SecretRefValue):
            return self._read_secret(value.credential_ref)
        if isinstance(value, RowBindingValue):
            for row in reversed(context.row_stack):
                if value.column_name in row:
                    return row[value.column_name]
            raise RpaError(ErrorCode.DATA_SOURCE_INVALID, "반복 데이터에 지정한 열이 없습니다.")
        raise RpaError(ErrorCode.INVALID_SCHEMA, "지원하지 않는 입력 값 형식입니다.")

    def _read_secret(self, reference: str) -> SecretValue:
        try:
            return self._secret_store.read(reference)
        except RpaError:
            raise
        except Exception:
            raise RpaError(
                ErrorCode.SECRET_MISSING, "선택한 자격 증명을 읽을 수 없습니다."
            ) from None


__all__ = ["ValueResolver"]
