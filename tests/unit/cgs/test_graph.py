from dataclasses import fields

import pytest

from CGS import CGSContractError, ErrorCode, Graph


def test_graph_is_exact_static_four_field_contract() -> None:
    graph = Graph("G", ["node"], ["edge"], "op")

    assert tuple(field.name for field in fields(graph)) == ("name", "node", "edge", "op")
    assert graph.to_dict() == {
        "name": "G",
        "node": ["node"],
        "edge": ["edge"],
        "op": "op",
    }
    assert not hasattr(graph, "state")


def test_graph_rejects_state_constructor_argument() -> None:
    with pytest.raises(TypeError):
        Graph("G", [], [], "op", state={})  # type: ignore[call-arg]


@pytest.mark.parametrize(
    "name",
    ("G", "@CGS", "@ComplexGitSync", "alpha-tech", "Graph.v1_test:local"),
)
def test_graph_name_accepts_canonical_language_neutral_identifiers(name: str) -> None:
    assert Graph(name, [], [], "op").name == name


@pytest.mark.parametrize(
    "name",
    (
        ".@",
        "G.@private",
        'G"] --> INJECT["owned',
        "G\nINJECT --> PUBLIC",
        "G; flowchart TD",
        "G{control}",
        "G<br/>",
        "G space",
    ),
)
def test_graph_name_rejects_reserved_or_mermaid_injection(name: str) -> None:
    with pytest.raises(CGSContractError) as raised:
        Graph(name, [], [], "op")

    assert raised.value.error.code == ErrorCode.INVALID_GRAPH_NAME
    assert name not in raised.value.error.message


@pytest.mark.parametrize(
    "private_value",
    (
        {"runtime": "private"},
        {"nested": [{"process-environment": {"safe": False}}]},
        {"nested": {"private_right": "hidden"}},
        {"nested": {"raw_memory": "hidden"}},
        {"nested": {"gateway_internals": "hidden"}},
        {"nested": "credential=hidden"},
        {"nested": "private runtime"},
        {"nested": "PATH=/private"},
        {"nested": "/env/PATH"},
        {"nested": "RIGHT=private"},
        {"nested": "raw process memory"},
        {"nested": ".@"},
    ),
)
def test_graph_recursively_rejects_private_public_values_without_echo(private_value) -> None:
    with pytest.raises(CGSContractError) as raised:
        Graph("Demo", private_value, [], "sync")

    assert raised.value.error.code == ErrorCode.INVALID_GRAPH
    message = raised.value.error.message.casefold()
    for fragment in ("hidden", "path=/private", ".@"):
        assert fragment not in message
