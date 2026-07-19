from __future__ import annotations

from typing import Any

from CGS import CGS


def serve(
    graph_name: str,
    graph: Any,
    memory_system: Any,
    operator: Any,
    server_gateway: Any,
) -> Any:
    """Submit a ComplexGitSync candidate to the public @CGS facade."""

    return CGS.serve(graph_name, graph, memory_system, operator, server_gateway)
