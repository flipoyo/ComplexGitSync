from __future__ import annotations

from dataclasses import dataclass

from .serialization import canonical_json


@dataclass(frozen=True, slots=True)
class StateCoreGraph:
    """STATE@.CORE.md: the safe public living-Gateway projection."""

    graph_name: str
    mermaid: str

    @classmethod
    def for_graph(cls, graph_name: str) -> "StateCoreGraph":
        mermaid = "\n".join(
            (
                "flowchart LR",
                '    PUBLIC[".PUBLIC"] <--> X["X / Gateway boundary"]',
                f'    X <--> LIVING["*{graph_name}<br/>STATE@"]',
                '    LEFT["LEFT"] --> X',
                '    RIGHT["RIGHT"] --> X',
            )
        )
        return cls(graph_name=graph_name, mermaid=mermaid)

    def to_dict(self) -> dict[str, str]:
        return {"graph_name": self.graph_name, "mermaid": self.mermaid}

    def to_json(self) -> str:
        return canonical_json(self.to_dict())
