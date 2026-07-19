from CGS import StateCoreGraph


def test_state_core_graph_is_distinct_safe_living_gateway_projection() -> None:
    projection = StateCoreGraph.for_graph("Demo")

    assert ".PUBLIC" in projection.mermaid
    assert "Gateway boundary" in projection.mermaid
    assert "*Demo" in projection.mermaid
    assert "STATE@" in projection.mermaid
    assert "LEFT" in projection.mermaid
    assert "RIGHT" in projection.mermaid
    for forbidden in (".@", "credential", "kept private", "raw execution memory"):
        assert forbidden not in projection.to_json()
