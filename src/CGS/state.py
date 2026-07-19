from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ._authority import require_cgs_authority
from .serialization import FrozenJson, freeze_json, thaw_json
from .state_id import StateId


@dataclass(frozen=True, slots=True, init=False)
class CandidateState:
    graph_name: str | None
    left: FrozenJson
    right: FrozenJson
    payload: FrozenJson
    complete: bool

    def __init__(
        self,
        graph_name: str | None,
        left: Any,
        right: Any,
        payload: Any,
        *,
        complete: bool = True,
    ) -> None:
        object.__setattr__(self, "graph_name", graph_name)
        object.__setattr__(self, "left", freeze_json(left))
        object.__setattr__(self, "right", freeze_json(right))
        object.__setattr__(self, "payload", freeze_json(payload))
        object.__setattr__(self, "complete", bool(complete))

    def to_dict(self) -> dict[str, Any]:
        return {
            "graph_name": self.graph_name,
            "left": thaw_json(self.left),
            "right": thaw_json(self.right),
            "payload": thaw_json(self.payload),
            "complete": self.complete,
        }


@dataclass(frozen=True, slots=True, init=False)
class State:
    """Validated authoritative State. Construction is restricted to CGS."""

    graph_name: str
    state_id: StateId
    left: FrozenJson
    right: FrozenJson
    payload: FrozenJson
    validated: bool

    def __init__(
        self,
        graph_name: str,
        state_id: StateId,
        left: FrozenJson,
        right: FrozenJson,
        payload: FrozenJson,
        *,
        validated: bool,
        _authority: object | None = None,
    ) -> None:
        require_cgs_authority(_authority)
        if not validated:
            raise ValueError("authoritative State must be validated")
        object.__setattr__(self, "graph_name", graph_name)
        object.__setattr__(self, "state_id", state_id)
        object.__setattr__(self, "left", left)
        object.__setattr__(self, "right", right)
        object.__setattr__(self, "payload", payload)
        object.__setattr__(self, "validated", True)

    def to_public_dict(self) -> dict[str, str | bool]:
        return {
            "graph_name": self.graph_name,
            "state_id": self.state_id.value,
            "validated": self.validated,
        }
