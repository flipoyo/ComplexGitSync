from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .graph import Graph
from .serialization import FrozenJson, canonical_json, thaw_json


@dataclass(frozen=True, slots=True)
class StateOntology:
    """STATE@.md: a static four-member PRIME G projection."""

    name: str
    node: FrozenJson
    edge: FrozenJson
    op: FrozenJson

    @classmethod
    def from_graph(cls, graph: Graph) -> "StateOntology":
        return cls(name=graph.name, node=graph.node, edge=graph.edge, op=graph.op)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "node": thaw_json(self.node),
            "edge": thaw_json(self.edge),
            "op": thaw_json(self.op),
        }

    def to_json(self) -> str:
        return canonical_json(self.to_dict())
