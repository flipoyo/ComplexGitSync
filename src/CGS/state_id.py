from __future__ import annotations

from dataclasses import dataclass

from ._authority import require_cgs_authority


@dataclass(frozen=True, slots=True, init=False)
class StateId:
    value: str

    def __init__(self, value: str, *, _authority: object | None = None) -> None:
        require_cgs_authority(_authority)
        if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
            raise ValueError("StateId must be a lowercase SHA-256 digest")
        object.__setattr__(self, "value", value)

    def __str__(self) -> str:
        return self.value
