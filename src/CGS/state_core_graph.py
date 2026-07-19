from __future__ import annotations

from dataclasses import dataclass

from .errors import CGSContractError, ErrorCode
from .graph import validate_graph_name
from .serialization import canonical_json


@dataclass(frozen=True, slots=True)
class StateCoreGraph:
    """STATE@.CORE.md: the safe public living-Gateway projection."""

    graph_name: str
    mermaid: str

    def __post_init__(self) -> None:
        graph_name = validate_graph_name(self.graph_name)
        if self.mermaid != self._render(graph_name):
            raise CGSContractError(
                ErrorCode.EMISSION_REJECTED,
                "StateCoreGraph must be the canonical public Gateway projection",
            )

    @classmethod
    def for_graph(cls, graph_name: str) -> "StateCoreGraph":
        graph_name = validate_graph_name(graph_name)
        return cls(graph_name=graph_name, mermaid=cls._render(graph_name))

    @staticmethod
    def _render(graph_name: str) -> str:
        return "\n".join(
            (
                "flowchart LR",
                '    PUBLIC[".PUBLIC"] <--> X["X / Gateway boundary"]',
                f'    X <--> LIVING["*{graph_name}<br/>STATE@"]',
                '    LEFT["LEFT"] --> X',
                '    RIGHT["RIGHT"] --> X',
            )
        )

    def to_dict(self) -> dict[str, str]:
        return {"graph_name": self.graph_name, "mermaid": self.mermaid}

    def to_json(self) -> str:
        return canonical_json(self.to_dict())
