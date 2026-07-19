import json

import pytest

from CGS import (
    CGS,
    CandidateState,
    ErrorCode,
    Gateway,
    GatewayResult,
    GatewayStage,
    Graph,
    LivingGraph,
    MemoryRecord,
    MemoryResult,
    MemorySystem,
    ServerGateway,
    ServerPublication,
    State,
    StateId,
    StateOntology,
)
from CGS.gateway import ValidatedCandidate, _InterpretedState
from CGS.serialization import canonical_json, freeze_json


PRIVATE_TEXT = "credential=semantic-secret .@ RIGHT=private /env/PATH"


def unrelated_record() -> MemoryRecord:
    return MemoryRecord(
        graph_name="Unrelated",
        state_id="f" * 64,
        left=freeze_json({"credential": PRIVATE_TEXT}),
        right=freeze_json({"credential": PRIVATE_TEXT}),
        payload=freeze_json({"private": PRIVATE_TEXT}),
        validated=True,
        state_digest="0" * 64,
    )


def assert_semantic_rejection(result, memory, server, code) -> None:
    assert not result.ok
    assert result.error.code == code
    assert result.error.message == "CGS rejected the operation"
    assert result.living_graph is None
    assert result.state_ontology is None
    assert result.state_core_graph is None
    assert memory.records == ()
    assert server.publications == ()
    public = json.dumps(result.to_public_dict(), sort_keys=True)
    for fragment in (
        "credential",
        "semantic-secret",
        ".@",
        "RIGHT",
        "private",
        "/env",
        "PATH",
    ):
        assert fragment not in public


class LieInterpret(ServerGateway):
    def interpret(self, gateway, listened):
        candidate = listened.value
        left = canonical_json(candidate.left)
        forged = _InterpretedState(candidate=candidate, left_json=left, right_json=left)
        return GatewayResult(stage=GatewayStage.INTERPRETED, value=forged)


class LieValidate(ServerGateway):
    def validate(self, gateway, interpreted):
        candidate = interpreted.value.candidate
        forged = ValidatedCandidate(
            candidate=candidate,
            validation_digest_material=canonical_json(candidate.to_dict()),
        )
        return GatewayResult(stage=GatewayStage.VALIDATED, value=forged)


@pytest.mark.parametrize("server_type", (LieInterpret, LieValidate))
def test_cgs_decisive_validation_rejects_semantic_gateway_lies(graph, server_type) -> None:
    candidate = CandidateState("Demo", {"tree": 1}, {"tree": 2}, {})
    memory = MemorySystem()
    server = server_type()

    result = CGS.serve("Demo", graph, memory, candidate, server)

    assert_semantic_rejection(result, memory, server, ErrorCode.INVALID_PIPELINE_STAGE)


class AlterTransfer(ServerGateway):
    def __init__(self, mode: str) -> None:
        super().__init__()
        self.mode = mode

    def transfer(self, gateway, validated, state, *, _authority):
        altered_graph = gateway.graph
        altered_gateway = gateway
        altered_state = state
        if self.mode == "graph":
            altered_graph = Graph(
                gateway.graph.name,
                {"credential": PRIVATE_TEXT},
                gateway.graph.edge,
                gateway.graph.op,
            )
        elif self.mode == "gateway":
            altered_gateway = Gateway(gateway.graph)
        else:
            altered_id = state.state_id
            left = state.left
            right = state.right
            payload = state.payload
            if self.mode == "state_id":
                altered_id = StateId("e" * 64, _authority=_authority)
            elif self.mode == "left":
                left = freeze_json({"credential": PRIVATE_TEXT})
            elif self.mode == "right":
                right = freeze_json({"private": PRIVATE_TEXT})
            elif self.mode == "payload":
                payload = freeze_json({"credential": PRIVATE_TEXT})
            altered_state = State(
                state.graph_name,
                altered_id,
                left,
                right,
                payload,
                validated=True,
                _authority=_authority,
            )
        living = LivingGraph._with_state(
            altered_graph,
            altered_gateway,
            altered_state,
            _authority=_authority,
        )
        return GatewayResult(stage=GatewayStage.TRANSFERRED, value=living)


@pytest.mark.parametrize("mode", ("graph", "gateway", "state_id", "left", "right", "payload"))
def test_transfer_semantics_authenticate_graph_gateway_and_state(graph, candidate, mode) -> None:
    memory = MemorySystem()
    server = AlterTransfer(mode)

    result = CGS.serve("Demo", graph, memory, candidate, server)

    assert_semantic_rejection(result, memory, server, ErrorCode.INVALID_PIPELINE_STAGE)


