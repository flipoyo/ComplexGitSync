import json

from CGS import (
    CGS,
    CGSError,
    CandidateState,
    ErrorCode,
    Graph,
    L0,
    LivingGraph,
    MemorySystem,
    MemoryResult,
    ServerGateway,
    State,
    StateCoreGraph,
    StateId,
    StateOntology,
)


class RejectingMemory(MemorySystem):
    def prepare(self, state):
        return MemoryResult(error=CGSError(ErrorCode.MEMORY_REJECTED, "rejected", "memory.prepare"))


class RejectingServer(ServerGateway):
    def prepare_publication(self, living_graph, state_ontology, state_core_graph):
        return CGSError(ErrorCode.SERVER_REJECTED, "rejected", "server.prepare")


def test_required_package_exports_are_importable() -> None:
    assert all(
        value is not None
        for value in (
            Graph,
            LivingGraph,
            State,
            StateOntology,
            StateCoreGraph,
            L0,
            StateId,
            MemorySystem,
            ServerGateway,
            CGS,
        )
    )


def test_cgs_service_returns_all_canonical_results(
    graph, candidate, memory_system, server_gateway
) -> None:
    result = CGS.serve("Demo", graph, memory_system, candidate, server_gateway)

    assert result.ok
    assert isinstance(result.living_graph, LivingGraph)
    assert isinstance(result.state_ontology, StateOntology)
    assert isinstance(result.state_core_graph, StateCoreGraph)
    assert result.state_ontology.to_json() != result.state_core_graph.to_json()
    assert json.dumps(result.to_public_dict(), sort_keys=True) == json.dumps(
        result.to_public_dict(), sort_keys=True
    )


def test_graph_name_mismatch_is_atomic(graph, candidate, memory_system, server_gateway) -> None:
    result = CGS.serve("Other", graph, memory_system, candidate, server_gateway)

    assert not result.ok
    assert result.error.code == ErrorCode.GRAPH_NAME_MISMATCH
    assert result.living_graph is None
    assert result.state_ontology is None
    assert result.state_core_graph is None
    assert memory_system.records == ()
    assert server_gateway.publications == ()


def test_partial_state_returns_typed_error_without_side_effects(
    graph, memory_system, server_gateway
) -> None:
    partial = CandidateState("Demo", {}, {}, {}, complete=False)
    result = CGS.serve("Demo", graph, memory_system, partial, server_gateway)

    assert result.error.code == ErrorCode.PARTIAL_STATE
    assert memory_system.records == ()
    assert server_gateway.publications == ()


def test_memory_prepare_failure_has_no_partial_publication(graph, candidate) -> None:
    memory = RejectingMemory()
    server = ServerGateway()

    result = CGS.serve("Demo", graph, memory, candidate, server)

    assert result.error.code == ErrorCode.MEMORY_REJECTED
    assert result.living_graph is None
    assert memory.records == ()
    assert server.publications == ()


def test_server_prepare_failure_has_no_partial_memory_write(graph, candidate) -> None:
    memory = MemorySystem()
    server = RejectingServer()

    result = CGS.serve("Demo", graph, memory, candidate, server)

    assert result.error.code == ErrorCode.SERVER_REJECTED
    assert result.living_graph is None
    assert memory.records == ()
    assert server.publications == ()
