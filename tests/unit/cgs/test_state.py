import inspect

import pytest

import CGS
from CGS import CGS as Kernel
from CGS import CandidateState, OwnershipError, State, StateId


def test_candidate_is_not_authoritative(candidate) -> None:
    assert isinstance(candidate, CandidateState)
    assert not isinstance(candidate, State)
    assert not hasattr(candidate, "state_id")


@pytest.mark.parametrize("value", ("0" * 64, object(), None))
def test_state_id_public_construction_always_fails(value) -> None:
    with pytest.raises(OwnershipError):
        StateId(value)  # type: ignore[arg-type]


def test_state_public_construction_always_fails(candidate) -> None:
    with pytest.raises(OwnershipError):
        State(
            "Demo",
            "0" * 64,
            "1" * 64,
            "2" * 64,
            candidate.left,
            candidate.right,
            candidate.payload,
        )


def test_cgs_returns_bound_validated_authoritative_state(
    graph, candidate, memory_system, server_gateway
) -> None:
    result = Kernel.serve("Demo", graph, memory_system, candidate, server_gateway)

    assert result.ok
    state = result.living_graph.state
    assert type(state) is State
    assert type(state.state_id) is StateId
    assert state.validated is True
    assert state.graph_binding == graph.digest
    assert state.gateway_binding == result.living_graph.gateway.binding
    assert len(state.state_digest) == 64


def test_no_authority_capability_is_exported_or_accepted() -> None:
    assert not hasattr(CGS, "CGS_AUTHORITY")
    assert "_authority" not in inspect.signature(State).parameters
    assert "_authority" not in inspect.signature(StateId).parameters


@pytest.mark.parametrize("primitive", (State, StateId))
def test_authoritative_primitives_reject_subclass_construction(primitive) -> None:
    with pytest.raises(TypeError, match="sealed"):
        type("ForgedPrimitive", (primitive,), {})
