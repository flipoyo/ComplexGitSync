from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from ._authority import CGS_AUTHORITY
from .errors import CGSError, ErrorCode
from .gateway import Gateway, GatewayResult, ValidatedCandidate
from .graph import Graph
from .L0 import L0
from .living_graph import LivingGraph
from .memory_system import MemorySystem
from .server_gateway import ServerGateway
from .state import CandidateState, State
from .state_core_graph import StateCoreGraph
from .state_ontology import StateOntology


class CandidateOperator(Protocol):
    def candidate_state(self, graph: Graph) -> CandidateState: ...


class ServiceStatus(str, Enum):
    SUCCESS = "success"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class ServiceResult:
    status: ServiceStatus
    living_graph: LivingGraph | None = None
    state_ontology: StateOntology | None = None
    state_core_graph: StateCoreGraph | None = None
    error: CGSError | None = None

    @property
    def ok(self) -> bool:
        return self.status == ServiceStatus.SUCCESS and self.error is None

    def to_public_dict(self) -> dict[str, object]:
        if not self.ok:
            return {
                "status": self.status.value,
                "error": self.error.to_dict() if self.error else None,
            }
        assert self.living_graph is not None
        assert self.living_graph.state is not None
        assert self.state_ontology is not None
        assert self.state_core_graph is not None
        return {
            "status": self.status.value,
            "state": self.living_graph.state.to_public_dict(),
            "state_ontology": self.state_ontology.to_dict(),
            "state_core_graph": self.state_core_graph.to_dict(),
        }


class CGS:
    """Canonical @CGS facade and sole authoritative State creator."""

    @classmethod
    def serve(
        cls,
        graph_name: str,
        graph: Graph,
        memory_system: MemorySystem,
        operator: CandidateState | CandidateOperator,
        server_gateway: ServerGateway,
    ) -> ServiceResult:
        if not isinstance(graph, Graph):
            return cls._failure(CGSError(ErrorCode.INVALID_GRAPH, "invalid static Graph", "serve"))
        if graph_name != graph.name:
            return cls._failure(
                CGSError(
                    ErrorCode.GRAPH_NAME_MISMATCH,
                    "graph_name does not match Graph.name",
                    "serve",
                )
            )
        if not isinstance(memory_system, MemorySystem):
            return cls._failure(
                CGSError(ErrorCode.MEMORY_REJECTED, "invalid MemorySystem", "serve")
            )
        if not isinstance(server_gateway, ServerGateway):
            return cls._failure(
                CGSError(ErrorCode.SERVER_REJECTED, "invalid ServerGateway", "serve")
            )

        candidate = cls._candidate(operator, graph)
        if isinstance(candidate, CGSError):
            return cls._failure(candidate)

        gateway = Gateway(graph)
        listened = server_gateway.listen(gateway, candidate)
        interpreted = server_gateway.interpret(gateway, listened)
        validated = server_gateway.validate(gateway, interpreted)
        if not validated.ok:
            return cls._failure(cls._pipeline_error(validated, "validate"))

        receipt = validated.value
        if not isinstance(receipt, ValidatedCandidate):
            return cls._failure(
                CGSError(
                    ErrorCode.INVALID_PIPELINE_STAGE,
                    "Gateway returned no validation receipt",
                    "validate",
                )
            )

        l0 = L0(_authority=CGS_AUTHORITY)
        _private_anchor, state_id = l0._anchor(candidate, _authority=CGS_AUTHORITY)
        state = State(
            graph_name=graph.name,
            state_id=state_id,
            left=candidate.left,
            right=candidate.right,
            payload=candidate.payload,
            validated=True,
            _authority=CGS_AUTHORITY,
        )
        transferred = server_gateway.transfer(gateway, validated, state, _authority=CGS_AUTHORITY)
        if not transferred.ok or not isinstance(transferred.value, LivingGraph):
            return cls._failure(cls._pipeline_error(transferred, "transfer"))

        living = transferred.value
        ontology = server_gateway.emit_state_ontology(gateway, transferred)
        core_graph = server_gateway.emit_state_core_graph(gateway, transferred)

        prepared_memory = memory_system.prepare(state)
        if not prepared_memory.ok:
            assert prepared_memory.error is not None
            return cls._failure(prepared_memory.error)
        publication = server_gateway.prepare_publication(living, ontology, core_graph)
        if isinstance(publication, CGSError):
            return cls._failure(publication)

        persisted = memory_system._commit(prepared_memory, _authority=CGS_AUTHORITY)
        if not persisted.ok:
            assert persisted.error is not None
            return cls._failure(persisted.error)
        server_gateway.publish(publication, _authority=CGS_AUTHORITY)
        return ServiceResult(
            status=ServiceStatus.SUCCESS,
            living_graph=living,
            state_ontology=ontology,
            state_core_graph=core_graph,
        )

    @staticmethod
    def _candidate(
        operator: CandidateState | CandidateOperator, graph: Graph
    ) -> CandidateState | CGSError:
        if isinstance(operator, CandidateState):
            return operator
        try:
            candidate = operator.candidate_state(graph)
        except Exception as exc:
            return CGSError(
                ErrorCode.OPERATOR_FAILED,
                f"operator could not supply CandidateState: {exc}",
                "operator",
            )
        if not isinstance(candidate, CandidateState):
            return CGSError(
                ErrorCode.INVALID_CANDIDATE,
                "operator returned a value other than CandidateState",
                "operator",
            )
        return candidate

    @staticmethod
    def _pipeline_error(result: GatewayResult, stage: str) -> CGSError:
        return result.error or CGSError(
            ErrorCode.INVALID_PIPELINE_STAGE,
            f"Gateway {stage} failed without a typed error",
            stage,
        )

    @staticmethod
    def _failure(error: CGSError) -> ServiceResult:
        return ServiceResult(status=ServiceStatus.ERROR, error=error)
