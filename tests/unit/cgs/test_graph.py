from dataclasses import fields

import pytest

from CGS import Graph


def test_graph_is_exact_static_four_field_contract() -> None:
    graph = Graph("G", ["node"], ["edge"], "op")

    assert tuple(field.name for field in fields(graph)) == ("name", "node", "edge", "op")
    assert graph.to_dict() == {
        "name": "G",
        "node": ["node"],
        "edge": ["edge"],
        "op": "op",
    }
    assert not hasattr(graph, "state")


def test_graph_rejects_state_constructor_argument() -> None:
    with pytest.raises(TypeError):
        Graph("G", [], [], "op", state={})  # type: ignore[call-arg]
