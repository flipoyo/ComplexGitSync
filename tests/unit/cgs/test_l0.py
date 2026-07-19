import json

import pytest

from CGS import CGS, L0, MemorySystem, OwnershipError, ServerGateway


def test_l0_ownership_is_exclusive_to_cgs() -> None:
    with pytest.raises(OwnershipError):
        L0()


def test_state_identity_is_deterministic_and_private_anchor_is_not_serialized(
    graph, candidate
) -> None:
    first = CGS.serve("Demo", graph, MemorySystem(), candidate, ServerGateway())
    second = CGS.serve("Demo", graph, MemorySystem(), candidate, ServerGateway())

    assert first.living_graph.state.state_id == second.living_graph.state.state_id
    public = json.dumps(first.to_public_dict(), sort_keys=True)
    assert ".@" not in public
    assert "runtime" not in public
    assert "kept private" not in public
