from dataclasses import replace
import json

import pytest

from CGS import CGS, CandidateState, ErrorCode, Gateway, Graph, MemorySystem, ServerGateway

from .driver_helpers import RecordingDriver


def test_physical_wrappers_route_pipeline_exactly_once_in_order(graph, candidate) -> None:
    driver = RecordingDriver()
    memory = MemorySystem(driver=driver)
    server = ServerGateway(driver=driver)

    result = CGS.serve("Demo", graph, memory, candidate, server)

    assert result.ok
    assert [operation for operation, _message in driver.calls] == [
        "listen",
        "interpret",
        "validate",
        "transfer",
        "emit_state_ontology",
        "emit_state_core_graph",
        "memory.prepare",
        "prepare_publication",
        "memory.persist",
        "publish",
    ]
    assert len(server.publications) == 1
    assert server.publications[0].operations == (
        "state",
        "state-core",
        "validated-operation",
    )


def test_invalid_input_stops_after_validation(graph) -> None:
    driver = RecordingDriver()
    memory = MemorySystem(driver=driver)
    server = ServerGateway(driver=driver)
    invalid = CandidateState("Demo", 1, 2, {})

    result = CGS.serve("Demo", graph, memory, invalid, server)

    assert not result.ok
    assert result.error.code == ErrorCode.VALIDATION_FAILED
    assert [operation for operation, _message in driver.calls] == [
        "listen",
        "interpret",
        "validate",
    ]
    assert memory.records == ()
    assert server.publications == ()


@pytest.mark.parametrize(
    "wrapper",
    (MemorySystem, ServerGateway),
)
def test_physical_wrappers_are_sealed(wrapper) -> None:
    with pytest.raises(TypeError, match="sealed"):
        type("AttemptedOverride", (wrapper,), {})


def test_gateway_binding_is_deterministic_value_identity(graph) -> None:
    equal_graph = Graph(graph.name, graph.node, graph.edge, graph.op)
    changed_graph = Graph(graph.name, graph.node, graph.edge, "different")

    assert Gateway(graph) == Gateway(equal_graph)
    assert Gateway(graph).binding == Gateway(equal_graph).binding
    assert Gateway(graph).binding != Gateway(changed_graph).binding


def test_server_rejects_noncanonical_publication_without_writing(graph, candidate) -> None:
    canonical_server = ServerGateway()
    served = CGS.serve("Demo", graph, MemorySystem(), candidate, canonical_server)
    assert served.ok
    publication = canonical_server.publications[0]
    server = ServerGateway()
    forged = replace(publication, operations=publication.operations + ("credential=hidden",))

    error = server.publish(forged)

    assert error.code == ErrorCode.SERVER_REJECTED
    assert "credential" not in error.message
    assert server.publications == ()


def test_driver_receives_only_detached_canonical_strings(graph, candidate) -> None:
    driver = RecordingDriver()
    driver.behavior = {
        operation: "mutate_detached"
        for operation in (
            "listen",
            "interpret",
            "validate",
            "transfer",
            "emit_state_ontology",
            "emit_state_core_graph",
            "memory.prepare",
            "prepare_publication",
            "memory.persist",
            "publish",
        )
    }
    memory = MemorySystem(driver=driver)
    server = ServerGateway(driver=driver)

    result = CGS.serve("Demo", graph, memory, candidate, server)

    assert result.ok
    assert result.living_graph.state.left == candidate.left
    assert result.living_graph.state.payload == candidate.payload
    for _operation, message in driver.calls:
        assert type(message) is str
        json.loads(message)
        lowered = message.casefold()
        for forbidden in ("_authority", "factory", "private_anchor", "l0_anchor"):
            assert forbidden not in lowered


@pytest.mark.parametrize(
    "operation",
    (
        "listen",
        "interpret",
        "validate",
        "transfer",
        "emit_state_ontology",
        "emit_state_core_graph",
        "memory.prepare",
        "prepare_publication",
        "memory.persist",
        "publish",
    ),
)
@pytest.mark.parametrize("mode", ("reject", "wrong_digest", "wrong_operation", "raise"))
def test_driver_failure_is_redacted_and_atomic(graph, candidate, operation, mode) -> None:
    driver = RecordingDriver()
    driver.behavior[operation] = mode
    memory = MemorySystem(driver=driver)
    server = ServerGateway(driver=driver)

    result = CGS.serve("Demo", graph, memory, candidate, server)

    assert not result.ok
    assert result.error.message == "CGS rejected the operation"
    assert memory.records == ()
    assert memory.memory_state is None
    assert server.publications == ()
    public = json.dumps(result.to_public_dict(), sort_keys=True).casefold()
    for fragment in ("credential", "hidden", ".@", "private", "/env", "path"):
        assert fragment not in public
