import hashlib
from dataclasses import FrozenInstanceError

import pytest

from CGS import CGS, CandidateState, ErrorCode, Gateway, Graph, MemorySystem, State
from CGS.serialization import canonical_json, thaw_json


def test_memory_rejects_candidate_without_side_effects(candidate) -> None:
    memory = MemorySystem()

    rejected = memory.persist(candidate)

    assert not rejected.ok
    assert rejected.error.code == ErrorCode.MEMORY_REJECTED
    assert memory.records == ()
    assert memory.memory_state is None


def test_memory_persists_complete_immutable_authoritative_state(
    graph, candidate, server_gateway
) -> None:
    memory = MemorySystem()
    result = CGS.serve("Demo", graph, memory, candidate, server_gateway)

    assert result.ok
    state = result.living_graph.state
    record = memory.records[0]
    assert record.state_data() == state.to_memory_data()
    assert record.state_digest == state.state_digest
    assert set(record.to_dict()) == {
        "graph_name",
        "graph_binding",
        "gateway_binding",
        "state_id",
        "left",
        "right",
        "payload",
        "validated",
        "state_digest",
        "record_digest",
    }
    expected_record_digest = hashlib.sha256(
        b"CGS-MEMORY-RECORD-v1\x00"
        + canonical_json({**record.state_data(), "state_digest": record.state_digest}).encode()
    ).hexdigest()
    assert record.record_digest == expected_record_digest
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


def test_each_commit_materializes_canonical_distinct_memory_state(graph, server_gateway) -> None:
    memory = MemorySystem()
    first = CGS.serve(
        "Demo", graph, memory, CandidateState("Demo", 1, 1, {"commit": 1}), server_gateway
    )
    first_ms = memory.memory_state
    second = CGS.serve(
        "Demo", graph, memory, CandidateState("Demo", 2, 2, {"commit": 2}), server_gateway
    )
    second_ms = memory.memory_state

    assert first.ok and second.ok
    assert memory.graph == Graph("MemorySystem", node="G", edge="Storage", op="Persist")
    assert memory.gateway == Gateway(memory.graph)
    assert type(first_ms) is State and type(second_ms) is State
    assert first_ms.state_id != second_ms.state_id
    assert first_ms.graph_binding == memory.graph.digest
    assert first_ms.gateway_binding == memory.gateway.binding
    second_record = memory.records[-1]
    source = thaw_json(second_ms.left)
    assert second_ms.left == second_ms.right
    assert source == {
        "source_state_id": second_record.state_id,
        "source_graph_binding": second_record.graph_binding,
        "source_gateway_binding": second_record.gateway_binding,
        "source_state_digest": second_record.state_digest,
        "record_digest": second_record.record_digest,
    }
    assert set(thaw_json(second_ms.payload)) == {"journal_digest"}


def test_memory_content_and_ms_projection_are_not_published(
    graph, candidate, server_gateway
) -> None:
    memory = MemorySystem()
    result = CGS.serve("Demo", graph, memory, candidate, server_gateway)

    assert result.ok
    publication = server_gateway.publications[0]
    public_text = canonical_json(publication.to_dict())
    assert "runtime" not in public_text
    assert "kept private" not in public_text
    assert "MemorySystem" not in public_text
    assert publication.state_id == result.living_graph.state.state_id.value


def test_invalid_candidate_causes_no_memory_write(graph, server_gateway) -> None:
    memory = MemorySystem()
    invalid = CandidateState("Demo", {"left": 1}, {"right": 2}, {})

    result = CGS.serve("Demo", graph, memory, invalid, server_gateway)

    assert not result.ok
    assert memory.records == ()
    assert memory.memory_state is None


def test_decoded_memory_properties_are_fresh_values(graph, candidate, server_gateway) -> None:
    memory = MemorySystem()
    served = CGS.serve("Demo", graph, memory, candidate, server_gateway)
    assert served.ok

    record = memory.records[0]
    memory_state = memory.memory_state
    object.__setattr__(record, "graph_name", "Mutated")
    object.__setattr__(memory_state, "graph_name", "Mutated")

    assert memory.records[0].graph_name == "Demo"
    assert memory.memory_state.graph_name == "MemorySystem"
