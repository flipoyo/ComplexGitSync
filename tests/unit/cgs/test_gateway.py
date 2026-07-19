import pytest

from CGS import CandidateState, ErrorCode, Gateway, GatewayStage


def test_gateway_runs_explicit_pipeline_without_direct_crossing(graph, candidate) -> None:
    gateway = Gateway(graph)

    listened = gateway.listen(candidate)
    interpreted = gateway.interpret(listened)
    validated = gateway.validate(interpreted)

    assert listened.stage == GatewayStage.LISTENED
    assert interpreted.stage == GatewayStage.INTERPRETED
    assert validated.stage == GatewayStage.VALIDATED
    assert validated.ok


def test_gateway_rejects_wrong_stage_and_differing_interpretations(graph) -> None:
    gateway = Gateway(graph)
    wrong_stage = gateway.interpret(gateway.listen("not-state"))  # type: ignore[arg-type]
    assert not wrong_stage.ok
    assert wrong_stage.error.code == ErrorCode.INVALID_PIPELINE_STAGE

    candidate = CandidateState("Demo", {"value": 1}, {"value": 2}, {})
    invalid = gateway.validate(gateway.interpret(gateway.listen(candidate)))
    assert invalid.error.code == ErrorCode.VALIDATION_FAILED
    with pytest.raises(ValueError):
        gateway.emit_state_ontology(invalid)
