from __future__ import annotations

from dataclasses import dataclass

from .errors import ErrorCode, OwnershipError


def _validate_digest(value: str, label: str) -> None:
    if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise ValueError(f"{label} must be a lowercase SHA-256 digest")


@dataclass(frozen=True, slots=True, init=False)
class StateId:
    value: str

    def __init_subclass__(cls, **_kwargs: object) -> None:
        raise TypeError("StateId is sealed")

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        raise OwnershipError(
            ErrorCode.OWNERSHIP_VIOLATION,
            "authoritative StateId can only be created by the CGS kernel",
        )

    def __str__(self) -> str:
        return self.value


def _new_state_id(value: str) -> StateId:
    _validate_digest(value, "StateId")
    state_id = object.__new__(StateId)
    object.__setattr__(state_id, "value", value)
    return state_id
