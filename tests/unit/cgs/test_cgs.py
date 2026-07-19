import json

import pytest
import CGS as cgs_package

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


SECRET_DIAGNOSTIC = (
    "credential=hunter2 .@ RIGHT=private-content "
    "HOME=/private/home PATH=/private/bin env=production"
)


class ExplodingOperator:
    def candidate_state(self, graph):
        raise RuntimeError(SECRET_DIAGNOSTIC)


class MutatingRaiseMemory(MemorySystem):
    def persist(self, state, *, _authority=None):
        super().persist(state, _authority=_authority)
        raise RuntimeError(SECRET_DIAGNOSTIC)


class MutatingReturnMemory(MemorySystem):
    def persist(self, state, *, _authority=None):
        super().persist(state, _authority=_authority)
        return MemoryResult(
            error=CGSError(ErrorCode.MEMORY_REJECTED, SECRET_DIAGNOSTIC, "private/path")
        )


class MutatingRaiseServer(ServerGateway):
    def publish(self, publication, *, _authority=None):
        super().publish(publication, _authority=_authority)
        raise RuntimeError(SECRET_DIAGNOSTIC)


class MutatingReturnServer(ServerGateway):
    def publish(self, publication, *, _authority=None):
        super().publish(publication, _authority=_authority)
        return CGSError(ErrorCode.SERVER_REJECTED, SECRET_DIAGNOSTIC, "private/path")


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
    assert not hasattr(cgs_package, "CGS_AUTHORITY")


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


def test_operator_exception_is_stably_typed_and_fully_redacted(graph) -> None:
    memory = MemorySystem()
    server = ServerGateway()

    result = CGS.serve("Demo", graph, memory, ExplodingOperator(), server)
    public = json.dumps(result.to_public_dict(), sort_keys=True)

    assert result.error.code == ErrorCode.OPERATOR_FAILED
    assert result.error.message == "CGS rejected the operation"
    for private_fragment in (
        "credential",
        "hunter2",
        ".@",
        "private-content",
        "HOME",
        "PATH",
        "/private",
        "production",
    ):
        assert private_fragment not in public
    assert memory.records == ()
    assert server.publications == ()


@pytest.mark.parametrize("memory_type", (MutatingRaiseMemory, MutatingReturnMemory))
def test_memory_commit_failure_rolls_back_all_side_effects(graph, candidate, memory_type) -> None:
    memory = memory_type()
    server = ServerGateway()

    result = CGS.serve("Demo", graph, memory, candidate, server)
    public = json.dumps(result.to_public_dict(), sort_keys=True)

    assert result.error.code == ErrorCode.SERVICE_COMMIT_FAILED
    assert result.living_graph is None
    assert result.state_ontology is None
    assert result.state_core_graph is None
    assert memory.records == ()
    assert server.publications == ()
    assert SECRET_DIAGNOSTIC not in public
    assert "hunter2" not in public


@pytest.mark.parametrize("server_type", (MutatingRaiseServer, MutatingReturnServer))
def test_publication_commit_failure_rolls_back_memory_and_server(
    graph, candidate, server_type
) -> None:
    memory = MemorySystem()
    server = server_type()

    result = CGS.serve("Demo", graph, memory, candidate, server)
    public = json.dumps(result.to_public_dict(), sort_keys=True)

    assert result.error.code == ErrorCode.SERVICE_COMMIT_FAILED
    assert result.living_graph is None
    assert result.state_ontology is None
    assert result.state_core_graph is None
    assert memory.records == ()
    assert server.publications == ()
    assert SECRET_DIAGNOSTIC not in public
    assert "hunter2" not in public
