from CGS import CGS, Gateway, LivingGraph


def test_living_graph_without_transfer_has_no_state(graph) -> None:
    living = LivingGraph(graph, Gateway(graph))
    assert living.state is None
    assert living.left is None
    assert living.right is None


def test_successful_named_living_graph_owns_authoritative_state(
    graph, candidate, memory_system, server_gateway
) -> None:
    result = CGS.serve(graph.name, graph, memory_system, candidate, server_gateway)

    assert result.ok
    assert result.living_graph.graph.name == "Demo"
    assert result.living_graph.state.graph_name == result.living_graph.graph.name
    assert result.living_graph.state is not None
