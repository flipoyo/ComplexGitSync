import json

import CGS as cgs_package
import pytest

from CGS import (
    CGS,
    CandidateState,
    ErrorCode,
    Graph,
    L0,
    LivingGraph,
    MemorySystem,
    ServerGateway,
    State,
    StateCoreGraph,
    StateId,
    StateOntology,
)

from .driver_helpers import RecordingDriver


class ExplodingOperator:
    def candidate_state(self, graph):
        raise RuntimeError("credential=hunter2 .@ RIGHT=private HOME=/private PATH=/bin")


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
    assert type(result.living_graph) is LivingGraph
    assert type(result.state_ontology) is StateOntology
    assert type(result.state_core_graph) is StateCoreGraph
    assert result.living_graph.gateway == type(result.living_graph.gateway)(graph)
    assert result.state_ontology.to_json() != result.state_core_graph.to_json()
    assert json.dumps(result.to_public_dict(), sort_keys=True) == json.dumps(
        result.to_public_dict(), sort_keys=True
    )


def test_graph_name_mismatch_is_atomic(graph, candidate, memory_system, server_gateway) -> None:
    result = CGS.serve("Other", graph, memory_system, candidate, server_gateway)

    assert result.error.code == ErrorCode.GRAPH_NAME_MISMATCH
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


def test_operator_exception_is_stably_redacted(graph) -> None:
    memory = MemorySystem()
    server = ServerGateway()

    result = CGS.serve("Demo", graph, memory, ExplodingOperator(), server)
    public = json.dumps(result.to_public_dict(), sort_keys=True)

    assert result.error.code == ErrorCode.OPERATOR_FAILED
    for fragment in ("credential", "hunter2", ".@", "private", "HOME", "PATH"):
        assert fragment not in public
    assert memory.records == ()
    assert server.publications == ()


@pytest.mark.parametrize(
    ("component", "operation"),
    (("memory", "memory.prepare"), ("memory", "memory.persist"), ("server", "publish")),
)
def test_driver_failures_are_atomic_and_redacted(graph, candidate, component, operation) -> None:
    memory_driver = RecordingDriver()
    server_driver = RecordingDriver()
    driver = memory_driver if component == "memory" else server_driver
    driver.behavior[operation] = "raise"
    memory = MemorySystem(driver=memory_driver)
    server = ServerGateway(driver=server_driver)

    result = CGS.serve("Demo", graph, memory, candidate, server)
    public = json.dumps(result.to_public_dict(), sort_keys=True)

    assert not result.ok
    assert result.living_graph is None
    assert memory.records == ()
    assert memory.memory_state is None
    assert server.publications == ()
    for fragment in ("credential", ".@", "RIGHT", "/env", "PATH"):
        assert fragment not in public
