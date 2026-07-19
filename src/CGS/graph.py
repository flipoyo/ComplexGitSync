from __future__ import annotations

from dataclasses import dataclass
import hashlib
import re
from typing import Any

from .errors import CGSContractError, ErrorCode
from .serialization import FrozenJson, freeze_json, thaw_json


GRAPH_NAME_PATTERN = r"@?[A-Za-z][A-Za-z0-9]*(?:[._:-][A-Za-z0-9]+)*"
_GRAPH_NAME_RE = re.compile(rf"\A{GRAPH_NAME_PATTERN}\Z", re.ASCII)
_PRIVATE_KEYS = frozenset(
    {
        "env",
        "environment",
        "token",
        "password",
        "secret",
        "credential",
        "credentials",
        "raw_memory",
        "gateway_internal",
        "gateway_internals",
        "runtime",
        "private_runtime",
        "process_environment",
        "private_right",
        "right_private",
    }
)
_PRIVATE_VALUE_MARKERS = (
    ".@",
    "credential=",
    "password=",
    "secret=",
    "token=",
    "private right",
    "private_right",
    "right=private",
    "right:private",
    "raw memory",
    "raw_memory",
    "raw process memory",
    "gateway internal",
    "gateway_internal",
    "runtime=",
    "private runtime",
    "process environment",
    "environment=",
    "/env/",
    "home=",
    "path=",
)


def validate_graph_name(name: object) -> str:
    """Validate the language-neutral public Graph identifier grammar."""

    if not isinstance(name, str) or not _GRAPH_NAME_RE.fullmatch(name) or ".@" in name:
        raise CGSContractError(
            ErrorCode.INVALID_GRAPH_NAME,
            "Graph name does not satisfy the canonical public identifier grammar",
        )
    return name


def validate_public_graph_value(value: Any) -> None:
    """Fail closed for values that can enter public Graph projections."""

    if value is None or isinstance(value, (bool, int, float)):
        return
    if isinstance(value, str):
        lowered = value.casefold()
        if any(marker in lowered for marker in _PRIVATE_VALUE_MARKERS):
            raise CGSContractError(
                ErrorCode.INVALID_GRAPH,
                "Graph public data violates the private-data policy",
            )
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise CGSContractError(
                    ErrorCode.INVALID_GRAPH,
                    "Graph public data must be deterministic JSON",
                )
            normalized = re.sub(r"[^a-z0-9]+", "_", key.casefold()).strip("_")
            segments = frozenset(normalized.split("_"))
            if normalized in _PRIVATE_KEYS or segments & {
                "env",
                "environment",
                "token",
                "password",
                "secret",
                "credential",
                "credentials",
                "runtime",
            }:
                raise CGSContractError(
                    ErrorCode.INVALID_GRAPH,
                    "Graph public data violates the private-data policy",
                )
            validate_public_graph_value(key)
            validate_public_graph_value(item)
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            validate_public_graph_value(item)
        return
    raise CGSContractError(
        ErrorCode.INVALID_GRAPH,
        "Graph public data must be deterministic JSON",
    )


@dataclass(frozen=True, slots=True, init=False)
class Graph:
    """The sole static PRIME G data contract."""

    name: str
    node: FrozenJson
    edge: FrozenJson
    op: FrozenJson

    def __init__(self, name: str, node: Any, edge: Any, op: Any) -> None:
        for value in (node, edge, op):
            validate_public_graph_value(thaw_json(value))
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

    def to_json(self) -> str:
        from .serialization import canonical_json

        return canonical_json(self.to_dict())

    @property
    def digest(self) -> str:
        return hashlib.sha256(b"CGS-GRAPH-v1\x00" + self.to_json().encode("utf-8")).hexdigest()

    def detached_copy(self) -> "Graph":
        values = self.to_dict()
        return Graph(values["name"], values["node"], values["edge"], values["op"])
