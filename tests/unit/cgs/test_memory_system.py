import hashlib
from dataclasses import FrozenInstanceError

import pytest

from CGS import CGS, CandidateState, ErrorCode, MemorySystem, OwnershipError
from CGS.serialization import canonical_json


def test_memory_rejects_candidate_without_side_effects(candidate) -> None:
    memory = MemorySystem()

    rejected = memory.persist(candidate)

    assert not rejected.ok
    assert rejected.error.code == ErrorCode.MEMORY_REJECTED
    assert memory.records == ()


def test_memory_persists_complete_immutable_authoritative_state(
    graph, candidate, server_gateway
) -> None:
    memory = MemorySystem()
    result = CGS.serve("Demo", graph, memory, candidate, server_gateway)

    assert result.ok
    assert len(memory.records) == 1
    state = result.living_graph.state
    record = memory.records[0]
    assert record.graph_name == state.graph_name
    assert record.state_id == state.state_id.value
    assert record.left == state.left
    assert record.right == state.right
    assert record.payload == state.payload
    assert record.validated is True
    assert set(record.to_dict()) == {
        "graph_name",
        "state_id",
        "left",
        "right",
        "payload",
        "validated",
        "state_digest",
    }
    expected_digest = hashlib.sha256(
        canonical_json(record.state_data()).encode("utf-8")
    ).hexdigest()
    assert record.state_digest == expected_digest
    assert "anchor" not in record.to_dict()
    with pytest.raises(FrozenInstanceError):
        record.validated = False  # type: ignore[misc]


def test_cgs_recovers_and_verifies_authoritative_state_by_value(
    graph, candidate, server_gateway
) -> None:
    memory = MemorySystem()
    served = CGS.serve("Demo", graph, memory, candidate, server_gateway)

    recovered = CGS.recover(memory, served.living_graph.state.state_id)

    assert recovered.ok
    assert recovered.state == served.living_graph.state
    assert recovered.record == memory.records[0]
    assert (
        recovered.record.state_digest
        == hashlib.sha256(canonical_json(recovered.record.state_data()).encode("utf-8")).hexdigest()
    )


def test_memory_content_is_not_exposed_by_server_publications(
    graph, candidate, server_gateway
) -> None:
    memory = MemorySystem()
    result = CGS.serve("Demo", graph, memory, candidate, server_gateway)

    assert result.ok
    publication = server_gateway.publications[0]
    public_text = publication.state_ontology.to_json() + publication.state_core_graph.to_json()
    assert "runtime" not in public_text
    assert "kept private" not in public_text
    assert memory.records[0].payload == result.living_graph.state.payload


def test_invalid_candidate_causes_no_memory_write(graph, server_gateway) -> None:
    memory = MemorySystem()
    invalid = CandidateState("Demo", {"left": 1}, {"right": 2}, {})

    result = CGS.serve("Demo", graph, memory, invalid, server_gateway)

    assert not result.ok
    assert memory.records == ()


def test_direct_persistence_and_recovery_require_nonforgeable_authority(
    graph, candidate, server_gateway
) -> None:
    memory = MemorySystem()
    served = CGS.serve("Demo", graph, memory, candidate, server_gateway)
    state = served.living_graph.state

    for forged in (None, "cgs-kernel-v1", object()):
        with pytest.raises(OwnershipError):
            memory.persist(state, _authority=forged)
        with pytest.raises(OwnershipError):
            memory.recover(state.state_id, _authority=forged)

    assert len(memory.records) == 1
