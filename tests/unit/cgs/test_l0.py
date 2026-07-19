import hashlib
import json

import pytest

from CGS import CGS, L0, MemorySystem, OwnershipError, ServerGateway
from CGS.L0 import _anchor_occurrence, _new_l0, _state_id_for_occurrence


def test_l0_public_construction_always_fails() -> None:
    with pytest.raises(OwnershipError):
        L0()
    with pytest.raises((OwnershipError, TypeError)):
        L0("forged")  # type: ignore[call-arg]


def test_l0_subclass_construction_is_rejected() -> None:
    with pytest.raises(TypeError, match="sealed"):
        type("ForgedL0", (L0,), {})


def test_internal_l0_occurrences_are_strictly_ordered_and_redacted() -> None:
    l0 = _new_l0()
    first_anchor, first_id = _anchor_occurrence(l0)
    second_anchor, second_id = _anchor_occurrence(l0)

    assert first_anchor.occurrence_ns < second_anchor.occurrence_ns
    assert first_id != second_id
    assert repr(first_anchor) == "<_PrivateAnchor redacted>"
    assert first_anchor.value.hex() not in repr(first_anchor)


def test_fixed_internal_occurrence_has_deterministic_sha256_identity() -> None:
    occurrence_ns = 1_725_000_000_000_000_001
    expected = hashlib.sha256(b"CGS-L0-v1\x00" + str(occurrence_ns).encode("ascii")).hexdigest()

    assert _state_id_for_occurrence(occurrence_ns).value == expected


def test_identical_candidates_create_distinct_occurrences(graph, candidate) -> None:
    memory = MemorySystem()
    server = ServerGateway()
    first = CGS.serve("Demo", graph, memory, candidate, server)
    second = CGS.serve("Demo", graph, memory, candidate, server)

    assert first.ok and second.ok
    assert first.living_graph.state.state_id != second.living_graph.state.state_id
    assert len(memory.records) == 2
    assert memory.memory_state is not None
    public = json.dumps(second.to_public_dict(), sort_keys=True)
    assert ".@" not in public
