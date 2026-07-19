import json

import pytest

from CGS import CGS, CandidateState, ErrorCode, MemorySystem, ServerGateway
from CGS.serialization import canonical_json

from .driver_helpers import RecordingDriver


def test_rollback_restores_records_publications_and_current_ms_state(graph) -> None:
    driver = RecordingDriver()
    memory = MemorySystem(driver=driver)
    server = ServerGateway(driver=driver)
    first = CGS.serve("Demo", graph, memory, CandidateState("Demo", 1, 1, {"revision": 1}), server)
    assert first.ok
    memory_before = memory._journal_snapshot()
    server_before = server._journal_snapshot()
    decoded_records = memory.records
    decoded_ms_state = memory.memory_state
    decoded_publications = server.publications
    driver.behavior["publish"] = "reject"

    failed = CGS.serve("Demo", graph, memory, CandidateState("Demo", 2, 2, {"revision": 2}), server)

    assert not failed.ok
    assert failed.error.code == ErrorCode.SERVICE_COMMIT_FAILED
    assert memory._journal_snapshot() == memory_before
    assert server._journal_snapshot() == server_before
    assert memory.records == decoded_records
    assert memory.memory_state == decoded_ms_state
    assert server.publications == decoded_publications


@pytest.mark.parametrize(
    "field",
    ("state_id", "graph_binding", "gateway_binding", "state_digest", "record_digest"),
)
def test_recovery_rejects_forged_sealed_memory_blobs(graph, candidate, field) -> None:
    memory = MemorySystem()
    served = CGS.serve("Demo", graph, memory, candidate, ServerGateway())
    assert served.ok
    requested = served.living_graph.state.state_id.value
    value = json.loads(memory._record_blobs[0])
    value[field] = "f" * 64 if value[field] != "f" * 64 else "e" * 64
    object.__setattr__(memory, "_record_blobs", (canonical_json(value),))

    recovered = CGS.recover(memory, requested)

    assert not recovered.ok
    assert recovered.state is None
    assert recovered.record is None
    assert recovered.error.code in {ErrorCode.MEMORY_CORRUPT, ErrorCode.MEMORY_NOT_FOUND}
    assert recovered.error.message == "CGS rejected the operation"


def test_recovery_rejects_noncanonical_and_non_json_memory_blobs(graph, candidate) -> None:
    memory = MemorySystem()
    served = CGS.serve("Demo", graph, memory, candidate, ServerGateway())
    requested = served.living_graph.state.state_id.value

    for forged in ("not-json", '{ "state_id": "%s" }' % requested):
        object.__setattr__(memory, "_record_blobs", (forged,))
        recovered = CGS.recover(memory, requested)
        assert not recovered.ok
        assert recovered.state is None
        assert recovered.error.code == ErrorCode.MEMORY_CORRUPT


def test_recovery_fails_closed_when_any_journal_blob_is_corrupt(graph, candidate) -> None:
    memory = MemorySystem()
    served = CGS.serve("Demo", graph, memory, candidate, ServerGateway())
    requested = served.living_graph.state.state_id.value
    valid = memory._record_blobs[0]
    object.__setattr__(memory, "_record_blobs", ("not-json", valid))

    recovered = CGS.recover(memory, requested)

    assert not recovered.ok
    assert recovered.error.code == ErrorCode.MEMORY_CORRUPT


def test_operator_receives_detached_graph_and_cannot_mutate_kernel_input(graph) -> None:
    seen = []

    class MutatingOperator:
        def candidate_state(self, detached_graph):
            seen.append(detached_graph)
            object.__setattr__(detached_graph, "name", "Changed")
            return CandidateState("Demo", {"tree": 1}, {"tree": 1}, {})

    result = CGS.serve("Demo", graph, MemorySystem(), MutatingOperator(), ServerGateway())

    assert result.ok
    assert seen[0] is not graph
    assert graph.name == "Demo"
    assert result.living_graph.graph.name == "Demo"


def test_callback_mutation_cannot_change_deep_snapshot_or_committed_values(graph) -> None:
    driver = RecordingDriver()
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
    ):
        driver.behavior[operation] = "mutate_detached"
    memory = MemorySystem(driver=driver)
    server = ServerGateway(driver=driver)
    candidate = CandidateState("Demo", {"tree": [1]}, {"tree": [1]}, {"runtime": 1})

    result = CGS.serve("Demo", graph, memory, candidate, server)

    assert result.ok
    assert memory.records[0].left == candidate.left
    assert result.living_graph.state.payload == candidate.payload
    assert "credential" not in canonical_json(server.publications[0].to_dict())


def test_recovery_driver_cannot_supply_or_mutate_records(graph, candidate) -> None:
    driver = RecordingDriver()
    memory = MemorySystem(driver=driver)
    served = CGS.serve("Demo", graph, memory, candidate, ServerGateway())
    before = memory._journal_snapshot()
    driver.behavior["memory.recover"] = "mutate_detached"

    recovered = CGS.recover(memory, served.living_graph.state.state_id)

    assert recovered.ok
    assert recovered.record == memory.records[0]
    assert memory._journal_snapshot() == before
    operation, message = driver.calls[-1]
    assert operation == "memory.recover"
    assert set(json.loads(message)) == {"state_id"}


def test_invalid_recovery_identity_does_not_reach_driver() -> None:
    driver = RecordingDriver()
    recovered = CGS.recover(MemorySystem(driver=driver), "credential=hidden")

    assert not recovered.ok
    assert recovered.error.code == ErrorCode.MEMORY_REJECTED
    assert driver.calls == []


def test_publication_property_is_a_fresh_decoded_value(graph, candidate) -> None:
    server = ServerGateway()
    served = CGS.serve("Demo", graph, MemorySystem(), candidate, server)
    assert served.ok

    publication = server.publications[0]
    object.__setattr__(publication, "state_id", "f" * 64)

    assert server.publications[0].state_id == served.living_graph.state.state_id.value


def test_no_importable_authority_module_or_capability() -> None:
    import CGS

    assert not hasattr(CGS, "CGS_AUTHORITY")
    with pytest.raises(ModuleNotFoundError):
        __import__("CGS._authority")
