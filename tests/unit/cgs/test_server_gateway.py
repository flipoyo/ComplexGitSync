import json

import pytest

from CGS import (
    CGS,
    CandidateState,
    ErrorCode,
    Gateway,
    GatewayResult,
    GatewayStage,
    LivingGraph,
    MemorySystem,
    ServerGateway,
)


MALFORMED_SECRET = "credential=hidden .@ RIGHT=private /env/PATH"


class SecretMalformed:
    def __repr__(self) -> str:
        return MALFORMED_SECRET


class MalformedEarlyServer(ServerGateway):
    def __init__(self, boundary: str, mode: str) -> None:
        super().__init__()
        self.boundary = boundary
        self.mode = mode

    def _result(self, expected_stage, wrong_stage, valid_value):
        if self.mode == "none":
            return None
        if self.mode == "object":
            return SecretMalformed()
        if self.mode == "wrong_stage":
            return GatewayResult(stage=wrong_stage, value=valid_value)
        return GatewayResult(stage=expected_stage, value=SecretMalformed())

    def listen(self, gateway, candidate):
        if self.boundary == "listen":
            return self._result(GatewayStage.LISTENED, GatewayStage.INTERPRETED, candidate)
        return super().listen(gateway, candidate)

    def interpret(self, gateway, listened):
        if self.boundary == "interpret":
            return self._result(GatewayStage.INTERPRETED, GatewayStage.LISTENED, listened.value)
        return super().interpret(gateway, listened)


class MalformedValidateServer(ServerGateway):
    def __init__(self, mode: str) -> None:
        super().__init__()
        self.mode = mode

    def validate(self, gateway, interpreted):
        if self.mode == "none":
            return None
        if self.mode == "object":
            return SecretMalformed()
        if self.mode == "wrong_stage":
            return interpreted
        return GatewayResult(stage=GatewayStage.VALIDATED, value=SecretMalformed())


class MalformedTransferServer(ServerGateway):
    def __init__(self, mode: str) -> None:
        super().__init__()
        self.mode = mode

    def transfer(self, gateway, validated, state, *, _authority):
        if self.mode == "none":
            return None
        if self.mode == "object":
            return SecretMalformed()
        if self.mode == "wrong_stage":
            return validated
        if self.mode == "empty_living":
            return GatewayResult(
                stage=GatewayStage.TRANSFERRED,
                value=LivingGraph(gateway.graph, Gateway(gateway.graph)),
            )
        return GatewayResult(stage=GatewayStage.TRANSFERRED, value=SecretMalformed())


class MalformedEmissionServer(ServerGateway):
    def __init__(self, target: str, mode: str) -> None:
        super().__init__()
        self.target = target
        self.mode = mode

    def _malformed(self):
        return None if self.mode == "none" else SecretMalformed()

    def emit_state_ontology(self, gateway, transferred):
        if self.target == "ontology":
            return self._malformed()
        return super().emit_state_ontology(gateway, transferred)

    def emit_state_core_graph(self, gateway, transferred):
        if self.target == "core":
            return self._malformed()
        return super().emit_state_core_graph(gateway, transferred)


class TracedMemorySystem(MemorySystem):
    def __init__(self, calls: list[str]) -> None:
        super().__init__()
        self.calls = calls

    def persist(self, state, *, _authority=None):
        self.calls.append("memory.persist")
        return super().persist(state, _authority=_authority)


class TracedServerGateway(ServerGateway):
    def __init__(self, calls: list[str] | None = None) -> None:
        super().__init__()
        self.calls = calls if calls is not None else []

    def listen(self, gateway, candidate):
        self.calls.append("listen")
        return super().listen(gateway, candidate)

    def interpret(self, gateway, listened):
        self.calls.append("interpret")
        return super().interpret(gateway, listened)

    def validate(self, gateway, interpreted):
        self.calls.append("validate")
        return super().validate(gateway, interpreted)

    def transfer(self, gateway, validated, state, *, _authority):
        self.calls.append("transfer")
        return super().transfer(gateway, validated, state, _authority=_authority)

    def emit_state_ontology(self, gateway, transferred):
        self.calls.append("emit_state_ontology")
        return super().emit_state_ontology(gateway, transferred)

    def emit_state_core_graph(self, gateway, transferred):
        self.calls.append("emit_state_core_graph")
        return super().emit_state_core_graph(gateway, transferred)

    def prepare_publication(self, living_graph, state_ontology, state_core_graph):
        self.calls.append("prepare_publication")
        return super().prepare_publication(living_graph, state_ontology, state_core_graph)

    def publish(self, publication, *, _authority=None):
        self.calls.append("publish")
        return super().publish(publication, _authority=_authority)


