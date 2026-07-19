from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from ._authority import CGS_AUTHORITY
from .errors import CGSError, ErrorCode
from .gateway import (
    Gateway,
    GatewayResult,
    GatewayStage,
    ValidatedCandidate,
    _InterpretedState,
)
from .graph import Graph
from .L0 import L0
from .living_graph import LivingGraph
from .memory_system import MemoryRecoveryResult, MemoryResult, MemorySystem
from .server_gateway import ServerGateway, ServerPublication
from .state import CandidateState, State
from .state_core_graph import StateCoreGraph
from .state_id import StateId
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

        memory_snapshot = MemorySystem._snapshot(memory_system, _authority=CGS_AUTHORITY)
        server_snapshot = ServerGateway._snapshot(server_gateway, _authority=CGS_AUTHORITY)

        gateway = Gateway(graph)
        try:
            canonical_listened = Gateway.listen(gateway, candidate)
            physical_listened = server_gateway.listen(gateway, candidate)
            contract_error = cls._gateway_contract_error(
                physical_listened, GatewayStage.LISTENED, CandidateState, "listen"
            )
            if contract_error is not None or physical_listened != canonical_listened:
                return cls._atomic_failure(
                    contract_error or cls._stable_error(ErrorCode.INVALID_PIPELINE_STAGE, "listen"),
                    memory_system,
                    server_gateway,
                    memory_snapshot,
                    server_snapshot,
                )
            if canonical_listened.error is not None:
                return cls._atomic_failure(
                    cls._pipeline_error(canonical_listened, "listen"),
                    memory_system,
                    server_gateway,
                    memory_snapshot,
                    server_snapshot,
                )

            canonical_interpreted = Gateway.interpret(gateway, canonical_listened)
            physical_interpreted = server_gateway.interpret(gateway, canonical_listened)
            contract_error = cls._gateway_contract_error(
                physical_interpreted,
                GatewayStage.INTERPRETED,
                _InterpretedState,
                "interpret",
            )
            if contract_error is not None or physical_interpreted != canonical_interpreted:
                return cls._atomic_failure(
                    contract_error
                    or cls._stable_error(ErrorCode.INVALID_PIPELINE_STAGE, "interpret"),
                    memory_system,
                    server_gateway,
                    memory_snapshot,
                    server_snapshot,
                )
            if canonical_interpreted.error is not None:
                return cls._atomic_failure(
                    cls._pipeline_error(canonical_interpreted, "interpret"),
                    memory_system,
                    server_gateway,
                    memory_snapshot,
                    server_snapshot,
                )

            canonical_validated = Gateway.validate(gateway, canonical_interpreted)
            physical_validated = server_gateway.validate(gateway, canonical_interpreted)
            contract_error = cls._gateway_contract_error(
                physical_validated,
                GatewayStage.VALIDATED,
                ValidatedCandidate,
                "validate",
            )
            if contract_error is not None or physical_validated != canonical_validated:
                return cls._atomic_failure(
                    contract_error
                    or cls._stable_error(ErrorCode.INVALID_PIPELINE_STAGE, "validate"),
                    memory_system,
                    server_gateway,
                    memory_snapshot,
                    server_snapshot,
                )
            if canonical_validated.error is not None:
                return cls._atomic_failure(
                    cls._pipeline_error(canonical_validated, "validate"),
                    memory_system,
                    server_gateway,
                    memory_snapshot,
                    server_snapshot,
                )
        except Exception:
            return cls._atomic_failure(
                cls._stable_error(ErrorCode.SERVER_REJECTED, "gateway.pipeline"),
                memory_system,
                server_gateway,
                memory_snapshot,
                server_snapshot,
            )

        try:
            l0 = L0(_authority=CGS_AUTHORITY)
            _private_anchor, state_id = l0._anchor(_authority=CGS_AUTHORITY)
            state = State(
                graph_name=graph.name,
                state_id=state_id,
                left=candidate.left,
                right=candidate.right,
                payload=candidate.payload,
                validated=True,
                _authority=CGS_AUTHORITY,
            )
            canonical_transferred = Gateway.transfer(
                gateway, canonical_validated, state, _authority=CGS_AUTHORITY
            )
            physical_transferred = server_gateway.transfer(
                gateway, canonical_validated, state, _authority=CGS_AUTHORITY
            )
            contract_error = cls._gateway_contract_error(
                physical_transferred,
                GatewayStage.TRANSFERRED,
                LivingGraph,
                "transfer",
            )
            if contract_error is not None or physical_transferred != canonical_transferred:
                return cls._atomic_failure(
                    contract_error
                    or cls._stable_error(ErrorCode.INVALID_PIPELINE_STAGE, "transfer"),
                    memory_system,
                    server_gateway,
                    memory_snapshot,
                    server_snapshot,
                )
            if canonical_transferred.error is not None:
                return cls._atomic_failure(
                    cls._pipeline_error(canonical_transferred, "transfer"),
                    memory_system,
                    server_gateway,
                    memory_snapshot,
                    server_snapshot,
                )
        except Exception:
            return cls._atomic_failure(
                cls._stable_error(ErrorCode.INVALID_AUTHORITATIVE_STATE, "transfer"),
                memory_system,
                server_gateway,
                memory_snapshot,
                server_snapshot,
            )

        living = canonical_transferred.value
        if (
            type(living.graph) is not Graph
            or living.graph != graph
            or living.gateway is not gateway
            or type(living.state) is not State
            or living.state != state
            or living.left != state.left
            or living.right != state.right
        ):
            return cls._atomic_failure(
                cls._stable_error(ErrorCode.INVALID_PIPELINE_STAGE, "transfer"),
                memory_system,
                server_gateway,
                memory_snapshot,
                server_snapshot,
            )

        try:
            canonical_ontology = Gateway.emit_state_ontology(gateway, canonical_transferred)
            physical_ontology = server_gateway.emit_state_ontology(gateway, canonical_transferred)
            if (
                type(physical_ontology) is not StateOntology
                or physical_ontology != canonical_ontology
            ):
                return cls._atomic_failure(
                    cls._stable_error(ErrorCode.EMISSION_REJECTED, "emit.state_ontology"),
                    memory_system,
                    server_gateway,
                    memory_snapshot,
                    server_snapshot,
                )
            canonical_core_graph = Gateway.emit_state_core_graph(gateway, canonical_transferred)
            physical_core_graph = server_gateway.emit_state_core_graph(
                gateway, canonical_transferred
            )
            if (
                type(physical_core_graph) is not StateCoreGraph
                or physical_core_graph != canonical_core_graph
            ):
                return cls._atomic_failure(
                    cls._stable_error(ErrorCode.EMISSION_REJECTED, "emit.state_core_graph"),
                    memory_system,
                    server_gateway,
                    memory_snapshot,
                    server_snapshot,
                )
        except Exception:
            return cls._atomic_failure(
                cls._stable_error(ErrorCode.EMISSION_REJECTED, "emit"),
                memory_system,
                server_gateway,
                memory_snapshot,
                server_snapshot,
            )

        ontology = canonical_ontology
        core_graph = canonical_core_graph

        try:
            canonical_memory = MemorySystem.prepare(memory_system, state)
            physical_memory = memory_system.prepare(state)
            if (
                type(physical_memory) is not MemoryResult
                or physical_memory != canonical_memory
                or not canonical_memory.ok
                or memory_system.records != memory_snapshot
            ):
                return cls._atomic_failure(
                    cls._stable_error(ErrorCode.MEMORY_REJECTED, "memory.prepare"),
                    memory_system,
                    server_gateway,
                    memory_snapshot,
                    server_snapshot,
                )
            canonical_publication = ServerGateway.prepare_publication(
                server_gateway, living, ontology, core_graph
            )
            physical_publication = server_gateway.prepare_publication(living, ontology, core_graph)
            if (
                type(canonical_publication) is not ServerPublication
                or type(physical_publication) is not ServerPublication
                or physical_publication != canonical_publication
                or server_gateway.publications != server_snapshot
            ):
                return cls._atomic_failure(
                    cls._stable_error(ErrorCode.SERVER_REJECTED, "server.prepare"),
                    memory_system,
                    server_gateway,
                    memory_snapshot,
                    server_snapshot,
                )
        except Exception:
            return cls._atomic_failure(
                cls._stable_error(ErrorCode.SERVICE_COMMIT_FAILED, "prepare"),
                memory_system,
                server_gateway,
                memory_snapshot,
                server_snapshot,
            )

        expected_record = canonical_memory.record
        expected_records = (
            memory_snapshot
            if expected_record in memory_snapshot
            else memory_snapshot + (expected_record,)
        )
        expected_publications = server_snapshot + (canonical_publication,)
        try:
            persisted = memory_system.persist(state, _authority=CGS_AUTHORITY)
            if (
                type(persisted) is not MemoryResult
                or persisted != canonical_memory
                or memory_system.records != expected_records
            ):
                return cls._atomic_failure(
                    cls._stable_error(ErrorCode.SERVICE_COMMIT_FAILED, "memory.persist"),
                    memory_system,
                    server_gateway,
                    memory_snapshot,
                    server_snapshot,
                )
            publication_failure = server_gateway.publish(
                canonical_publication, _authority=CGS_AUTHORITY
            )
            if (
                publication_failure is not None
                or server_gateway.publications != expected_publications
            ):
                return cls._atomic_failure(
                    cls._stable_error(ErrorCode.SERVICE_COMMIT_FAILED, "server.publish"),
                    memory_system,
                    server_gateway,
                    memory_snapshot,
                    server_snapshot,
                )
        except Exception:
            return cls._atomic_failure(
                cls._stable_error(ErrorCode.SERVICE_COMMIT_FAILED, "commit"),
                memory_system,
                server_gateway,
                memory_snapshot,
                server_snapshot,
            )

        return ServiceResult(
            status=ServiceStatus.SUCCESS,
            living_graph=living,
            state_ontology=ontology,
            state_core_graph=core_graph,
        )

    @classmethod
    def recover(cls, memory_system: MemorySystem, state_id: StateId | str) -> MemoryRecoveryResult:
        if not isinstance(memory_system, MemorySystem):
            return MemoryRecoveryResult(
                error=cls._stable_error(ErrorCode.MEMORY_REJECTED, "memory.recover")
            )
        try:
            recovered = memory_system.recover(state_id, _authority=CGS_AUTHORITY)
        except Exception:
            return MemoryRecoveryResult(
                error=cls._stable_error(ErrorCode.MEMORY_REJECTED, "memory.recover")
            )
        if not isinstance(recovered, MemoryRecoveryResult) or not recovered.ok:
            code = (
                recovered.error.code
                if isinstance(recovered, MemoryRecoveryResult) and recovered.error
                else ErrorCode.MEMORY_REJECTED
            )
            return MemoryRecoveryResult(error=cls._stable_error(code, "memory.recover"))
        return recovered

    @staticmethod
    def _candidate(
        operator: CandidateState | CandidateOperator, graph: Graph
    ) -> CandidateState | CGSError:
        if isinstance(operator, CandidateState):
            return operator
        try:
            candidate = operator.candidate_state(graph)
        except Exception:
            return CGS._stable_error(ErrorCode.OPERATOR_FAILED, "operator")
        if not isinstance(candidate, CandidateState):
            return CGS._stable_error(ErrorCode.INVALID_CANDIDATE, "operator")
        return candidate

    @staticmethod
    def _pipeline_error(result: GatewayResult, stage: str) -> CGSError:
        code = result.error.code if result.error is not None else ErrorCode.INVALID_PIPELINE_STAGE
        return CGS._stable_error(code, stage)

    @staticmethod
    def _gateway_contract_error(
        result: object,
        expected_stage: GatewayStage,
        expected_value_type: type[object],
        stage: str,
    ) -> CGSError | None:
        if type(result) is not GatewayResult:
            return CGS._stable_error(ErrorCode.INVALID_PIPELINE_STAGE, stage)
        if result.error is not None:
            if (
                not isinstance(result.error, CGSError)
                or result.stage is not None
                or result.value is not None
            ):
                return CGS._stable_error(ErrorCode.INVALID_PIPELINE_STAGE, stage)
            return None
        if result.stage != expected_stage or type(result.value) is not expected_value_type:
            return CGS._stable_error(ErrorCode.INVALID_PIPELINE_STAGE, stage)
        return None

    @staticmethod
    def _stable_error(code: ErrorCode, stage: str) -> CGSError:
        return CGSError(code, "CGS rejected the operation", stage)

    @staticmethod
    def _rollback(
        memory_system: MemorySystem,
        server_gateway: ServerGateway,
        memory_snapshot: tuple[object, ...],
        server_snapshot: tuple[object, ...],
    ) -> None:
        MemorySystem._restore(
            memory_system,
            memory_snapshot,
            _authority=CGS_AUTHORITY,  # type: ignore[arg-type]
        )
        ServerGateway._restore(
            server_gateway,
            server_snapshot,
            _authority=CGS_AUTHORITY,  # type: ignore[arg-type]
        )

    @classmethod
    def _atomic_failure(
        cls,
        error: CGSError,
        memory_system: MemorySystem,
        server_gateway: ServerGateway,
        memory_snapshot: tuple[object, ...],
        server_snapshot: tuple[object, ...],
    ) -> ServiceResult:
        cls._rollback(memory_system, server_gateway, memory_snapshot, server_snapshot)
        return cls._failure(error)

    @staticmethod
    def _failure(error: CGSError) -> ServiceResult:
        return ServiceResult(status=ServiceStatus.ERROR, error=error)
