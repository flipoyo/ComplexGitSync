from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from ._authority import require_cgs_authority
from .errors import CGSContractError, CGSError, ErrorCode
from .graph import Graph
from .living_graph import LivingGraph
from .serialization import canonical_json
from .state import CandidateState, State
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
    left_json: str
    right_json: str


@dataclass(frozen=True, slots=True)
class ValidatedCandidate:
    candidate: CandidateState
    validation_digest_material: str


class Gateway:
    __slots__ = ("graph",)

    def __init__(self, graph: Graph) -> None:
        self.graph = graph

    @staticmethod
    def _stage_error(stage: str, message: str) -> GatewayResult:
        return GatewayResult(
            stage=None,
            error=CGSError(ErrorCode.INVALID_PIPELINE_STAGE, message, stage),
        )

    def listen(self, candidate: CandidateState) -> GatewayResult:
        if not isinstance(candidate, CandidateState):
            return GatewayResult(
                stage=None,
                error=CGSError(
                    ErrorCode.INVALID_CANDIDATE,
                    "Gateway accepts CandidateState input only",
                    "listen",
                ),
            )
        return GatewayResult(stage=GatewayStage.LISTENED, value=candidate)

    def interpret(self, listened: GatewayResult) -> GatewayResult:
        if not listened.ok or listened.stage != GatewayStage.LISTENED:
            return self._stage_error("interpret", "listen must succeed before interpret")
        candidate = listened.value
        if not isinstance(candidate, CandidateState):
            return self._stage_error("interpret", "listened value is not a CandidateState")
        interpreted = _InterpretedState(
            candidate=candidate,
            left_json=canonical_json(candidate.left),
            right_json=canonical_json(candidate.right),
        )
        return GatewayResult(stage=GatewayStage.INTERPRETED, value=interpreted)

    def validate(self, interpreted: GatewayResult) -> GatewayResult:
        if not interpreted.ok or interpreted.stage != GatewayStage.INTERPRETED:
            return self._stage_error("validate", "interpret must succeed before validate")
        value = interpreted.value
        if not isinstance(value, _InterpretedState):
            return self._stage_error("validate", "interpreted value has invalid type")
        candidate = value.candidate
        if not candidate.complete or not candidate.graph_name:
            return GatewayResult(
                stage=None,
                error=CGSError(
                    ErrorCode.PARTIAL_STATE,
                    "candidate State is partial",
                    "validate",
                ),
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
        validated = ValidatedCandidate(
            candidate=candidate,
            validation_digest_material=canonical_json(candidate.to_dict()),
        )
        return GatewayResult(stage=GatewayStage.VALIDATED, value=validated)

    def transfer(
        self,
        validated: GatewayResult,
        state: State,
        *,
        _authority: object,
    ) -> GatewayResult:
        require_cgs_authority(_authority)
        if not validated.ok or validated.stage != GatewayStage.VALIDATED:
            return self._stage_error("transfer", "validate must succeed before transfer")
        receipt = validated.value
        if not isinstance(receipt, ValidatedCandidate):
            return self._stage_error("transfer", "validation receipt has invalid type")
        candidate = receipt.candidate
        if (
            not state.validated
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
        living = LivingGraph._with_state(self.graph, self, state, _authority=_authority)
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
            not transferred.ok
            or transferred.stage != GatewayStage.TRANSFERRED
            or not isinstance(transferred.value, LivingGraph)
            or transferred.value.state is None
        ):
            raise CGSContractError(
                ErrorCode.EMISSION_REJECTED,
                "only a transferred authoritative State may be emitted",
            )
        return transferred.value
