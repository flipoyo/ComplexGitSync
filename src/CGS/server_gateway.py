from __future__ import annotations

from dataclasses import dataclass

from ._authority import require_cgs_authority
from .errors import CGSError, ErrorCode
from .gateway import Gateway, GatewayResult
from .living_graph import LivingGraph
from .state import CandidateState, State
from .state_core_graph import StateCoreGraph
from .state_ontology import StateOntology


@dataclass(frozen=True, slots=True)
class ServerPublication:
    state_ontology: StateOntology
    state_core_graph: StateCoreGraph
    operations: tuple[str, ...]


class ServerGateway:
    """Physical public service wrapper; all pipeline calls cross this boundary."""

    __slots__ = ("name", "_publications")

    def __init__(self, name: str = "@LOCALHOST@G") -> None:
        if not name:
            raise ValueError("ServerGateway.name must be non-empty")
        self.name = name
        self._publications: tuple[ServerPublication, ...] = ()

    @property
    def publications(self) -> tuple[ServerPublication, ...]:
        return self._publications

    def listen(self, gateway: Gateway, candidate: CandidateState) -> GatewayResult:
        return gateway.listen(candidate)

    def interpret(self, gateway: Gateway, listened: GatewayResult) -> GatewayResult:
        return gateway.interpret(listened)

    def validate(self, gateway: Gateway, interpreted: GatewayResult) -> GatewayResult:
        return gateway.validate(interpreted)

    def transfer(
        self,
        gateway: Gateway,
        validated: GatewayResult,
        state: State,
        *,
        _authority: object,
    ) -> GatewayResult:
        return gateway.transfer(validated, state, _authority=_authority)

    def emit_state_ontology(self, gateway: Gateway, transferred: GatewayResult) -> StateOntology:
        return gateway.emit_state_ontology(transferred)

    def emit_state_core_graph(self, gateway: Gateway, transferred: GatewayResult) -> StateCoreGraph:
        return gateway.emit_state_core_graph(transferred)

    def prepare_publication(
        self,
        living_graph: LivingGraph,
        state_ontology: StateOntology,
        state_core_graph: StateCoreGraph,
    ) -> ServerPublication | CGSError:
        if living_graph.state is None or living_graph.state.graph_name != state_ontology.name:
            return CGSError(
                ErrorCode.SERVER_REJECTED,
                "Server accepts only matching validated living projections",
                "server.prepare",
            )
        return ServerPublication(
            state_ontology=state_ontology,
            state_core_graph=state_core_graph,
            operations=("state", "state-core", "validated-operation"),
        )

    def publish(
        self,
        publication: ServerPublication,
        *,
        _authority: object | None = None,
    ) -> CGSError | None:
        require_cgs_authority(_authority)
        self._publications = self._publications + (publication,)
        return None

    def _snapshot(self, *, _authority: object) -> tuple[ServerPublication, ...]:
        require_cgs_authority(_authority)
        return self._publications

    def _restore(self, publications: tuple[ServerPublication, ...], *, _authority: object) -> None:
        require_cgs_authority(_authority)
        self._publications = publications
