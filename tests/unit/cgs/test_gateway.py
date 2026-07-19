from dataclasses import replace

import pytest

from CGS import (
    CGS,
    CGSContractError,
    CandidateState,
    ErrorCode,
    Gateway,
    GatewayResult,
    GatewayStage,
    MemorySystem,
    ServerGateway,
)


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
    with pytest.raises(CGSContractError) as ontology_error:
        gateway.emit_state_ontology(invalid)
    with pytest.raises(CGSContractError) as core_error:
        gateway.emit_state_core_graph(invalid)

    assert ontology_error.value.error.code == ErrorCode.EMISSION_REJECTED
    assert core_error.value.error.code == ErrorCode.EMISSION_REJECTED


def test_transfer_recomputes_candidate_and_state_bindings(graph, candidate) -> None:
    gateway = Gateway(graph)
    validated = gateway.validate(gateway.interpret(gateway.listen(candidate)))
    served = CGS.serve("Demo", graph, MemorySystem(), candidate, ServerGateway())
    state = served.living_graph.state
    forged_receipt = replace(validated.value, candidate_digest="0" * 64)

    forged = gateway.transfer(GatewayResult(GatewayStage.VALIDATED, forged_receipt), state)
    assert forged.error.code == ErrorCode.INVALID_AUTHORITATIVE_STATE

    object.__setattr__(state, "state_digest", "0" * 64)
    mutated = gateway.transfer(validated, state)
    assert mutated.error.code == ErrorCode.INVALID_AUTHORITATIVE_STATE
