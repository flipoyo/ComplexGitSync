from dataclasses import fields

import pytest

from CGS import CGSContractError, StateOntology


def test_state_ontology_is_static_prime_graph_projection(graph) -> None:
    ontology = StateOntology.from_graph(graph)

    assert tuple(field.name for field in fields(ontology)) == ("name", "node", "edge", "op")
    assert ontology.to_dict() == graph.to_dict()
    assert "state_id" not in ontology.to_json()
    assert ".@" not in ontology.to_json()


def test_state_ontology_direct_construction_enforces_public_graph_policy() -> None:
    with pytest.raises(CGSContractError):
        StateOntology("Demo", {"nested": {"runtime": "hidden"}}, [], "sync")
