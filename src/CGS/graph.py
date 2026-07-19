from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

from .errors import CGSContractError, ErrorCode
from .serialization import FrozenJson, freeze_json, thaw_json


GRAPH_NAME_PATTERN = r"@?[A-Za-z][A-Za-z0-9]*(?:[._:-][A-Za-z0-9]+)*"
_GRAPH_NAME_RE = re.compile(rf"\A{GRAPH_NAME_PATTERN}\Z", re.ASCII)


def validate_graph_name(name: object) -> str:
    """Validate the language-neutral public Graph identifier grammar."""

    if not isinstance(name, str) or not _GRAPH_NAME_RE.fullmatch(name) or ".@" in name:
        raise CGSContractError(
            ErrorCode.INVALID_GRAPH_NAME,
            "Graph name does not satisfy the canonical public identifier grammar",
        )
    return name


@dataclass(frozen=True, slots=True, init=False)
class Graph:
    """The sole static PRIME G data contract."""

    name: str
    node: FrozenJson
    edge: FrozenJson
    op: FrozenJson

    def __init__(self, name: str, node: Any, edge: Any, op: Any) -> None:
        object.__setattr__(self, "name", validate_graph_name(name))
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
