import hashlib
import json
import pickle

import pytest

from CGS import CGS, L0, MemorySystem, OwnershipError, ServerGateway
from CGS._authority import CGS_AUTHORITY, _AuthorityCapability


def test_l0_ownership_is_exclusive_and_capability_is_not_reconstructable() -> None:
    with pytest.raises(OwnershipError):
        L0()
    with pytest.raises(OwnershipError):
        L0(_authority="cgs-kernel-v1")
    with pytest.raises(TypeError):
        _AuthorityCapability()
    with pytest.raises(TypeError):
        pickle.dumps(CGS_AUTHORITY)

    reconstructed = object.__new__(_AuthorityCapability)
    with pytest.raises(OwnershipError):
        L0(_authority=reconstructed)


def test_l0_occurrences_are_strictly_ordered_and_unique() -> None:
    l0 = L0(_authority=CGS_AUTHORITY)
    first_anchor, first_id = l0._anchor(_authority=CGS_AUTHORITY)
    second_anchor, second_id = l0._anchor(_authority=CGS_AUTHORITY)

    assert first_anchor.occurrence_ns < second_anchor.occurrence_ns
    assert first_anchor.value != second_anchor.value
    assert first_id != second_id
    assert repr(first_anchor) == "<_PrivateAnchor redacted>"
    assert first_anchor.value.hex() not in repr(first_anchor)


def test_fixed_internal_occurrence_has_deterministic_sha256_identity() -> None:
    occurrence_ns = 1_725_000_000_000_000_001
    encoded = b"CGS-L0-v1\x00" + str(occurrence_ns).encode("ascii")
    expected = hashlib.sha256(encoded).hexdigest()

    state_id = L0._state_id_for_occurrence(occurrence_ns, _authority=CGS_AUTHORITY)

    assert state_id.value == expected
    assert len(state_id.value) == 64


def test_identical_candidates_create_distinct_private_occurrences(graph, candidate) -> None:
    memory = MemorySystem()
    server = ServerGateway()
    first = CGS.serve("Demo", graph, memory, candidate, server)
    second = CGS.serve("Demo", graph, memory, candidate, server)

    assert first.ok and second.ok
    assert first.living_graph.state.state_id != second.living_graph.state.state_id
    assert len(first.living_graph.state.state_id.value) == 64
    assert len(second.living_graph.state.state_id.value) == 64
    assert len(memory.records) == 2
    assert len(server.publications) == 2

    first_public = json.dumps(first.to_public_dict(), sort_keys=True)
    second_public = json.dumps(second.to_public_dict(), sort_keys=True)
    for public in (first_public, second_public):
        assert ".@" not in public
        assert "runtime" not in public
        assert "kept private" not in public
