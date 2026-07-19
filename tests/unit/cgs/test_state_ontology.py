from dataclasses import fields

from CGS import StateOntology


def test_state_ontology_is_static_prime_graph_projection(graph) -> None:
    ontology = StateOntology.from_graph(graph)

    assert tuple(field.name for field in fields(ontology)) == ("name", "node", "edge", "op")
    assert ontology.to_dict() == graph.to_dict()
    assert "state_id" not in ontology.to_json()
    assert ".@" not in ontology.to_json()
