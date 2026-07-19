from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ._authority import _AuthorityScope, require_cgs_authority
from .graph import Graph
from .serialization import FrozenJson
from .state import State

if TYPE_CHECKING:
    from .gateway import Gateway


@dataclass(frozen=True, slots=True, init=False)
class LivingGraph:
    graph: Graph
    gateway: "Gateway"
    state: State | None
    left: FrozenJson | None
    right: FrozenJson | None

    def __init__(self, graph: Graph, gateway: "Gateway") -> None:
        object.__setattr__(self, "graph", graph)
        object.__setattr__(self, "gateway", gateway)
        object.__setattr__(self, "state", None)
        object.__setattr__(self, "left", None)
        object.__setattr__(self, "right", None)

    @classmethod
    def _with_state(
        cls,
        graph: Graph,
        gateway: "Gateway",
        state: State,
        *,
        _authority: _AuthorityScope,
    ) -> "LivingGraph":
        require_cgs_authority(_authority)
        living = cls(graph, gateway)
        object.__setattr__(living, "state", state)
        object.__setattr__(living, "left", state.left)
        object.__setattr__(living, "right", state.right)
        return living
