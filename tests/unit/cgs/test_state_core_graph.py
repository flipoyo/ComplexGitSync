import pytest

from CGS import CGSContractError, ErrorCode, StateCoreGraph


def test_state_core_graph_is_distinct_safe_living_gateway_projection() -> None:
    projection = StateCoreGraph.for_graph("Demo")

    assert ".PUBLIC" in projection.mermaid
    assert "Gateway boundary" in projection.mermaid
    assert "*Demo" in projection.mermaid
    assert "STATE@" in projection.mermaid
    assert "LEFT" in projection.mermaid
    assert "RIGHT" in projection.mermaid
    for forbidden in (".@", "credential", "kept private", "raw execution memory"):
        assert forbidden not in projection.to_json()


@pytest.mark.parametrize(
    "hostile_name",
    (
        'Demo"] --> INJECT["private',
        "Demo\nflowchart TD",
        "Demo; PUBLIC --> PRIVATE",
        "Demo.@anchor",
    ),
)
def test_state_core_graph_rejects_graph_name_injection(hostile_name: str) -> None:
    with pytest.raises(CGSContractError) as raised:
        StateCoreGraph.for_graph(hostile_name)

    assert raised.value.error.code == ErrorCode.INVALID_GRAPH_NAME
    assert hostile_name not in raised.value.error.message


def test_state_core_graph_rejects_noncanonical_direct_mermaid_content() -> None:
    with pytest.raises(CGSContractError) as raised:
        StateCoreGraph("Demo", 'flowchart LR\nPUBLIC --> INJECT["private"]')

    assert raised.value.error.code == ErrorCode.EMISSION_REJECTED
