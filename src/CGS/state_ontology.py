from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .graph import Graph, validate_graph_name, validate_public_graph_value
from .serialization import FrozenJson, canonical_json, freeze_json, thaw_json


@dataclass(frozen=True, slots=True)
class StateOntology:
    """STATE@.md: a static four-member PRIME G projection."""

    name: str
    node: FrozenJson
    edge: FrozenJson
    op: FrozenJson

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", validate_graph_name(self.name))
        for field_name in ("node", "edge", "op"):
            value = thaw_json(getattr(self, field_name))
            validate_public_graph_value(value)
            object.__setattr__(self, field_name, freeze_json(value))

    @classmethod
    def from_graph(cls, graph: Graph) -> "StateOntology":
        canonical = graph.detached_copy()
        return cls(
            name=canonical.name,
            node=canonical.node,
            edge=canonical.edge,
            op=canonical.op,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "node": thaw_json(self.node),
            "edge": thaw_json(self.edge),
            "op": thaw_json(self.op),
        }

    def to_json(self) -> str:
        return canonical_json(self.to_dict())
