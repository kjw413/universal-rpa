from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from math import isfinite
from typing import Any, TypeVar, overload

type JsonScalar = bool | int | float | str | None
type JsonValue = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]
type DataCell = JsonScalar

K = TypeVar("K")
T = TypeVar("T")
V = TypeVar("V", covariant=True)


@dataclass(frozen=True, slots=True)
class FrozenMapping(Mapping[K, V]):
    """A tuple-backed mapping with deterministic iteration and no mutation API."""

    _items: tuple[tuple[K, V], ...]

    @overload
    @classmethod
    def from_mapping(cls, value: Mapping[str, JsonValue | FrozenJsonValue]) -> FrozenJsonObject: ...

    @overload
    @classmethod
    def from_mapping(cls, value: Mapping[K, type[T]]) -> FrozenMapping[K, type[T]]: ...

    @overload
    @classmethod
    def from_mapping(cls, value: Mapping[K, object]) -> FrozenMapping[K, object]: ...

    @classmethod
    def from_mapping(cls, value: Mapping[Any, Any]) -> FrozenMapping[Any, Any]:
        return FrozenMapping(
            tuple((key, _freeze_generic_container(item)) for key, item in value.items())
        )

    @classmethod
    def empty(cls) -> FrozenMapping[K, V]:
        return cls(())

    def __getitem__(self, key: K) -> V:
        for item_key, item_value in self._items:
            if item_key == key:
                return item_value
        raise KeyError(key)

    def __iter__(self) -> Iterator[K]:
        return (key for key, _ in self._items)

    def __len__(self) -> int:
        return len(self._items)


type FrozenJsonValue = (
    JsonScalar | tuple["FrozenJsonValue", ...] | FrozenMapping[str, "FrozenJsonValue"]
)
type FrozenJsonObject = FrozenMapping[str, FrozenJsonValue]


def _freeze_generic_container(value: object) -> object:
    if isinstance(value, Mapping):
        return FrozenMapping.from_mapping(value)
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_generic_container(item) for item in value)
    return value


def deep_freeze_json(value: JsonValue | FrozenJsonValue) -> FrozenJsonValue:
    if isinstance(value, float) and not isfinite(value):
        raise TypeError("JSON numbers must be finite")
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Mapping):
        frozen_items: list[tuple[str, FrozenJsonValue]] = []
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError("JSON object keys must be strings")
            frozen_items.append((key, deep_freeze_json(item)))
        return FrozenMapping(tuple(frozen_items))
    if isinstance(value, (list, tuple)):
        return tuple(deep_freeze_json(item) for item in value)
    raise TypeError(f"not a JSON value: {type(value).__name__}")


def thaw_json(value: FrozenJsonValue) -> JsonValue:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, FrozenMapping):
        return {key: thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [thaw_json(item) for item in value]
    raise TypeError(f"not a frozen JSON value: {type(value).__name__}")
