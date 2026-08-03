"""Windows Credential Manager adapter; workflows retain references only."""

from __future__ import annotations

from typing import Any

from universal_rpa.domain.errors import ErrorCode, RpaError
from universal_rpa.ports.credentials import SecretStorePort, SecretValue


class WindowsCredentialStore(SecretStorePort):
    def __init__(self, *, prefix: str = "universal-rpa/") -> None:
        self._prefix = prefix

    def _target(self, reference: str) -> str:
        if not reference or reference.startswith(self._prefix):
            return reference
        return self._prefix + reference

    @staticmethod
    def _module() -> Any:
        try:
            import win32cred  # type: ignore[import-untyped]

            return win32cred
        except Exception:
            raise RpaError(
                ErrorCode.SECRET_MISSING, "Windows 자격 증명 저장소를 사용할 수 없습니다."
            ) from None

    def exists(self, reference: str) -> bool:
        try:
            module = self._module()
            module.CredRead(self._target(reference), module.CRED_TYPE_GENERIC)
            return True
        except RpaError:
            return False
        except Exception:
            return False

    def read(self, reference: str) -> SecretValue:
        try:
            module = self._module()
            credential = module.CredRead(self._target(reference), module.CRED_TYPE_GENERIC)
            blob = credential["CredentialBlob"]
            value = blob.decode("utf-16-le") if isinstance(blob, bytes) else str(blob)
            return SecretValue.from_text(value)
        except RpaError:
            raise
        except Exception:
            raise RpaError(
                ErrorCode.SECRET_MISSING, "선택한 자격 증명을 읽을 수 없습니다."
            ) from None

    def write(self, reference: str, value: SecretValue) -> None:
        module = self._module()
        with value.reveal() as revealed:
            module.CredWrite(
                {
                    "Type": module.CRED_TYPE_GENERIC,
                    "TargetName": self._target(reference),
                    "CredentialBlob": revealed.encode("utf-16-le"),
                    "Persist": module.CRED_PERSIST_LOCAL_MACHINE,
                    "UserName": "universal-rpa",
                },
                0,
            )

    def delete(self, reference: str) -> None:
        try:
            module = self._module()
            module.CredDelete(self._target(reference), module.CRED_TYPE_GENERIC, 0)
        except RpaError:
            raise
        except Exception:
            return


__all__ = ["WindowsCredentialStore"]
