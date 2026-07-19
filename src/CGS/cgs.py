from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from .errors import CGSError, ErrorCode
from .gateway import (
    Gateway,
    GatewayResult,
    GatewayStage,
    ValidatedCandidate,
    _InterpretedState,
)
from .graph import Graph
from .L0 import _anchor_occurrence, _new_l0
from .living_graph import LivingGraph
from .memory_system import (
    MemoryRecoveryResult,
    MemoryResult,
    MemorySystem,
    _record_for_state,
    _verify_commit,
)
from .server_gateway import (
    ServerGateway,
    ServerPublication,
    _canonical_publication,
    _publication_blob,
)
from .state import CandidateState, State, _new_state
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
    """Canonical @CGS facade and sole authoritative construction path."""

    @classmethod
    def serve(
        cls,
        graph_name: str,
        graph: Graph,
        memory_system: MemorySystem,
        operator: CandidateState | CandidateOperator,
        server_gateway: ServerGateway,
    ) -> ServiceResult:
        if type(graph) is not Graph:
            return cls._failure(cls._stable_error(ErrorCode.INVALID_GRAPH, "serve"))
        if type(memory_system) is not MemorySystem:
            return cls._failure(cls._stable_error(ErrorCode.MEMORY_REJECTED, "serve"))
        if type(server_gateway) is not ServerGateway:
            return cls._failure(cls._stable_error(ErrorCode.SERVER_REJECTED, "serve"))
        try:
            canonical_graph = graph.detached_copy()
        except Exception:
            return cls._failure(cls._stable_error(ErrorCode.INVALID_GRAPH, "serve"))
        if graph_name != canonical_graph.name:
            return cls._failure(cls._stable_error(ErrorCode.GRAPH_NAME_MISMATCH, "serve"))

        candidate = cls._candidate(operator, canonical_graph.detached_copy())
        if isinstance(candidate, CGSError):
            return cls._failure(candidate)

        memory_snapshot = memory_system._journal_snapshot()
        server_snapshot = server_gateway._journal_snapshot()
        gateway = Gateway(canonical_graph)

        try:
            canonical_listened = Gateway.listen(gateway, candidate)
            physical_listened = server_gateway.listen(gateway, candidate.detached_copy())
            failure = cls._authenticate_gateway_result(
                canonical_listened,
                physical_listened,
                GatewayStage.LISTENED,
                CandidateState,
                "listen",
            )
            if failure:
                return cls._atomic_failure(
                    failure, memory_system, server_gateway, memory_snapshot, server_snapshot
                )

            canonical_interpreted = Gateway.interpret(gateway, canonical_listened)
            physical_interpreted = server_gateway.interpret(gateway, canonical_listened)
            failure = cls._authenticate_gateway_result(
                canonical_interpreted,
                physical_interpreted,
                GatewayStage.INTERPRETED,
                _InterpretedState,
                "interpret",
            )
            if failure:
                return cls._atomic_failure(
                    failure, memory_system, server_gateway, memory_snapshot, server_snapshot
                )

            canonical_validated = Gateway.validate(gateway, canonical_interpreted)
            physical_validated = server_gateway.validate(gateway, canonical_interpreted)
            failure = cls._authenticate_gateway_result(
                canonical_validated,
                physical_validated,
                GatewayStage.VALIDATED,
                ValidatedCandidate,
                "validate",
            )
            if failure:
                return cls._atomic_failure(
                    failure, memory_system, server_gateway, memory_snapshot, server_snapshot
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
            l0 = _new_l0()
            _private_anchor, state_id = _anchor_occurrence(l0)
            state = _new_state(
                graph_name=canonical_graph.name,
                graph_binding=canonical_graph.digest,
                gateway_binding=gateway.binding,
                state_id=state_id,
                left=candidate.left,
                right=candidate.right,
                payload=candidate.payload,
            )
            canonical_transferred = Gateway.transfer(gateway, canonical_validated, state)
            physical_transferred = server_gateway.transfer(gateway, canonical_validated, state)
            failure = cls._authenticate_gateway_result(
                canonical_transferred,
                physical_transferred,
                GatewayStage.TRANSFERRED,
                LivingGraph,
                "transfer",
            )
            if failure:
                return cls._atomic_failure(
                    failure, memory_system, server_gateway, memory_snapshot, server_snapshot
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
            type(living) is not LivingGraph
            or living.graph != canonical_graph
            or living.gateway != gateway
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
                raise ValueError("invalid physical ontology")
            canonical_core = Gateway.emit_state_core_graph(gateway, canonical_transferred)
            physical_core = server_gateway.emit_state_core_graph(gateway, canonical_transferred)
            if type(physical_core) is not StateCoreGraph or physical_core != canonical_core:
                raise ValueError("invalid physical core projection")
        except Exception:
            return cls._atomic_failure(
                cls._stable_error(ErrorCode.EMISSION_REJECTED, "emit"),
                memory_system,
                server_gateway,
                memory_snapshot,
                server_snapshot,
            )

        try:
            canonical_memory = _record_for_state(state)
            physical_memory = memory_system.prepare(state)
            if (
                type(physical_memory) is not MemoryResult
                or physical_memory != canonical_memory
                or not canonical_memory.ok
                or memory_system._journal_snapshot() != memory_snapshot
            ):
                raise ValueError("invalid physical Memory preparation")
            canonical_publication = _canonical_publication(
                living, canonical_ontology, canonical_core
            )
            physical_publication = server_gateway.prepare_publication(
                living, canonical_ontology, canonical_core
            )
            if (
                type(canonical_publication) is not ServerPublication
                or type(physical_publication) is not ServerPublication
                or physical_publication != canonical_publication
                or server_gateway._journal_snapshot() != server_snapshot
            ):
                raise ValueError("invalid physical publication preparation")
        except Exception:
            return cls._atomic_failure(
                cls._stable_error(ErrorCode.SERVICE_COMMIT_FAILED, "prepare"),
                memory_system,
                server_gateway,
                memory_snapshot,
                server_snapshot,
            )

        assert canonical_memory.record is not None
        try:
            persisted = memory_system.persist(state)
            if (
                type(persisted) is not MemoryResult
                or persisted != canonical_memory
                or not _verify_commit(memory_system, memory_snapshot, canonical_memory.record)
            ):
                raise ValueError("invalid physical Memory commit")
            publication_failure = server_gateway.publish(canonical_publication)
            expected_server = server_snapshot + (_publication_blob(canonical_publication),)
            if (
                publication_failure is not None
                or server_gateway._journal_snapshot() != expected_server
            ):
                raise ValueError("invalid physical publication commit")
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
            state_ontology=canonical_ontology,
            state_core_graph=canonical_core,
        )

    @classmethod
    def recover(cls, memory_system: MemorySystem, state_id: StateId | str) -> MemoryRecoveryResult:
        if type(memory_system) is not MemorySystem:
            return MemoryRecoveryResult(
                error=cls._stable_error(ErrorCode.MEMORY_REJECTED, "memory.recover")
            )
        snapshot = memory_system._journal_snapshot()
        try:
            recovered = memory_system.recover(state_id)
        except Exception:
            memory_system._restore_journal(snapshot)
            return MemoryRecoveryResult(
                error=cls._stable_error(ErrorCode.MEMORY_REJECTED, "memory.recover")
            )
        if memory_system._journal_snapshot() != snapshot:
            memory_system._restore_journal(snapshot)
            return MemoryRecoveryResult(
                error=cls._stable_error(ErrorCode.MEMORY_CORRUPT, "memory.recover")
            )
        if type(recovered) is not MemoryRecoveryResult or not recovered.ok:
            code = (
                recovered.error.code
                if type(recovered) is MemoryRecoveryResult and recovered.error
                else ErrorCode.MEMORY_REJECTED
            )
            return MemoryRecoveryResult(error=cls._stable_error(code, "memory.recover"))
        assert recovered.state is not None and recovered.record is not None
        canonical = _record_for_state(recovered.state)
        requested = state_id.value if type(state_id) is StateId else state_id
        if (
            not canonical.ok
            or canonical.record != recovered.record
            or recovered.state.state_id.value != requested
        ):
            return MemoryRecoveryResult(
                error=cls._stable_error(ErrorCode.MEMORY_CORRUPT, "memory.recover")
            )
        return recovered

    @staticmethod
    def _candidate(
        operator: CandidateState | CandidateOperator, graph: Graph
    ) -> CandidateState | CGSError:
        try:
            candidate = (
                operator.detached_copy()
                if type(operator) is CandidateState
                else operator.candidate_state(graph.detached_copy())
            )
            if type(candidate) is not CandidateState:
                return CGS._stable_error(ErrorCode.INVALID_CANDIDATE, "operator")
            return candidate.detached_copy()
        except Exception:
            return CGS._stable_error(ErrorCode.OPERATOR_FAILED, "operator")

    @staticmethod
    def _authenticate_gateway_result(
        canonical: GatewayResult,
        physical: object,
        expected_stage: GatewayStage,
        expected_value_type: type[object],
        stage: str,
    ) -> CGSError | None:
        if type(physical) is not GatewayResult:
            return CGS._stable_error(ErrorCode.INVALID_PIPELINE_STAGE, stage)
        if physical.error is not None:
            if (
                type(physical.error) is not CGSError
                or physical.stage is not None
                or physical.value is not None
            ):
                return CGS._stable_error(ErrorCode.INVALID_PIPELINE_STAGE, stage)
        elif physical.stage != expected_stage or type(physical.value) is not expected_value_type:
            return CGS._stable_error(ErrorCode.INVALID_PIPELINE_STAGE, stage)
        if physical != canonical:
            return (
                CGS._pipeline_error(physical, stage)
                if physical.error is not None
                else CGS._stable_error(ErrorCode.INVALID_PIPELINE_STAGE, stage)
            )
        return None

    @staticmethod
    def _pipeline_error(result: GatewayResult, stage: str) -> CGSError:
        code = result.error.code if result.error is not None else ErrorCode.INVALID_PIPELINE_STAGE
        return CGS._stable_error(code, stage)

    @staticmethod
    def _stable_error(code: ErrorCode, stage: str) -> CGSError:
        return CGSError(code, "CGS rejected the operation", stage)

    @classmethod
    def _atomic_failure(
        cls,
        error: CGSError,
        memory_system: MemorySystem,
        server_gateway: ServerGateway,
        memory_snapshot: object,
        server_snapshot: tuple[str, ...],
    ) -> ServiceResult:
        memory_system._restore_journal(memory_snapshot)  # type: ignore[arg-type]
        server_gateway._restore_journal(server_snapshot)
        if (
            memory_system._journal_snapshot() != memory_snapshot
            or server_gateway._journal_snapshot() != server_snapshot
        ):
            return cls._failure(cls._stable_error(ErrorCode.SERVICE_COMMIT_FAILED, "rollback"))
        return cls._failure(error)

    @staticmethod
    def _failure(error: CGSError) -> ServiceResult:
        return ServiceResult(status=ServiceStatus.ERROR, error=error)
