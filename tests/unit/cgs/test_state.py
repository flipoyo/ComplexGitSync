import pytest

from CGS import CGS, CandidateState, OwnershipError, State, StateId


def test_candidate_is_not_authoritative(candidate) -> None:
    assert isinstance(candidate, CandidateState)
    assert not isinstance(candidate, State)
    assert not hasattr(candidate, "state_id")


def test_authoritative_state_cannot_be_constructed_outside_cgs(candidate) -> None:
    with pytest.raises(OwnershipError):
        StateId("0" * 64)

    with pytest.raises(OwnershipError):
        State(
            "Demo",
            object(),  # type: ignore[arg-type]
            candidate.left,
            candidate.right,
            candidate.payload,
            validated=True,
        )


def test_cgs_returns_validated_authoritative_state(
    graph, candidate, memory_system, server_gateway
) -> None:
    result = CGS.serve("Demo", graph, memory_system, candidate, server_gateway)
    state = result.living_graph.state
    assert isinstance(state, State)
    assert isinstance(state.state_id, StateId)
    assert state.validated is True