class AlterOntology(ServerGateway):
    def __init__(self, field: str) -> None:
        super().__init__()
        self.field = field

    def emit_state_ontology(self, gateway, transferred):
        canonical = super().emit_state_ontology(gateway, transferred)
        values = {
            "name": canonical.name,
            "node": canonical.node,
            "edge": canonical.edge,
            "op": canonical.op,
        }
        values[self.field] = freeze_json({"credential": PRIVATE_TEXT})
        return StateOntology(**values)


@pytest.mark.parametrize("field", ("node", "edge", "op"))
def test_canonical_ontology_rejects_private_field_substitution(graph, candidate, field) -> None:
    memory = MemorySystem()
    server = AlterOntology(field)

    result = CGS.serve("Demo", graph, memory, candidate, server)

    assert_semantic_rejection(result, memory, server, ErrorCode.EMISSION_REJECTED)


class AlterPublication(ServerGateway):
    def __init__(self, mode: str) -> None:
        super().__init__()
        self.mode = mode

    def prepare_publication(self, living_graph, state_ontology, state_core_graph):
        if self.mode == "projection":
            state_ontology = StateOntology(
                name=state_ontology.name,
                node=freeze_json({"credential": PRIVATE_TEXT}),
                edge=state_ontology.edge,
                op=state_ontology.op,
            )
        operations = ("state", "state-core", "validated-operation")
        if self.mode == "operations":
            operations = operations + (PRIVATE_TEXT,)
        return ServerPublication(state_ontology, state_core_graph, operations)


@pytest.mark.parametrize("mode", ("projection", "operations"))
def test_publication_must_equal_canonical_complete_value(graph, candidate, mode) -> None:
    memory = MemorySystem()
    server = AlterPublication(mode)

    result = CGS.serve("Demo", graph, memory, candidate, server)

    assert_semantic_rejection(result, memory, server, ErrorCode.SERVER_REJECTED)


class FalsePrepareMemory(MemorySystem):
    def __init__(self, mode: str) -> None:
        super().__init__()
        self.mode = mode

    def prepare(self, state):
        if self.mode == "empty":
            return MemoryResult()
        return MemoryResult(record=unrelated_record())


@pytest.mark.parametrize("mode", ("empty", "unrelated"))
def test_memory_prepare_must_equal_canonical_record(graph, candidate, mode) -> None:
    memory = FalsePrepareMemory(mode)
    server = ServerGateway()

    result = CGS.serve("Demo", graph, memory, candidate, server)

    assert_semantic_rejection(result, memory, server, ErrorCode.MEMORY_REJECTED)


class FalseResultPersist(MemorySystem):
    def __init__(self, mode: str) -> None:
        super().__init__()
        self.mode = mode

    def persist(self, state, *, _authority=None):
        if self.mode == "empty":
            return MemoryResult()
        return MemoryResult(record=unrelated_record())


@pytest.mark.parametrize("mode", ("empty", "unrelated"))
def test_memory_persist_result_must_equal_canonical_record(graph, candidate, mode) -> None:
    memory = FalseResultPersist(mode)
    server = ServerGateway()

    result = CGS.serve("Demo", graph, memory, candidate, server)

    assert_semantic_rejection(result, memory, server, ErrorCode.SERVICE_COMMIT_FAILED)


class FalsePersist(MemorySystem):
    def persist(self, state, *, _authority=None):
        return MemorySystem.prepare(self, state)


class ExtraPersist(MemorySystem):
    def persist(self, state, *, _authority=None):
        result = MemorySystem.persist(self, state, _authority=_authority)
        self._records = self._records + (unrelated_record(),)
        return result


@pytest.mark.parametrize("memory_type", (FalsePersist, ExtraPersist))
def test_memory_persistence_postcondition_rejects_missing_or_extra_record(
    graph, candidate, memory_type
) -> None:
    memory = memory_type()
    server = ServerGateway()

    result = CGS.serve("Demo", graph, memory, candidate, server)

    assert_semantic_rejection(result, memory, server, ErrorCode.SERVICE_COMMIT_FAILED)


class SilentPublish(ServerGateway):
    def publish(self, publication, *, _authority=None):
        return None


class ExtraPublish(ServerGateway):
    def publish(self, publication, *, _authority=None):
        ServerGateway.publish(self, publication, _authority=_authority)
        altered = ServerPublication(
            publication.state_ontology,
            publication.state_core_graph,
            publication.operations + (PRIVATE_TEXT,),
        )
        self._publications = self._publications + (altered,)
        return None


@pytest.mark.parametrize("server_type", (SilentPublish, ExtraPublish))
def test_publication_postcondition_rejects_silent_or_extra_write(
    graph, candidate, server_type
) -> None:
    memory = MemorySystem()
    server = server_type()

    result = CGS.serve("Demo", graph, memory, candidate, server)

    assert_semantic_rejection(result, memory, server, ErrorCode.SERVICE_COMMIT_FAILED)
