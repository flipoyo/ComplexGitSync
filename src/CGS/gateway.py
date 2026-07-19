from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
from typing import Any

from .errors import CGSContractError, CGSError, ErrorCode
from .graph import Graph
from .living_graph import LivingGraph, _new_living_graph
from .serialization import canonical_json
from .state import CandidateState, State, _state_digest
from .state_core_graph import StateCoreGraph
from .state_ontology import StateOntology


class GatewayStage(str, Enum):
    LISTENED = "listened"
    INTERPRETED = "interpreted"
    VALIDATED = "validated"
    TRANSFERRED = "transferred"


@dataclass(frozen=True, slots=True)
class GatewayResult:
    stage: GatewayStage | None
    value: Any | None = None
    error: CGSError | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


@dataclass(frozen=True, slots=True)
class _InterpretedState:
    candidate: CandidateState
    candidate_digest: str
    graph_binding: str
    gateway_binding: str
    left_json: str
    right_json: str


@dataclass(frozen=True, slots=True)
class ValidatedCandidate:
    candidate: CandidateState
    candidate_digest: str
    graph_binding: str
    gateway_binding: str


@dataclass(frozen=True, slots=True, init=False)
class Gateway:
    graph: Graph
    binding: str

    def __init__(self, graph: Graph) -> None:
        canonical_graph = graph.detached_copy()
        binding = hashlib.sha256(
            b"CGS-GATEWAY-v1\x00" + canonical_graph.to_json().encode("utf-8")
        ).hexdigest()
        object.__setattr__(self, "graph", canonical_graph)
        object.__setattr__(self, "binding", binding)

    @staticmethod
    def _stage_error(stage: str, message: str) -> GatewayResult:
        return GatewayResult(
            stage=None,
            error=CGSError(ErrorCode.INVALID_PIPELINE_STAGE, message, stage),
        )

    def listen(self, candidate: CandidateState) -> GatewayResult:
        if type(candidate) is not CandidateState:
            return GatewayResult(
                stage=None,
                error=CGSError(
                    ErrorCode.INVALID_CANDIDATE,
                    "Gateway accepts CandidateState input only",
                    "listen",
                ),
            )
        try:
            detached = candidate.detached_copy()
        except (TypeError, ValueError):
            return GatewayResult(
                stage=None,
                error=CGSError(ErrorCode.INVALID_CANDIDATE, "invalid CandidateState", "listen"),
            )
        return GatewayResult(stage=GatewayStage.LISTENED, value=detached)

    def interpret(self, listened: GatewayResult) -> GatewayResult:
        if (
            type(listened) is not GatewayResult
            or not listened.ok
            or listened.stage != GatewayStage.LISTENED
            or type(listened.value) is not CandidateState
        ):
            return self._stage_error("interpret", "listen must succeed before interpret")
        candidate = listened.value.detached_copy()
        digest = hashlib.sha256(
            b"CGS-CANDIDATE-v1\x00" + canonical_json(candidate.to_dict()).encode("utf-8")
        ).hexdigest()
        interpreted = _InterpretedState(
            candidate=candidate,
            candidate_digest=digest,
            graph_binding=self.graph.digest,
            gateway_binding=self.binding,
            left_json=canonical_json(candidate.left),
            right_json=canonical_json(candidate.right),
        )
        return GatewayResult(stage=GatewayStage.INTERPRETED, value=interpreted)

    def validate(self, interpreted: GatewayResult) -> GatewayResult:
        if (
            type(interpreted) is not GatewayResult
            or not interpreted.ok
            or interpreted.stage != GatewayStage.INTERPRETED
            or type(interpreted.value) is not _InterpretedState
        ):
            return self._stage_error("validate", "interpret must succeed before validate")
        value = interpreted.value
        candidate = value.candidate.detached_copy()
        expected_digest = hashlib.sha256(
            b"CGS-CANDIDATE-v1\x00" + canonical_json(candidate.to_dict()).encode("utf-8")
        ).hexdigest()
        if (
            value.candidate_digest != expected_digest
            or value.graph_binding != self.graph.digest
            or value.gateway_binding != self.binding
        ):
            return self._stage_error("validate", "interpreted binding is invalid")
        if not candidate.complete or not candidate.graph_name:
            return GatewayResult(
                stage=None,
                error=CGSError(ErrorCode.PARTIAL_STATE, "candidate State is partial", "validate"),
            )
        if candidate.graph_name != self.graph.name:
            return GatewayResult(
                stage=None,
                error=CGSError(
                    ErrorCode.GRAPH_NAME_MISMATCH,
                    "candidate graph_name does not match Gateway Graph",
                    "validate",
                ),
            )
        if value.left_json != value.right_json:
            return GatewayResult(
                stage=None,
                error=CGSError(
                    ErrorCode.VALIDATION_FAILED,
                    "LEFT and RIGHT interpretations differ",
                    "validate",
                ),
            )
        return GatewayResult(
            stage=GatewayStage.VALIDATED,
            value=ValidatedCandidate(
                candidate=candidate,
                candidate_digest=expected_digest,
                graph_binding=self.graph.digest,
                gateway_binding=self.binding,
            ),
        )

    def transfer(self, validated: GatewayResult, state: State) -> GatewayResult:
        if (
            type(validated) is not GatewayResult
            or not validated.ok
            or validated.stage != GatewayStage.VALIDATED
            or type(validated.value) is not ValidatedCandidate
        ):
            return self._stage_error("transfer", "validate must succeed before transfer")
        receipt = validated.value
        candidate = receipt.candidate.detached_copy()
        candidate_digest = hashlib.sha256(
            b"CGS-CANDIDATE-v1\x00" + canonical_json(candidate.to_dict()).encode("utf-8")
        ).hexdigest()
        if (
            type(state) is not State
            or receipt.candidate_digest != candidate_digest
            or receipt.graph_binding != self.graph.digest
            or receipt.gateway_binding != self.binding
            or not candidate.complete
            or candidate.graph_name != self.graph.name
            or canonical_json(candidate.left) != canonical_json(candidate.right)
            or state.graph_binding != self.graph.digest
            or state.gateway_binding != self.binding
            or _state_digest(state.to_memory_data()) != state.state_digest
            or not state.validated
            or state.graph_name != self.graph.name
            or state.left != candidate.left
            or state.right != candidate.right
            or state.payload != candidate.payload
        ):
            return GatewayResult(
                stage=None,
                error=CGSError(
                    ErrorCode.INVALID_AUTHORITATIVE_STATE,
                    "authoritative State does not match the validated candidate",
                    "transfer",
                ),
            )
        living = _new_living_graph(self.graph, self, state)
        return GatewayResult(stage=GatewayStage.TRANSFERRED, value=living)

    def emit_state_ontology(self, transferred: GatewayResult) -> StateOntology:
        living = self._require_transferred(transferred)
        return StateOntology.from_graph(living.graph)

    def emit_state_core_graph(self, transferred: GatewayResult) -> StateCoreGraph:
        living = self._require_transferred(transferred)
        return StateCoreGraph.for_graph(living.graph.name)

    @staticmethod
    def _require_transferred(transferred: GatewayResult) -> LivingGraph:
        if (
            type(transferred) is not GatewayResult
            or not transferred.ok
            or transferred.stage != GatewayStage.TRANSFERRED
            or type(transferred.value) is not LivingGraph
            or type(transferred.value.state) is not State
        ):
            raise CGSContractError(
                ErrorCode.EMISSION_REJECTED,
                "only a transferred authoritative State may be emitted",
            )
        return transferred.value
