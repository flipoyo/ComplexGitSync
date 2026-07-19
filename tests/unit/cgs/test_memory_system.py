from CGS import CGS, CandidateState, ErrorCode, MemorySystem


def test_memory_rejects_candidate_without_side_effects(candidate) -> None:
    memory = MemorySystem()

    rejected = memory.persist(candidate)

    assert not rejected.ok
    assert rejected.error.code == ErrorCode.MEMORY_REJECTED
    assert memory.records == ()


def test_memory_persists_immutable_deterministic_public_record_only(
    graph, candidate, server_gateway
) -> None:
    memory = MemorySystem()
    result = CGS.serve("Demo", graph, memory, candidate, server_gateway)

    assert result.ok
    assert len(memory.records) == 1
    record = memory.records[0]
    assert record.graph_name == "Demo"
    assert record.state_id == result.living_graph.state.state_id.value
    assert set(record.to_dict()) == {"graph_name", "state_id", "public_digest"}
    assert "runtime" not in str(record.to_dict())


def test_invalid_candidate_causes_no_memory_write(graph, server_gateway) -> None:
    memory = MemorySystem()
    invalid = CandidateState("Demo", {"left": 1}, {"right": 2}, {})

    result = CGS.serve("Demo", graph, memory, invalid, server_gateway)

    assert not result.ok
    assert memory.records == ()
