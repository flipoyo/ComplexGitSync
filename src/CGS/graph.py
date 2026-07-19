from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .serialization import FrozenJson, freeze_json, thaw_json


@dataclass(frozen=True, slots=True, init=False)
class Graph:
    """The sole static PRIME G data contract."""

    name: str
    node: FrozenJson
    edge: FrozenJson
    op: FrozenJson

    def __init__(self, name: str, node: Any, edge: Any, op: Any) -> None:
        if not isinstance(name, str) or not name:
            raise ValueError("Graph.name must be a non-empty string")
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "node", freeze_json(node))
        object.__setattr__(self, "edge", freeze_json(edge))
        object.__setattr__(self, "op", freeze_json(op))

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "node": thaw_json(self.node),
            "edge": thaw_json(self.edge),
            "op": thaw_json(self.op),
        }
