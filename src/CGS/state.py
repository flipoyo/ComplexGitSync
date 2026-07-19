from __future__ import annotations

from dataclasses import dataclass
import hashlib
from typing import Any

from .errors import ErrorCode, OwnershipError
from .graph import validate_graph_name
from .serialization import FrozenJson, canonical_json, freeze_json, thaw_json
from .state_id import StateId, _new_state_id, _validate_digest


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

    def detached_copy(self) -> "CandidateState":
        values = self.to_dict()
        return CandidateState(
            values["graph_name"],
            values["left"],
            values["right"],
            values["payload"],
            complete=bool(values["complete"]),
        )


@dataclass(frozen=True, slots=True, init=False)
class State:
    """Validated authoritative State. Public construction always fails."""

    graph_name: str
    graph_binding: str
    gateway_binding: str
    state_id: StateId
    left: FrozenJson
    right: FrozenJson
    payload: FrozenJson
    validated: bool
    state_digest: str

    def __init_subclass__(cls, **_kwargs: object) -> None:
        raise TypeError("State is sealed")

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        raise OwnershipError(
            ErrorCode.OWNERSHIP_VIOLATION,
            "authoritative State can only be created by the CGS kernel",
        )

    def to_memory_data(self) -> dict[str, Any]:
        return {
            "graph_name": self.graph_name,
            "graph_binding": self.graph_binding,
            "gateway_binding": self.gateway_binding,
            "state_id": self.state_id.value,
            "left": thaw_json(self.left),
            "right": thaw_json(self.right),
            "payload": thaw_json(self.payload),
            "validated": self.validated,
        }

    def to_public_dict(self) -> dict[str, str | bool]:
        return {
            "graph_name": self.graph_name,
            "state_id": self.state_id.value,
            "validated": self.validated,
            "state_digest": self.state_digest,
        }


def _state_digest(data: dict[str, Any]) -> str:
    return hashlib.sha256(b"CGS-STATE-v1\x00" + canonical_json(data).encode("utf-8")).hexdigest()


def _new_state(
    *,
    graph_name: str,
    graph_binding: str,
    gateway_binding: str,
    state_id: StateId | str,
    left: Any,
    right: Any,
    payload: Any,
) -> State:
    graph_name = validate_graph_name(graph_name)
    _validate_digest(graph_binding, "Graph binding")
    _validate_digest(gateway_binding, "Gateway binding")
    canonical_id = state_id if type(state_id) is StateId else _new_state_id(state_id)
    frozen_left = freeze_json(thaw_json(left))
    frozen_right = freeze_json(thaw_json(right))
    frozen_payload = freeze_json(thaw_json(payload))
    data = {
        "graph_name": graph_name,
        "graph_binding": graph_binding,
        "gateway_binding": gateway_binding,
        "state_id": canonical_id.value,
        "left": thaw_json(frozen_left),
        "right": thaw_json(frozen_right),
        "payload": thaw_json(frozen_payload),
        "validated": True,
    }
    state = object.__new__(State)
    object.__setattr__(state, "graph_name", graph_name)
    object.__setattr__(state, "graph_binding", graph_binding)
    object.__setattr__(state, "gateway_binding", gateway_binding)
    object.__setattr__(state, "state_id", canonical_id)
    object.__setattr__(state, "left", frozen_left)
    object.__setattr__(state, "right", frozen_right)
    object.__setattr__(state, "payload", frozen_payload)
    object.__setattr__(state, "validated", True)
    object.__setattr__(state, "state_digest", _state_digest(data))
    return state