def test_physical_server_routes_full_gateway_pipeline_exactly_once(graph, candidate) -> None:
    calls: list[str] = []
    memory_system = TracedMemorySystem(calls)
    server = TracedServerGateway(calls)

    result = CGS.serve("Demo", graph, memory_system, candidate, server)

    assert result.ok
    assert calls == [
        "listen",
        "interpret",
        "validate",
        "transfer",
        "emit_state_ontology",
        "emit_state_core_graph",
        "prepare_publication",
        "memory.persist",
        "publish",
    ]
    assert len(server.publications) == 1
    assert server.publications[0].operations == (
        "state",
        "state-core",
        "validated-operation",
    )


def test_invalid_input_is_never_served(graph, memory_system) -> None:
    server = TracedServerGateway()
    invalid = CandidateState("Demo", 1, 2, {})

    result = CGS.serve("Demo", graph, memory_system, invalid, server)

    assert not result.ok
    assert result.error.code == ErrorCode.VALIDATION_FAILED
    assert server.calls == ["listen", "interpret", "validate"]
    assert server.publications == ()


def assert_malformed_boundary_is_atomic(result, memory, server, expected_code) -> None:
    assert not result.ok
    assert result.error.code == expected_code
    assert result.error.message == "CGS rejected the operation"
    assert result.living_graph is None
    assert result.state_ontology is None
    assert result.state_core_graph is None
    assert memory.records == ()
    assert server.publications == ()
    public = json.dumps(result.to_public_dict(), sort_keys=True)
    for fragment in ("credential", "hidden", ".@", "private", "/env", "PATH"):
        assert fragment not in public


@pytest.mark.parametrize("boundary", ("listen", "interpret"))
@pytest.mark.parametrize("mode", ("none", "object", "wrong_stage", "wrong_value"))
def test_malformed_early_gateway_result_is_typed_redacted_and_atomic(
    graph, candidate, boundary, mode
) -> None:
    memory = MemorySystem()
    server = MalformedEarlyServer(boundary, mode)

    result = CGS.serve("Demo", graph, memory, candidate, server)

    assert_malformed_boundary_is_atomic(result, memory, server, ErrorCode.INVALID_PIPELINE_STAGE)


@pytest.mark.parametrize("mode", ("none", "object", "wrong_stage", "wrong_value"))
def test_malformed_validate_result_is_typed_redacted_and_atomic(graph, candidate, mode) -> None:
    memory = MemorySystem()
    server = MalformedValidateServer(mode)

    result = CGS.serve("Demo", graph, memory, candidate, server)

    assert_malformed_boundary_is_atomic(result, memory, server, ErrorCode.INVALID_PIPELINE_STAGE)


@pytest.mark.parametrize("mode", ("none", "object", "wrong_stage", "wrong_value", "empty_living"))
def test_malformed_transfer_result_is_typed_redacted_and_atomic(graph, candidate, mode) -> None:
    memory = MemorySystem()
    server = MalformedTransferServer(mode)

    result = CGS.serve("Demo", graph, memory, candidate, server)

    assert_malformed_boundary_is_atomic(result, memory, server, ErrorCode.INVALID_PIPELINE_STAGE)


@pytest.mark.parametrize(
    ("target", "mode"),
    (("ontology", "none"), ("ontology", "object"), ("core", "none"), ("core", "object")),
)
def test_malformed_emission_result_is_typed_redacted_and_atomic(
    graph, candidate, target, mode
) -> None:
    memory = MemorySystem()
    server = MalformedEmissionServer(target, mode)

    result = CGS.serve("Demo", graph, memory, candidate, server)

    assert_malformed_boundary_is_atomic(result, memory, server, ErrorCode.EMISSION_REJECTED)
