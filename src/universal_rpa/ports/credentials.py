from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Protocol


class SecretValue:
    """An explicitly revealable secret whose normal representations are safe."""

    __slots__ = ("__value",)

    def __init__(self, value: str) -> None:
        self.__value = value

    @classmethod
    def from_text(cls, value: str) -> SecretValue:
        if not isinstance(value, str):
            raise TypeError("secret value must be text")
        return cls(value)

    @contextmanager
    def reveal(self) -> Iterator[str]:
        yield self.__value

    def __repr__(self) -> str:
        return "SecretValue(<redacted>)"

    def __str__(self) -> str:
        raise TypeError("SecretValue cannot be converted to str")


class SecretStorePort(Protocol):
    def exists(self, reference: str) -> bool: ...

    def read(self, reference: str) -> SecretValue: ...


__all__ = ["SecretStorePort", "SecretValue"]
