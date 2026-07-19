from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Mapping, Sequence, TypeAlias


JsonScalar: TypeAlias = str | int | float | bool | None


@dataclass(frozen=True, slots=True)
class FrozenObject:
    items: tuple[tuple[str, "FrozenJson"], ...]


@dataclass(frozen=True, slots=True)
class FrozenArray:
    items: tuple["FrozenJson", ...]


FrozenJson: TypeAlias = JsonScalar | FrozenObject | FrozenArray


def freeze_json(value: Any) -> FrozenJson:
    """Copy JSON data into an immutable, deterministically ordered value."""

    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            raise ValueError("non-finite numbers are not deterministic JSON")
        return value
    if isinstance(value, Mapping):
        if not all(isinstance(key, str) for key in value):
            raise TypeError("JSON object keys must be strings")
        return FrozenObject(tuple((key, freeze_json(value[key])) for key in sorted(value)))
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return FrozenArray(tuple(freeze_json(item) for item in value))
    if isinstance(value, (FrozenObject, FrozenArray)):
        return value
    raise TypeError(f"value of type {type(value).__name__} is not deterministic JSON")


def thaw_json(value: FrozenJson) -> Any:
    if isinstance(value, FrozenObject):
        return {key: thaw_json(item) for key, item in value.items}
    if isinstance(value, FrozenArray):
        return [thaw_json(item) for item in value.items]
    return value


def canonical_json(value: Any) -> str:
    frozen = freeze_json(value)
    return json.dumps(
        thaw_json(frozen),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )
